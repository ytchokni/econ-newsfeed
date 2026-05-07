# Fix False Feed Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate false `new_paper` feed events caused by non-deterministic LLM extraction finding papers that were always on the page.

**Architecture:** Add a title-in-previous-HTML check before creating `new_paper` events. When a paper is newly extracted but its title appears in the previous HTML snapshot, it's not actually new — the LLM just missed it before. Also clean up the 7 existing false events and their incorrectly-seeded papers.

**Tech Stack:** Python, MySQL, existing `html_snapshots` table with `raw_html_compressed` (zlib)

---

## Root Cause

The LLM extraction is non-deterministic. Different runs extract different subsets of papers from the same page. The system treats "paper not in DB + URL has baseline snapshots" as "new paper appeared on website", but it could be the LLM finding a paper it missed on a prior run.

Evidence: all 7 current feed events reference papers whose titles appear in **both** HTML snapshots (snapshot 1 and snapshot 2) — the papers were always on the page.

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `publication.py` | Modify | Add `_title_in_previous_snapshot()` check before creating `new_paper` events |
| `database/schema.py` | Modify | Update DB trigger to also check previous snapshot text |
| `tests/test_feed_event_validation.py` | Create | Tests for the new validation logic |
| `scripts/cleanup_false_feed_events.py` | Modify | One-time script to clean up existing false events |

---

### Task 1: Add title-in-previous-snapshot check to `publication.py`

**Files:**
- Modify: `publication.py:210-221` (`_url_has_baseline`)
- Modify: `publication.py:283-298` (new paper event creation for new papers)
- Modify: `publication.py:346-364` (new paper event creation for duplicates)
- Test: `tests/test_feed_event_validation.py`

The idea: before creating a `new_paper` feed event, check if the paper's title appears (case-insensitive substring match) in the most recent *previous* HTML snapshot's decompressed text. If it does, the paper was already on the page — suppress the event.

- [ ] **Step 1: Write failing tests**

Create `tests/test_feed_event_validation.py`:

```python
"""Tests for new_paper feed event validation — title-in-previous-snapshot check."""
import unittest
import zlib
from unittest.mock import MagicMock, patch

from publication import _title_in_previous_snapshot


class TestTitleInPreviousSnapshot(unittest.TestCase):
    """_title_in_previous_snapshot returns True if title appears in the prior HTML."""

    @patch("publication.Database")
    def test_title_found_in_previous_snapshot(self, mock_db):
        """Title present in previous HTML → returns True (suppress event)."""
        html = "<h2>Insult Politics in the Age of Social Media</h2>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Insult Politics in the Age of Social Media", "https://example.com/")
        assert result is True

    @patch("publication.Database")
    def test_title_not_found_in_previous_snapshot(self, mock_db):
        """Title absent from previous HTML → returns False (allow event)."""
        html = "<h2>Some Other Paper Title</h2>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Brand New Paper Title", "https://example.com/")
        assert result is False

    @patch("publication.Database")
    def test_case_insensitive_match(self, mock_db):
        """Match should be case-insensitive."""
        html = "<h2>INSULT POLITICS IN THE AGE OF SOCIAL MEDIA</h2>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Insult Politics in the Age of Social Media", "https://example.com/")
        assert result is True

    @patch("publication.Database")
    def test_no_previous_snapshot(self, mock_db):
        """No previous snapshot exists → returns False (allow event)."""
        mock_db.fetch_one.return_value = None

        result = _title_in_previous_snapshot("Any Title", "https://example.com/")
        assert result is False

    @patch("publication.Database")
    def test_partial_title_match(self, mock_db):
        """A meaningful substring (first 40 chars) should match."""
        html = "<p>Monetary Policy Shocks: A New Hope — some extra text</p>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        # Title with suffix change — the core title is in the HTML
        result = _title_in_previous_snapshot(
            "Monetary Policy Shocks: A New Hope — Job Market Paper",
            "https://example.com/",
        )
        assert result is True

    @patch("publication.Database")
    def test_short_title_uses_full_match(self, mock_db):
        """Titles shorter than 40 chars use full title for matching."""
        html = "<p>Short Paper</p>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Short Paper", "https://example.com/")
        assert result is True


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_feed_event_validation.py -v`
Expected: FAIL with `ImportError: cannot import name '_title_in_previous_snapshot'`

- [ ] **Step 3: Implement `_title_in_previous_snapshot`**

