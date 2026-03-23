# HTML Snapshot History via Reverse Diffs

## Problem

The `html_content` table stores exactly one row per URL, overwriting the previous version on each scrape. Historical snapshots are lost — there's no way to audit when a page changed, what it looked like before, or debug extraction issues against prior versions.

## Goal

Keep all historical snapshots of researcher pages for auditability, while minimizing storage by storing diffs between versions rather than full copies. Only material changes (text content changes, not cosmetic HTML changes) should trigger a new snapshot.

## Constraints

- Reconstruction latency: seconds are acceptable (debugging use case, not user-facing)
- Content stored: `raw_html` snapshots (text can be re-derived)
- Archive trigger: only when extracted text changes (existing `content_hash` gate via `has_text_changed()`)
- Change frequency: most pages change rarely (monthly or less) — short diff chains per URL
- Hot path impact: zero — the pipeline must continue reading the latest snapshot from `html_content` with no reconstruction overhead

## Design

### Approach: Separate history table with reverse diffs

Keep `html_content` unchanged (latest snapshot, one row per URL). Add a new `html_snapshots` table that stores reverse diffs. When new content arrives and text has changed, compute a diff from new→old `raw_html`, compress it, store it in `html_snapshots`, then overwrite `html_content` as today.

Reverse diffs mean the most recent version (most commonly needed) is always available in full from `html_content`. Older versions are reconstructed by applying diffs backward from the current version.

### Schema

```sql
CREATE TABLE IF NOT EXISTS html_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    url_id INT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    diff MEDIUMBLOB,
    snapshot_at DATETIME NOT NULL,
    FOREIGN KEY (url_id) REFERENCES researcher_urls(id) ON DELETE CASCADE,
    INDEX idx_url_id_snapshot (url_id, snapshot_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```

- `content_hash` — SHA-256 of the extracted text content for the version being archived (the old version)
- `diff` — zlib-compressed unified diff from new `raw_html` to old `raw_html` (reverse diff). For the very first snapshot of a URL (no prior version to diff against), this stores the full `raw_html` zlib-compressed as a base.
- `snapshot_at` — timestamp of when this version was originally saved (copied from `html_content.timestamp`)

No changes to the `html_content` table schema.

### Archive Flow

When `HTMLFetcher.save_text()` is called (text content has changed):

1. Read current `raw_html` and `timestamp` from `html_content` for this `url_id`
2. If a current row exists (not the first-ever fetch):
   - Compute unified diff from new `raw_html` → old `raw_html` (reverse diff) using `difflib.unified_diff()`
   - Compress with `zlib.compress()`
   - Insert into `html_snapshots` with the old `content_hash` and `timestamp`
3. Upsert `html_content` as today — no change to existing logic

On the first-ever fetch for a URL, no snapshot row is created (there's no prior version). The first snapshot row appears on the second fetch when content changes.

If archiving fails (DB error, diff error), log a warning but do not block the save. The pipeline's primary job — tracking latest content — must not be disrupted by history tracking failures.

### Reconstruction

`HTMLFetcher.reconstruct_snapshot(url_id, snapshot_id=None, at_datetime=None)`:

1. Start with current `raw_html` from `html_content`
2. Fetch snapshots from `html_snapshots` ordered by `snapshot_at DESC`
3. Apply reverse diffs sequentially (decompress with `zlib.decompress()`, apply patch) until reaching the target
4. Return reconstructed `raw_html`

Target selection: if `snapshot_id` is given, stop at that row. If `at_datetime` is given, stop at the first snapshot with `snapshot_at <= at_datetime`.

`HTMLFetcher.list_snapshots(url_id)` — returns list of `(id, content_hash, snapshot_at)` for a URL, ordered by `snapshot_at DESC`.

These are utility methods for debugging — not called by the pipeline. No API endpoint initially.

### Diff Format

Unified diffs via Python's `difflib.unified_diff()` operating on line-split `raw_html`. Patch application uses a simple unified-diff applier. Since HTML is line-based text, unified diffs work well and produce compact output.

Storage estimate: a diff of two HTML pages is typically 5-20% the size of the full page. With zlib compression on top, this shrinks further.

## Migration

- Add `html_snapshots` to `_TABLE_SCHEMAS` in `database/schema.py` (created on `make seed`)
- Add to `_ALL_TABLES` list for `make reset-db`
- No data migration — history starts accumulating from the next scrape

## Files Changed

- `html_fetcher.py` — add `archive_snapshot()`, `reconstruct_snapshot()`, `list_snapshots()`
- `database/schema.py` — add table definition, add to `_ALL_TABLES`
- `tests/test_html_fetcher.py` — new test cases

No changes to: `publication.py`, `link_extractor.py`, `scheduler.py`, `api.py`, frontend, or any existing `html_content` queries.

## Testing

- `test_archive_snapshot_on_text_change` — verify snapshot row inserted with compressed reverse diff when `save_text()` is called with a prior row existing
- `test_no_archive_on_first_fetch` — verify no snapshot created when no prior row exists
- `test_archive_failure_doesnt_block_save` — simulate DB error in archive, verify `save_text()` still succeeds
- `test_reconstruct_snapshot` — create a chain of 2-3 diffs, verify reconstruction produces original HTML at each point
- `test_list_snapshots` — verify correct ordering and content
