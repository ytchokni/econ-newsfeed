# Decouple HTML Fetch from LLM Extraction in Scheduler

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the scheduler's `run_scrape_job()` always complete HTML fetching even when the LLM provider is down or quota-exhausted, and clean up stale `scrape_log` entries on startup.

**Architecture:** Split `run_scrape_job()` into two phases: a fetch-only loop that always runs, and an extraction loop that skips remaining URLs on persistent LLM failure. Add stale scrape_log cleanup so zombie `'running'` entries don't accumulate. Track extraction failures in `scrape_log` via a new `extraction_errors` column.

**Tech Stack:** Python, MySQL, existing `scheduler.py` + `html_fetcher.py` + `publication.py`

---

## Problem Analysis

The scheduler's `run_scrape_job()` interleaves fetch and extraction in a single per-URL loop (scheduler.py:166-248). When OpenAI returns 429 quota errors:

1. `extract_publications()` catches the error internally and returns `[]` — no exception raised
2. The loop continues to next URL: fetches HTML successfully, tries extraction, gets another 429
3. This repeats for all ~17k URLs — every URL gets fetched but extraction silently fails
4. `scrape_log` reports `status='completed'` with `pubs_extracted=0`, hiding the problem
5. The `content_hash` gets updated (new HTML saved) but `extracted_hash` stays stale
6. No feed events are generated because no publications are extracted

Additionally, when the process crashes or fetch is run via `download_htmls()`, `scrape_log` entries get stuck in `status='running'` forever (10 zombie entries currently in local DB).

## Solution

**Phase separation:** Split the single URL loop into two passes:
1. **Fetch pass** — download HTML for all URLs, update `content_hash`. No LLM calls. Always completes.
2. **Extract pass** — for URLs where `content_hash != extracted_hash`, run LLM extraction. Circuit-break after N consecutive failures to avoid burning through the entire URL list on quota errors.

**Stale log cleanup:** On scheduler startup, mark any `scrape_log` entries that have been `'running'` for >24 hours as `'failed'` with a descriptive error message.

**Observability:** Add `extraction_errors` column to `scrape_log` so operators can see how many URLs failed extraction vs succeeded.

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `scheduler.py` | Modify | Split `run_scrape_job()` into fetch + extract passes; add circuit breaker; add stale log cleanup |
| `database/schema.py` | Modify | Add `extraction_errors` column to `scrape_log` |
| `scheduler.py` | Modify | Update `update_scrape_log()` to write `extraction_errors` |
| `tests/test_scheduler.py` | Create | Tests for phase separation, circuit breaker, stale cleanup |

---

### Task 1: Add `extraction_errors` column to `scrape_log`

**Files:**
- Modify: `database/schema.py` (migrations section)
- Modify: `scheduler.py:83-106` (`update_scrape_log` function)

- [ ] **Step 1: Add migration for `extraction_errors` column**

In `database/schema.py`, find the `_run_migrations()` function and add a new migration at the end of the migrations list:

```python
("ALTER TABLE scrape_log ADD COLUMN extraction_errors INT DEFAULT 0 AFTER pubs_extracted",),
```

This follows the existing pattern of idempotent ALTER TABLE migrations in the file.

- [ ] **Step 2: Update `update_scrape_log()` to accept and write `extraction_errors`**

In `scheduler.py`, change the `update_scrape_log` function signature and query:

```python
def update_scrape_log(log_id: int, status: str, urls_checked: int = 0, urls_changed: int = 0, pubs_extracted: int = 0, extraction_errors: int = 0, error_message: str | None = None) -> None:
```

Update the SQL query to include the new column:

```python
    query = """
        UPDATE scrape_log
        SET finished_at = %s, status = %s, urls_checked = %s,
            urls_changed = %s, pubs_extracted = %s, extraction_errors = %s,
            error_message = %s,
            prompt_tokens_total = %s, completion_tokens_total = %s
        WHERE id = %s
    """
    Database.execute_query(query, (
        datetime.now(timezone.utc), status, urls_checked,
        urls_changed, pubs_extracted, extraction_errors, error_message,
        prompt_tokens_total, completion_tokens_total, log_id
    ))
```

- [ ] **Step 3: Run migrations to verify column is added**

Run: `poetry run python -c "from database import Database; Database.create_tables()"`
Expected: No errors. Column added to `scrape_log`.

Verify: `docker compose exec -T db mysql -u econ_app -psecret econ_newsfeed -e "DESCRIBE scrape_log;"` should show `extraction_errors` column.

- [ ] **Step 4: Commit**

