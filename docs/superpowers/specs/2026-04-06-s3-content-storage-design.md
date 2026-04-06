# S3 Content Storage — Design Spec

**Date:** 2026-04-06
**Status:** Draft
**Goal:** Move all HTML content (raw HTML + extracted text) out of MySQL and into S3, so the database stays lean as the system scales to 50K+ researcher URLs.

## Problem

`html_content` stores raw HTML (uncompressed MEDIUMTEXT) and extracted text in MySQL. At 1,245 URLs this table is already 137MB — 77% of all database storage. At 50K URLs it would reach 100-250GB, far exceeding what a 2GB Lightsail instance can handle.

The API never serves HTML content to the frontend. It only queries `papers`, `feed_events`, `researchers`, and related tables. HTML content is only accessed during the background scrape pipeline.

## Design

### Two-tier storage

| Tier | Stores | Access pattern |
|------|--------|----------------|
| **MySQL** | Hashes, timestamps, S3 keys, all researcher/paper data | API reads (fast), scrape metadata |
| **S3** | Raw HTML (compressed), extracted text, archived snapshots | Scrape pipeline reads/writes (latency acceptable) |

### S3 key structure

```
s3://{bucket}/
  html/{url_id}/current.txt              # extracted text (for diff computation)
  html/{url_id}/current.html.zlib        # raw HTML, zlib-compressed
  html/{url_id}/snapshots/{content_hash}.html.zlib   # archived versions
```

Keys are predictable from `url_id` and `content_hash` — no need to store full S3 paths in the DB, just derive them.

### MySQL schema changes

`html_content` table shrinks to metadata only:

```sql
-- Columns KEPT:
url_id, content_hash, timestamp, researcher_id, extracted_at, extracted_hash

-- Columns DROPPED (after migration):
content       -- moved to S3 html/{url_id}/current.txt
raw_html      -- moved to S3 html/{url_id}/current.html.zlib
```

`html_snapshots` table:

```sql
-- Columns KEPT:
url_id, text_content_hash, snapshot_at

-- Columns DROPPED (after migration):
raw_html_compressed  -- moved to S3 html/{url_id}/snapshots/{content_hash}.html.zlib
raw_html_hash        -- derivable from S3 object, no longer needed in DB
```

### ContentStore class

New module `content_store.py` that wraps S3 operations. All HTML storage/retrieval goes through this class.

```python
class ContentStore:
    def __init__(self, bucket: str, region: str):
        self.s3 = boto3.client("s3", region_name=region)
        self.bucket = bucket

    def put_text(self, url_id: int, text: str) -> None
    def get_text(self, url_id: int) -> str | None
    def put_html(self, url_id: int, raw_html: str) -> None
    def get_html(self, url_id: int) -> str | None
    def archive_snapshot(self, url_id: int, content_hash: str) -> None
    def get_snapshot(self, url_id: int, content_hash: str) -> str | None
```

- `put_html` / `get_html` handle zlib compression/decompression internally.
- `archive_snapshot` copies current S3 objects to the snapshots prefix before overwriting.
- All methods use the predictable key scheme — no DB lookups needed for S3 paths.

### Pipeline changes

The scrape flow in `scheduler.py` stays structurally identical. What changes is where data is read/written:

| Operation | Before | After |
|-----------|--------|-------|
| `get_previous_text(url_id)` | `SELECT content FROM html_content` | `ContentStore.get_text(url_id)` |
| `get_latest_text(url_id)` | `SELECT content FROM html_content` | `ContentStore.get_text(url_id)` |
| `save_text(url_id, ...)` | `INSERT/UPDATE html_content` | `ContentStore.put_text()` + `ContentStore.put_html()` + MySQL metadata update |
| `get_raw_html(url_id)` | `SELECT raw_html FROM html_content` | `ContentStore.get_html(url_id)` |
| `archive_snapshot(url_id)` | Compress + insert into `html_snapshots` | `ContentStore.archive_snapshot()` + MySQL metadata insert |
| `has_text_changed(url_id, hash)` | `SELECT content_hash FROM html_content` | Same (stays in MySQL) |
| `_was_fetched_recently(url_id)` | `SELECT timestamp FROM html_content` | Same (stays in MySQL) |

The API is completely unaffected — it never touches these tables.

### What stays in MySQL

- `html_content`: `url_id`, `content_hash`, `timestamp`, `researcher_id`, `extracted_at`, `extracted_hash`
- `html_snapshots`: `url_id`, `text_content_hash`, `snapshot_at`
- All other tables unchanged: `papers`, `feed_events`, `researchers`, `authorship`, `paper_links`, etc.

Estimated MySQL size at 50K URLs: **<1GB** (down from 100-250GB).

## Infrastructure

### S3 setup

- Bucket: `econ-newsfeed` in `eu-central-1` (same region as Lightsail)
- IAM user with scoped policy: `s3:GetObject`, `s3:PutObject`, `s3:CopyObject` on the bucket only
- No public access
- No lifecycle rules initially (S3 Standard is $0.023/GB/mo — 250GB = ~$6/mo)

### New env vars

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=econ-newsfeed
AWS_REGION=eu-central-1
```

Added to `docker-compose.yml`, `.env.example`, and production `.env`.

### New dependency

`boto3` added to `pyproject.toml`.

### Local development

`content_store.py` supports a `S3_ENDPOINT_URL` env var for pointing at LocalStack or MinIO. Alternatively, a filesystem-backed implementation (`FileContentStore`) can be used when `AWS_S3_BUCKET` is not set, storing files under a local directory (e.g., `.content-store/`).

### Docker

Add `AWS_*` env vars as passthrough in `docker-compose.yml`. Add `!content_store.py` to `.dockerignore`. No new containers needed.

## Migration

Three-phase rollout to avoid data loss:

### Phase 1: Dual-write

- Deploy `ContentStore` alongside existing MySQL storage
- On every save: write to both MySQL columns and S3
- On every read: read from MySQL (S3 is backup)
- Validates S3 writes are working correctly

### Phase 2: Backfill + switch reads

- Backfill script: iterate all `html_content` rows, upload `content` and `raw_html` to S3
- Backfill script: iterate all `html_snapshots` rows, upload `raw_html_compressed` to S3
- Switch reads to S3 (MySQL columns still populated but no longer read)
- Verify pipeline works end-to-end with S3 reads

### Phase 3: Drop columns

- Stop writing to MySQL `content` and `raw_html` columns
- Run migration: `ALTER TABLE html_content DROP COLUMN content, DROP COLUMN raw_html`
- Run migration: `ALTER TABLE html_snapshots DROP COLUMN raw_html_compressed, DROP COLUMN raw_html_hash`
- Reclaim space with `OPTIMIZE TABLE`

## Testing

- Unit tests for `ContentStore` using mocked S3 (moto library or similar)
- Integration test: full scrape cycle with LocalStack S3
- Backfill script should be idempotent (safe to re-run)
- Verify `_url_has_baseline` still works (only needs snapshot count from MySQL)

## Cost estimate at 50K URLs

| Component | Cost/mo |
|-----------|---------|
| Lightsail (2GB) | $12 |
| S3 storage (250GB) | ~$6 |
| S3 requests (PUT/GET during scrapes) | ~$1 |
| **Total** | **~$19** |
