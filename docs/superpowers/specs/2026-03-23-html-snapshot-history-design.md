# HTML Snapshot History via Compressed Full Snapshots

## Problem

The `html_content` table stores exactly one row per URL, overwriting the previous version on each scrape. Historical snapshots are lost — there's no way to audit when a page changed, what it looked like before, or debug extraction issues against prior versions.

## Goal

Keep all historical snapshots of researcher pages for auditability, while minimizing storage via compression. Only material changes (text content changes, not cosmetic HTML changes) should trigger a new snapshot.

## Constraints

- Reconstruction latency: seconds are acceptable (debugging use case, not user-facing)
- Content stored: `raw_html` snapshots (text can be re-derived)
- Archive trigger: only when extracted text changes (existing `content_hash` gate via `has_text_changed()`)
- Change frequency: most pages change rarely (monthly or less) — few snapshots per URL
- Hot path impact: zero — the pipeline must continue reading the latest snapshot from `html_content` with no reconstruction overhead

## Design

### Approach: Separate history table with compressed full snapshots

Keep `html_content` unchanged (latest snapshot, one row per URL). Add a new `html_snapshots` table that stores full `raw_html` snapshots compressed with `zlib`. When new content arrives and text has changed, compress and archive the *old* `raw_html` into `html_snapshots`, then overwrite `html_content` as today.

Each snapshot is independently readable — no diff chains, no reconstruction logic, no corruption propagation risk. Storage is kept in check via zlib compression (typical HTML compresses to 20-30% of original size). Given pages change rarely (monthly or less), the snapshot count per URL stays small and the marginal cost of full snapshots over diffs is negligible.

### Schema

```sql
CREATE TABLE IF NOT EXISTS html_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    url_id INT NOT NULL,
    text_content_hash VARCHAR(64) NOT NULL,
    raw_html_hash VARCHAR(64) NOT NULL,
    raw_html_compressed MEDIUMBLOB NOT NULL,
    snapshot_at DATETIME NOT NULL,
    UNIQUE KEY uq_url_snapshot (url_id, text_content_hash),
    FOREIGN KEY (url_id) REFERENCES researcher_urls(id) ON DELETE CASCADE,
    INDEX idx_url_id_snapshot (url_id, snapshot_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```

- `text_content_hash` — SHA-256 of the extracted text content for the archived version. Matches `html_content.content_hash` at the time of archiving. Used for identification (linking a snapshot to the text version that triggered it).
- `raw_html_hash` — SHA-256 of the raw HTML being archived. Used for integrity verification — after decompressing, recompute and compare.
- `raw_html_compressed` — `zlib.compress(raw_html.encode('utf-8'))`. Full raw HTML, compressed.
- `snapshot_at` — when this version was originally saved (copied from `html_content.timestamp` at archive time)
- `UNIQUE KEY (url_id, text_content_hash)` — prevents duplicate snapshots for the same text version of a URL (guards against crash-recovery edge cases)

No changes to the `html_content` table schema.

### Archive Flow

When `HTMLFetcher.save_text()` is called (text content has changed):

1. Read current `raw_html`, `content_hash`, and `timestamp` from `html_content` for this `url_id`
2. If a current row exists **and `raw_html` is not NULL**:
   - Compress old `raw_html` with `zlib.compress()`
   - Compute `raw_html_hash` as `SHA-256(old_raw_html)`
   - INSERT into `html_snapshots` (using `INSERT IGNORE` to handle the unique constraint gracefully if a duplicate exists)
3. Upsert `html_content` as today — no change to existing logic

Steps 2 and 3 are wrapped in a single database transaction to ensure atomicity.

**Edge cases:**
- First-ever fetch for a URL: no prior row exists, no snapshot created. History begins on the second text-changing fetch.
- Legacy rows with `raw_html = NULL` (from before the `raw_html` column was added): skip archiving for that transition, log a warning. The next fetch will populate `raw_html`, and subsequent changes will be archived normally.
- If archiving fails (DB error): log a warning but do not block the save. The pipeline's primary job — tracking latest content — must not be disrupted by history tracking failures.

**Batch API path:** The batch pipeline (`make batch-submit` / `make batch-check`) processes already-fetched HTML for publication extraction — it does not fetch new HTML. All HTML fetching goes through `HTMLFetcher.fetch_and_save_if_changed()` → `save_text()`, so archiving is fully captured.

### Retrieval

`HTMLFetcher.get_snapshot(url_id, snapshot_id)`:

1. Fetch the row from `html_snapshots`
2. Decompress with `zlib.decompress()`
3. Verify integrity: compute SHA-256 of decompressed HTML, compare against stored `raw_html_hash`
4. Return the raw HTML string

`HTMLFetcher.list_snapshots(url_id)` — returns a list of `(id, text_content_hash, raw_html_hash, snapshot_at)` for a URL, ordered by `snapshot_at DESC`.

These are utility methods for debugging/investigation — not called by the pipeline. No API endpoint initially.

## Migration

- Add `html_snapshots` to `_TABLE_DEFINITIONS` dict in `database/schema.py` (created on `make seed`)
- Add `"html_snapshots"` to the `_ALL_TABLES` list inside `create_tables()` (used for charset migration)
- No data migration — history starts accumulating from the next scrape

## Files Changed

- `html_fetcher.py` — add `archive_snapshot()` (called inside `save_text()`), `get_snapshot()`, `list_snapshots()`
- `database/schema.py` — add table definition to `_TABLE_DEFINITIONS`, add to `_ALL_TABLES`
- `tests/test_html_fetcher.py` — new test cases

No changes to: `publication.py`, `link_extractor.py`, `scheduler.py`, `api.py`, frontend, or any existing `html_content` queries.

## Testing

- `test_archive_snapshot_on_text_change` — verify snapshot row inserted with compressed HTML when `save_text()` is called with a prior row existing
- `test_no_archive_on_first_fetch` — verify no snapshot created when no prior row exists
- `test_no_archive_when_raw_html_null` — verify no snapshot created when old `raw_html` is NULL, with warning logged
- `test_archive_failure_doesnt_block_save` — simulate DB error in archive, verify `save_text()` still succeeds
- `test_duplicate_archive_ignored` — verify INSERT IGNORE handles duplicate `(url_id, text_content_hash)` gracefully
- `test_get_snapshot_decompresses_and_verifies` — compress known HTML, store, retrieve, verify integrity check passes
- `test_get_snapshot_integrity_failure` — corrupt the blob, verify integrity check detects it
- `test_list_snapshots` — verify correct ordering and content