```bash
git add database/schema.py scheduler.py
git commit -m "feat: add extraction_errors column to scrape_log

Tracks how many URLs failed LLM extraction during a scrape run,
separate from the overall status. Needed for observability when
quota errors cause silent extraction failures."
```

---

### Task 2: Split `run_scrape_job()` into fetch + extract passes

**Files:**
- Modify: `scheduler.py:144-269` (`run_scrape_job` function)
- Test: `tests/test_scheduler.py`

This is the core change. The current single loop does fetch+extract per URL. We split into:
1. **Fetch pass:** iterate all URLs, call `fetch_and_save_if_changed()`, collect which ones changed.
2. **Extract pass:** iterate only changed URLs, run LLM extraction with a circuit breaker.

- [ ] **Step 1: Write tests for the two-pass behavior**

Create `tests/test_scheduler.py`:

```python
"""Tests for scheduler scrape job phase separation and circuit breaker."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "econ_app")
os.environ.setdefault("DB_PASSWORD", "secret")
os.environ.setdefault("DB_NAME", "econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

import unittest
from unittest.mock import patch, MagicMock


class TestFetchExtractSeparation(unittest.TestCase):
    """Verify that fetch completes even when extraction fails."""

    @patch("scheduler._release_db_lock")
    @patch("scheduler._acquire_db_lock")
    @patch("scheduler.create_scrape_log", return_value=1)
    @patch("scheduler.update_scrape_log")
    @patch("scheduler._enrich_with_openalex")
    @patch("scheduler.merge_duplicate_papers", side_effect=Exception("skip"))
    @patch("scheduler.Researcher.get_all_researcher_urls")
    @patch("scheduler.HTMLFetcher")
    @patch("scheduler.Publication")
    def test_fetch_completes_when_extraction_fails(
        self, mock_pub, mock_fetcher, mock_urls,
        mock_merge, mock_enrich, mock_update_log,
        mock_create_log, mock_acquire, mock_release,
    ):
        mock_acquire.return_value = MagicMock()

        mock_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "http://a.com", "page_type": "RESEARCH"},
            {"id": 2, "researcher_id": 20, "url": "http://b.com", "page_type": "RESEARCH"},
            {"id": 3, "researcher_id": 30, "url": "http://c.com", "page_type": "RESEARCH"},
        ]

        mock_fetcher.get_previous_text.return_value = "old text"
        mock_fetcher.fetch_and_save_if_changed.return_value = True
        mock_fetcher.get_latest_text.return_value = "new text"
        mock_fetcher.compute_diff.return_value = "diff text"

        # Extraction always returns empty (simulates quota failure)
        mock_pub.extract_publications.return_value = []

        from scheduler import run_scrape_job
        run_scrape_job()

        # All 3 URLs should have been fetched
        self.assertEqual(mock_fetcher.fetch_and_save_if_changed.call_count, 3)

        # update_scrape_log should be called with urls_checked=3, urls_changed=3
        mock_update_log.assert_called_once()
        call_args = mock_update_log.call_args
        self.assertEqual(call_args[0][0], 1)  # log_id
        self.assertEqual(call_args[0][2], 3)  # urls_checked
        self.assertEqual(call_args[0][3], 3)  # urls_changed


class TestExtractionCircuitBreaker(unittest.TestCase):
    """Verify circuit breaker stops extraction after consecutive failures."""

    @patch("scheduler._release_db_lock")
    @patch("scheduler._acquire_db_lock")
    @patch("scheduler.create_scrape_log", return_value=1)
    @patch("scheduler.update_scrape_log")
    @patch("scheduler._enrich_with_openalex")
    @patch("scheduler._validate_draft_urls")
    @patch("scheduler.merge_duplicate_papers", side_effect=Exception("skip"))
    @patch("scheduler.Researcher.get_all_researcher_urls")
    @patch("scheduler.HTMLFetcher")
    @patch("scheduler.Publication")
    def test_circuit_breaker_stops_extraction_after_consecutive_failures(
        self, mock_pub, mock_fetcher, mock_urls,
        mock_merge, mock_validate, mock_enrich,
        mock_update_log, mock_create_log, mock_acquire, mock_release,
    ):
        mock_acquire.return_value = MagicMock()

        # 20 URLs, all changed
        mock_urls.return_value = [
            {"id": i, "researcher_id": i * 10, "url": f"http://r{i}.com", "page_type": "RESEARCH"}
            for i in range(1, 21)
        ]

        mock_fetcher.get_previous_text.return_value = "old"
        mock_fetcher.fetch_and_save_if_changed.return_value = True
        mock_fetcher.get_latest_text.return_value = "new"
        mock_fetcher.compute_diff.return_value = "diff"

        # Extraction always returns empty (quota failure)
        mock_pub.extract_publications.return_value = []

        from scheduler import run_scrape_job, _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD
        run_scrape_job()

        # All 20 URLs should still be fetched
        self.assertEqual(mock_fetcher.fetch_and_save_if_changed.call_count, 20)

        # But extraction should stop after the circuit breaker threshold
        self.assertEqual(
            mock_pub.extract_publications.call_count,
            _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_scheduler.py -v`