Add to `publication.py` after the `_url_has_baseline` function (after line 221):

```python
def _title_in_previous_snapshot(title: str, source_url: str) -> bool:
    """Return True if the paper title appears in the previous HTML snapshot.

    Checks the second-most-recent html_snapshot for this URL. If the title
    (or its first 40 characters for long titles) is found in the decompressed
    HTML, the paper was already on the page and should not generate a
    new_paper feed event.
    """
    import zlib

    row = Database.fetch_one(
        """SELECT hs.raw_html_compressed
           FROM html_snapshots hs
           JOIN researcher_urls ru ON ru.id = hs.url_id
           WHERE ru.url = %s
           ORDER BY hs.snapshot_at DESC
           LIMIT 1 OFFSET 1""",
        (source_url,),
    )
    if not row or not row['raw_html_compressed']:
        return False

    try:
        html_text = zlib.decompress(row['raw_html_compressed']).decode('utf-8', errors='replace').lower()
    except Exception:
        return False

    # Use first 40 chars of title to handle minor suffix changes
    search_term = title[:40].lower() if len(title) > 40 else title.lower()
    return search_term in html_text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_feed_event_validation.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Wire the check into `save_publications` — new paper path**

In `publication.py`, modify the new paper feed event creation block (around line 283-298). Change:

```python
                        # Create new_paper feed event only when source URL has established baseline
                        pub_status = pub.get('status')
                        if not is_seed and pub_status and pub_status != 'published':
                            if has_baseline:
                                cursor.execute(
```

To:

```python
                        # Create new_paper feed event only when source URL has established baseline
                        # AND the paper title was NOT in the previous HTML snapshot
                        pub_status = pub.get('status')
                        if not is_seed and pub_status and pub_status != 'published':
                            if has_baseline and not _title_in_previous_snapshot(title, url):
                                cursor.execute(
```

Also update the suppression log message (around line 294-298). Change:

```python
                            else:
                                logging.info(
                                    "Suppressed new_paper event for '%s': source URL lacks baseline snapshots",
                                    pub['title'],
                                )
```

To:

```python
                            elif not has_baseline:
                                logging.info(
                                    "Suppressed new_paper event for '%s': source URL lacks baseline snapshots",
                                    pub['title'],
                                )
                            else:
                                logging.info(
                                    "Suppressed new_paper event for '%s': title found in previous HTML snapshot",
                                    pub['title'],
                                )
```

- [ ] **Step 6: Wire the check into `save_publications` — duplicate paper path**

In `publication.py`, modify the duplicate paper feed event creation block (around line 346-364). Change:

```python
                        if not is_seed and new_to_this_url and pub_status and pub_status != 'published':
                            if has_baseline:
```

To:

```python
                        if not is_seed and new_to_this_url and pub_status and pub_status != 'published':
                            if has_baseline and not _title_in_previous_snapshot(title, url):
```

Also update the corresponding suppression log (around line 360-364). Change:

```python
                            else:
                                logging.info(
                                    "Suppressed new_paper event for '%s': source URL lacks baseline snapshots",
                                    pub['title'],
                                )
```

To:

```python
                            elif not has_baseline:
                                logging.info(
                                    "Suppressed new_paper event for '%s': source URL lacks baseline snapshots",
                                    pub['title'],
                                )
                            else:
                                logging.info(
                                    "Suppressed new_paper event for '%s': title found in previous HTML snapshot",
                                    pub['title'],
                                )
```

- [ ] **Step 7: Run full test suite**

Run: `poetry run pytest tests/ -v --tb=short`
Expected: all tests pass, no regressions

- [ ] **Step 8: Commit**

```bash
git add publication.py tests/test_feed_event_validation.py
git commit -m "fix: suppress false new_paper events when title exists in previous HTML snapshot

Papers extracted by the LLM that were already present in the prior HTML
snapshot are not genuinely new — the LLM just missed them before. Check
the decompressed previous snapshot for the title before creating events."
```

---

### Task 2: Clean up existing false feed events

**Files:**
- Modify: `scripts/cleanup_false_feed_events.py`

- [ ] **Step 1: Review existing cleanup script**

Read `scripts/cleanup_false_feed_events.py` to understand the current structure.

- [ ] **Step 2: Update the cleanup script**

Replace the content of `scripts/cleanup_false_feed_events.py` with a script that:
1. Finds all `new_paper` feed events where the paper title appears in the previous HTML snapshot
2. Deletes those events
3. Reports what was cleaned up

```python
#!/usr/bin/env python3
"""Clean up false new_paper feed events.

Finds events where the paper's title was already present in the previous
HTML snapshot (i.e., the LLM just missed it on a prior extraction).
"""
import logging
import sys
import zlib

# Must set env before importing app modules
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "econ_app")
os.environ.setdefault("DB_PASSWORD", "secret")
os.environ.setdefault("DB_NAME", "econ_newsfeed")

from database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DRY_RUN = "--dry-run" in sys.argv


def main():
    events = Database.fetch_all("""
        SELECT fe.id AS event_id, p.id AS paper_id, p.title, p.source_url
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        WHERE fe.event_type = 'new_paper'
        ORDER BY fe.created_at
    """)
    logging.info("Found %d new_paper feed events to check", len(events))

    false_events = []
    for ev in events:
        row = Database.fetch_one(
            """SELECT hs.raw_html_compressed
               FROM html_snapshots hs
               JOIN researcher_urls ru ON ru.id = hs.url_id
               WHERE ru.url = %s
               ORDER BY hs.snapshot_at DESC
               LIMIT 1 OFFSET 1""",
            (ev['source_url'],),
        )
        if not row or not row['raw_html_compressed']:
            continue
        try:
            html = zlib.decompress(row['raw_html_compressed']).decode('utf-8', errors='replace').lower()
        except Exception:
            continue

        title = ev['title']
        search = title[:40].lower() if len(title) > 40 else title.lower()
        if search in html:
            false_events.append(ev)
            logging.info("FALSE: event %d, paper %d: '%s'", ev['event_id'], ev['paper_id'], title[:60])

    logging.info("Found %d false events out of %d total", len(false_events), len(events))

    if not false_events:
        logging.info("Nothing to clean up")
        return

    if DRY_RUN:
        logging.info("DRY RUN — no changes made. Run without --dry-run to delete.")
        return

    event_ids = [ev['event_id'] for ev in false_events]
    placeholders = ",".join(["%s"] * len(event_ids))
    Database.execute_query(
        f"DELETE FROM feed_events WHERE id IN ({placeholders})",
        tuple(event_ids),
    )
    logging.info("Deleted %d false feed events", len(event_ids))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run with --dry-run to verify detection**

Run: `poetry run python scripts/cleanup_false_feed_events.py --dry-run`
Expected: logs showing all 7 false events detected, no changes made

- [ ] **Step 4: Run for real to delete false events**

Run: `poetry run python scripts/cleanup_false_feed_events.py`
Expected: 7 false events deleted

- [ ] **Step 5: Verify the feed is now empty**

```bash
docker exec econ-newsfeed-db-1 mysql -u econ_app -psecret econ_newsfeed \
  -e "SELECT COUNT(*) FROM feed_events;" 2>/dev/null
```
Expected: 0 rows

- [ ] **Step 6: Commit**

```bash
git add scripts/cleanup_false_feed_events.py
git commit -m "fix: clean up 7 false new_paper feed events

All existing events were from papers that existed in previous HTML
snapshots but were missed by earlier LLM extraction runs."
```

---

### Task 3: Update DB trigger to match application logic

**Files:**
- Modify: `database/schema.py:544-585` (trigger definition)

The DB trigger (`trg_feed_events_snapshot_guard`) currently only checks HTML snapshot count >= 2. It should also check the previous snapshot for the title. However, MySQL triggers have limited capabilities (no zlib decompression). Instead, relax the trigger to only check snapshot count and rely on the application-layer check for the title validation.

- [ ] **Step 1: Add a comment to the trigger documenting the limitation**

In `database/schema.py`, update the comment at line 544:

```python
                    # DB-level safety net: checks snapshot count only.
                    # The full validation (title-in-previous-snapshot) is in
                    # publication._title_in_previous_snapshot() — MySQL triggers
                    # cannot decompress zlib blobs, so this is a coarse guard.
```

No functional change to the trigger — it still provides the snapshot-count safety net.

- [ ] **Step 2: Commit**

```bash
git add database/schema.py
git commit -m "docs: clarify DB trigger limitation vs application-layer check"
```

---

### Task 4: Fix `main.py` extract path to also use diff-based extraction

**Files:**
- Modify: `main.py:43-64` (`extract_data_from_htmls`)

The scheduler already uses `compute_diff` to only send added/changed lines to the LLM. But `make parse` (`extract_data_from_htmls`) sends the full HTML text, which is why the LLM can "discover" papers it missed before. Align the two code paths.

- [ ] **Step 1: Modify `extract_data_from_htmls` to use diff when previous content exists**

Change `main.py` lines 43-64:

```python
def extract_data_from_htmls() -> None:
    """Extract publication data from downloaded HTML content."""
    researcher_urls = Researcher.get_all_researcher_urls()
    for row in researcher_urls:
        id, researcher_id, url, page_type = row['id'], row['researcher_id'], row['url'], row['page_type']
        if not HTMLFetcher.needs_extraction(id):
            logging.info(f"Skipping extraction for URL ID: {id}, URL: {url} (content unchanged since last extraction)")
            continue
        logging.info(f"Extracting data from HTML for URL ID: {id}, URL: {url}, Page Type: {page_type}")
        html_content = HTMLFetcher.get_latest_text(id)
        if html_content:
            is_seed = HTMLFetcher.is_first_extraction(id)
            # Use diff-based extraction when previous content exists (matches scheduler behavior)
            if not is_seed:
                previous_text = HTMLFetcher.get_extracted_text(id)
                if previous_text:
                    extraction_text = HTMLFetcher.compute_diff(previous_text, html_content)
                else:
                    extraction_text = html_content
            else:
                extraction_text = html_content
            extracted_publications = Publication.extract_publications(extraction_text, url)
            if extracted_publications:
                Publication.save_publications(url, extracted_publications, is_seed=is_seed)
                reconcile_title_renames(url, extracted_publications)
                match_and_save_paper_links(id, extracted_publications)
            else:
                logging.warning(f"No publications extracted for URL ID: {id}, URL: {url}")
            HTMLFetcher.mark_extracted(id)
        else:
            logging.error(f"No HTML content found for URL ID: {id}, URL: {url}")
```

Note: This requires checking whether `HTMLFetcher.get_extracted_text()` exists or if we need to use the snapshot-based approach. The scheduler uses `get_previous_text()` which returns the current stored content (before upsert). For `main.py`, the content has already been updated by a prior `make fetch`, so we need the text that was *last extracted against*. If no such method exists, we can use the second-most-recent HTML snapshot's text instead.

- [ ] **Step 2: Check if `get_extracted_text` helper is needed**

If `HTMLFetcher` doesn't have a method to retrieve the previously-extracted text content, add one. The `html_content` table stores `content` (current) and `extracted_hash`. We can retrieve the snapshot matching `extracted_hash`:

```python
@staticmethod
def get_previously_extracted_text(url_id: int) -> str | None:
    """Get the text content that was last sent to LLM extraction.

    Returns the html_content.content if extracted_hash matches a previous
    content_hash, indicating what was last extracted. Returns None if
    never extracted.
    """
    result = Database.fetch_one(
        "SELECT content, extracted_hash, content_hash FROM html_content WHERE url_id = %s",
        (url_id,),
    )
    if not result or not result['extracted_hash']:
        return None
    # If extracted_hash == content_hash, content hasn't changed since extraction
    # In that case there's no diff to compute
    if result['extracted_hash'] == result['content_hash']:
        return None
    # The current content is newer than what was extracted.
    # We don't have the old text anymore (upsert overwrote it).
    # Fall back to the snapshot approach.
    return None
```

Since `html_content` uses upsert and only stores the latest version, we can't retrieve the old text directly. Instead, use the snapshot-based approach in `_title_in_previous_snapshot` (Task 1) as the primary guard, and keep `extract_data_from_htmls` sending full text. The application-layer check in Task 1 is sufficient.

**Decision: Skip this task.** The `_title_in_previous_snapshot` check from Task 1 is the correct fix. Trying to replicate the scheduler's diff-based approach in `main.py` is fragile because the old text is lost after upsert. The title check is more robust — it works regardless of which code path created the paper.

- [ ] **Step 3: Commit (if any changes were made)**

Skip — no changes needed. Task 1's fix covers this.

---

## Summary

| Task | What | Why |
|------|------|-----|
| 1 | Add `_title_in_previous_snapshot` check | Prevents false events at the source |
| 2 | Clean up existing false events | Removes 7 known false positives |
| 3 | Document DB trigger limitation | Clarifies the two-layer validation |
| ~~4~~ | ~~Diff-based extraction in main.py~~ | ~~Skipped — Task 1's check is sufficient~~ |