Expected: FAIL — `_EXTRACTION_CIRCUIT_BREAKER_THRESHOLD` doesn't exist yet, and `run_scrape_job` still uses the single-loop approach.

- [ ] **Step 3: Rewrite `run_scrape_job()` with two-pass architecture**

Replace the body of `run_scrape_job()` in `scheduler.py` (lines 144-285) with:

```python
_EXTRACTION_CIRCUIT_BREAKER_THRESHOLD = 10


def run_scrape_job() -> None:
    """Orchestrates a full scraping cycle: fetch all HTML first, then extract.

    Fetch always completes. Extraction circuit-breaks after
    _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD consecutive failures (e.g. quota exhausted).
    """
    global _lock_conn
    lock_conn = _acquire_db_lock()
    if lock_conn is None:
        logger.warning("Scrape already in progress, skipping")
        return
    _lock_conn = lock_conn

    HTMLFetcher._robots_cache.clear()

    log_id = None
    try:
        log_id = create_scrape_log()
        urls = Researcher.get_all_researcher_urls()
        urls_checked = 0
        urls_changed = 0
        pubs_extracted = 0
        extraction_errors = 0

        # ── PHASE 1: Fetch all HTML ──────────────────────────────────
        scrape_start = time.time()
        changed_urls = []

        for url_row in urls:
            url_id = url_row['id']
            researcher_id = url_row['researcher_id']
            url = url_row['url']
            page_type = url_row['page_type']
            urls_checked += 1

            try:
                old_text = HTMLFetcher.get_previous_text(url_id)
                is_first_scrape = old_text is None

                t0 = time.time()
                changed = HTMLFetcher.fetch_and_save_if_changed(url_id, url, researcher_id)
                fetch_ms = (time.time() - t0) * 1000
                logger.info(f"[{urls_checked}/{len(urls)}] fetch {url} — {fetch_ms:.0f}ms (changed={changed})")

                if changed:
                    urls_changed += 1
                    changed_urls.append({
                        'url_id': url_id,
                        'researcher_id': researcher_id,
                        'url': url,
                        'page_type': page_type,
                        'old_text': old_text,
                        'is_first_scrape': is_first_scrape,
                    })

            except Exception as e:
                logger.error("Error fetching URL %s (id=%s): %s", url, url_id, e)
                continue

        fetch_phase_s = time.time() - scrape_start
        logger.info(f"Fetch phase done: {fetch_phase_s:.1f}s — {urls_checked} checked, {urls_changed} changed")

        # ── PHASE 2: Extract publications from changed URLs ──────────
        extract_start = time.time()
        consecutive_failures = 0
        circuit_broken = False

        for entry in changed_urls:
            url_id = entry['url_id']
            researcher_id = entry['researcher_id']
            url = entry['url']
            page_type = entry['page_type']
            old_text = entry['old_text']
            is_first_scrape = entry['is_first_scrape']

            if circuit_broken:
                break

            try:
                new_text = HTMLFetcher.get_latest_text(url_id)
                extraction_text = HTMLFetcher.compute_diff(old_text, new_text) if old_text else new_text

                if extraction_text:
                    t0 = time.time()
                    pubs = Publication.extract_publications(extraction_text, url, scrape_log_id=log_id)
                    extract_ms = (time.time() - t0) * 1000
                    logger.info(f"  LLM extract {url} — {extract_ms:.0f}ms, {len(pubs)} pubs")

                    if pubs:
                        consecutive_failures = 0

                        t0 = time.time()
                        Publication.save_publications(url, pubs, is_seed=is_first_scrape)
                        save_ms = (time.time() - t0) * 1000
                        logger.info(f"  save_publications — {save_ms:.0f}ms")

                        t0_recon = time.time()
                        reconcile_title_renames(url, pubs)
                        recon_ms = (time.time() - t0_recon) * 1000
                        logger.info(f"  title reconciliation — {recon_ms:.0f}ms")

                        pubs_extracted += len(pubs)

                        t0 = time.time()
                        match_and_save_paper_links(url_id, pubs)
                        links_ms = (time.time() - t0) * 1000
                        logger.info(f"  paper links — {links_ms:.0f}ms")

                        t0 = time.time()
                        append_snapshots_for_pubs(pubs, url)
                        snapshot_ms = (time.time() - t0) * 1000
                        logger.info(f"  paper snapshots — {snapshot_ms:.0f}ms")
                    else:
                        consecutive_failures += 1
                        extraction_errors += 1
                        if consecutive_failures >= _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD:
                            logger.warning(
                                "Circuit breaker: %d consecutive extraction failures — "
                                "stopping extraction (likely LLM quota exhausted). "
                                "Remaining %d changed URLs will be extracted next run.",
                                consecutive_failures,
                                len(changed_urls) - changed_urls.index(entry) - 1,
                            )
                            circuit_broken = True

                # Extract description from HOME pages
                if page_type == "HOME" and not circuit_broken:
                    page_text = HTMLFetcher.get_latest_text(url_id)
                    if page_text:
                        t0 = time.time()
                        description = HTMLFetcher.extract_description(page_text, url, scrape_log_id=log_id)
                        desc_ms = (time.time() - t0) * 1000
                        logger.info(f"  description extract — {desc_ms:.0f}ms (found={description is not None})")
                        if description:
                            r_row = Database.fetch_one(
                                "SELECT position, affiliation FROM researchers WHERE id = %s",
                                (researcher_id,),
                            )
                            position = r_row['position'] if r_row else None
                            affiliation = r_row['affiliation'] if r_row else None
                            Database.append_researcher_snapshot(
                                researcher_id, position, affiliation, description, source_url=url
                            )

            except Exception as e:
                logger.error("Error extracting URL %s (id=%s): %s", url, url_id, e)
                extraction_errors += 1
                continue

        extract_phase_s = time.time() - extract_start
        logger.info(f"Extract phase done: {extract_phase_s:.1f}s — {pubs_extracted} pubs, {extraction_errors} errors")

        # Validate draft URLs
        t0 = time.time()
        _validate_draft_urls()
        validate_s = time.time() - t0
        logger.info(f"Draft URL validation: {validate_s:.1f}s")

        status = "completed"
        error_msg = None
        if circuit_broken:
            error_msg = f"Extraction circuit-breaker tripped after {_EXTRACTION_CIRCUIT_BREAKER_THRESHOLD} consecutive failures"

        total_s = time.time() - scrape_start
        update_scrape_log(log_id, status, urls_checked, urls_changed, pubs_extracted, extraction_errors, error_msg)
        logger.info(f"Scrape {status}: {urls_checked} checked, {urls_changed} changed, {pubs_extracted} extracted, {extraction_errors} errors — {total_s:.1f}s total")

    except Exception as e:
        logger.error("Scrape job failed: %s: %s", type(e).__name__, e)
        if log_id:
            update_scrape_log(log_id, "failed", error_message=f"{type(e).__name__}: {e}")
    finally:
        _release_db_lock(lock_conn)
        _lock_conn = None

    # Enrich after releasing lock
    t0 = time.time()
    _enrich_with_openalex()
    enrich_s = time.time() - t0
    logger.info(f"OpenAlex enrichment: {enrich_s:.1f}s")

    # Merge duplicate papers
    t0 = time.time()
    try:
        from paper_merge import merge_duplicate_papers
        merge_duplicate_papers()
    except Exception as e:
        logger.error("Paper merge failed: %s: %s", type(e).__name__, e)
    merge_s = time.time() - t0
    logger.info(f"Paper merge: {merge_s:.1f}s")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_scheduler.py -v`
Expected: Both tests PASS

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest -v --tb=short`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: split scrape job into fetch-first + extract phases

HTML fetching now completes for all URLs before extraction begins.
Extraction circuit-breaks after 10 consecutive failures (e.g. LLM
quota exhausted), leaving remaining URLs for the next run. Fetch
results are never lost even when the LLM provider is down."
```

---

### Task 3: Add stale `scrape_log` cleanup

**Files:**
- Modify: `scheduler.py` (add `_cleanup_stale_scrape_logs()`, call from `start_scheduler`)
- Test: `tests/test_scheduler.py` (add test)

Stale `'running'` entries accumulate when processes crash or `download_htmls()` leaves orphaned logs. These don't block the advisory-lock-based scheduler, but they pollute the scrape history and confuse operators.

- [ ] **Step 1: Write failing test**

Add to `tests/test_scheduler.py`:

```python
class TestStaleLogCleanup(unittest.TestCase):
    """Verify stale running scrape_log entries get cleaned up."""

    @patch("scheduler.Database")
    def test_cleanup_marks_old_running_as_failed(self, mock_db):
        mock_db.fetch_all.return_value = [
            {"id": 6, "started_at": "2026-03-19 22:26:31"},
            {"id": 9, "started_at": "2026-03-23 15:13:20"},
        ]

        from scheduler import _cleanup_stale_scrape_logs
        _cleanup_stale_scrape_logs()

        # Should have updated each stale entry
        self.assertEqual(mock_db.execute_query.call_count, 2)
        for call in mock_db.execute_query.call_args_list:
            query = call[0][0]
            params = call[0][1]
            self.assertIn("UPDATE scrape_log", query)
            self.assertIn("failed", params)

    @patch("scheduler.Database")
    def test_cleanup_skips_when_none_stale(self, mock_db):
        mock_db.fetch_all.return_value = []

        from scheduler import _cleanup_stale_scrape_logs
        _cleanup_stale_scrape_logs()

        mock_db.execute_query.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_scheduler.py::TestStaleLogCleanup -v`
Expected: FAIL — `_cleanup_stale_scrape_logs` doesn't exist yet

- [ ] **Step 3: Implement `_cleanup_stale_scrape_logs()`**

Add to `scheduler.py`, after the `update_scrape_log` function:

```python
_STALE_SCRAPE_HOURS = 24


def _cleanup_stale_scrape_logs() -> None:
    """Mark scrape_log entries stuck in 'running' for >24h as 'failed'."""
    stale = Database.fetch_all(
        """SELECT id, started_at FROM scrape_log
           WHERE status = 'running'
             AND started_at < DATE_SUB(NOW(), INTERVAL %s HOUR)""",
        (_STALE_SCRAPE_HOURS,),
    )
    if not stale:
        return
    logger.info("Cleaning up %d stale scrape_log entries", len(stale))
    for row in stale:
        Database.execute_query(
            """UPDATE scrape_log
               SET finished_at = NOW(), status = %s, error_message = %s
               WHERE id = %s""",
            ("failed", "Stale running entry — cleaned up on scheduler start", row['id']),
        )
        logger.info("Marked scrape_log id=%d (started %s) as failed", row['id'], row['started_at'])
```

- [ ] **Step 4: Call cleanup from `start_scheduler()`**

In `scheduler.py`, in the `start_scheduler()` function, add a call right after acquiring the scheduler lock (after line 319 `_scheduler_lock_conn = conn`):

```python
    _cleanup_stale_scrape_logs()
```

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/test_scheduler.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "fix: clean up stale scrape_log entries on scheduler start

Marks scrape_log rows stuck in 'running' for >24h as 'failed'.
These accumulate when processes crash or download_htmls() leaves
orphaned entries."
```

---

### Task 4: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update Pipeline Details section**

In the `**\`make scrape\`**` paragraph in CLAUDE.md, update the description to reflect the two-phase architecture:

Change the first paragraph to:

```markdown
**`make scrape`** runs the full scheduler job (`scheduler.run_scrape_job()`):
1. **Fetch phase**: Download HTML for all researcher URLs, skip unchanged (content hash)
2. **Extract phase**: LLM extracts publications from changed pages, saves to `papers`
3. **Link matching**: Extract trusted-domain links from HTML, match to papers via DOI resolution (regex/Crossref) or anchor text, save to `paper_links`
4. **Draft URL validation**: HEAD-request validation of `draft_url` fields
5. **Enrichment phase** (after releasing scrape lock): Enrich unenriched papers via OpenAlex — DOI lookup first (from `paper_links`), title search fallback (published papers only)
```

Add after the pipeline details:

```markdown
**Extraction circuit breaker:** If 10 consecutive URLs return empty extraction results (e.g. LLM quota exhausted), the extraction phase stops early. Fetched HTML is preserved — extraction will resume on the next run for URLs where `content_hash ≠ extracted_hash`.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document fetch/extract phase separation and circuit breaker"
```

---

## Summary

| Task | What | Why |
|------|------|-----|
| 1 | Add `extraction_errors` to `scrape_log` | Visible count of failed extractions per run |
| 2 | Split `run_scrape_job()` into fetch + extract | Fetch always completes; extraction circuit-breaks on quota failure |
| 3 | Clean up stale `scrape_log` entries | Stop zombie `'running'` entries from accumulating |
| 4 | Update CLAUDE.md | Document the new architecture |

**After implementation:**
- `make scrape` fetches all HTML regardless of LLM provider status
- If LLM quota is exhausted, fetch still completes and extraction stops gracefully after 10 consecutive failures
- Next run picks up extraction for URLs where `content_hash ≠ extracted_hash`
- Stale `scrape_log` entries are auto-cleaned on scheduler start
- `scrape_log` shows `extraction_errors` count for observability
