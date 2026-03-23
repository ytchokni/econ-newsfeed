# Feed Seed Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the newsfeed from showing all existing papers by auto-detecting first-time URL extractions as seed data, and clean up the 1,579 spurious feed events from the initial scrape.

**Architecture:** Add an `is_first_extraction(url_id)` check to `HTMLFetcher` that returns `True` when `extracted_at IS NULL` in `html_content` (meaning the URL has never been parsed before). Pass this as `is_seed=True` to `Publication.save_publications()` at all 3 call sites in `main.py`. Clean up existing bad feed events via a one-time SQL script.

**Tech Stack:** Python, MySQL, pytest (mocked DB — no real connection needed)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `html_fetcher.py` | Modify (add method ~line 423) | New `is_first_extraction(url_id)` static method |
| `main.py` | Modify (lines 57, 77, 301) | Pass `is_seed` at all 3 `save_publications` call sites |
| `tests/test_html_fetcher.py` | Modify (append) | Test `is_first_extraction` |
| `tests/test_feed_seed_detection.py` | Create | Test that extraction pipelines pass `is_seed` correctly |
| `scripts/cleanup_seed_events.py` | Create | One-time script to delete spurious feed events |

---

### Task 1: Add `is_first_extraction` to HTMLFetcher

**Files:**
- Modify: `html_fetcher.py:423` (after `needs_extraction`)
- Test: `tests/test_html_fetcher.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_html_fetcher.py`:

```python
class TestIsFirstExtraction:
    """Tests for HTMLFetcher.is_first_extraction()."""

    @patch("html_fetcher.Database.fetch_one")
    def test_returns_true_when_never_extracted(self, mock_fetch):
        """extracted_at IS NULL means first extraction."""
        mock_fetch.return_value = {"extracted_at": None}
        assert HTMLFetcher.is_first_extraction(1) is True

    @patch("html_fetcher.Database.fetch_one")
    def test_returns_false_when_previously_extracted(self, mock_fetch):
        """extracted_at is set means already extracted before."""
        mock_fetch.return_value = {"extracted_at": "2026-03-19 12:00:00"}
        assert HTMLFetcher.is_first_extraction(1) is False

    @patch("html_fetcher.Database.fetch_one")
    def test_returns_false_when_no_html_content(self, mock_fetch):
        """No html_content row at all — nothing to extract."""
        mock_fetch.return_value = None
        assert HTMLFetcher.is_first_extraction(1) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_html_fetcher.py::TestIsFirstExtraction -v`
Expected: FAIL with `AttributeError: type object 'HTMLFetcher' has no attribute 'is_first_extraction'`

- [ ] **Step 3: Write minimal implementation**

Add to `html_fetcher.py` after the `needs_extraction` method (after line 422):

```python
@staticmethod
def is_first_extraction(url_id: int) -> bool:
    """Return True if this URL has never been extracted before.

    Checks whether extracted_at is NULL in html_content — meaning
    mark_extracted() has never been called for this URL.
    """
    result = Database.fetch_one(
        "SELECT extracted_at FROM html_content WHERE url_id = %s",
        (url_id,),
    )
    return result is not None and result['extracted_at'] is None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_html_fetcher.py::TestIsFirstExtraction -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "feat: add HTMLFetcher.is_first_extraction() for seed detection"
```

---

### Task 2: Pass `is_seed` at all extraction call sites

**Files:**
- Modify: `main.py:57` (`extract_data_from_htmls`)
- Modify: `main.py:77` (`_process_one_url`)
- Modify: `main.py:301` (`batch_check`)
- Test: `tests/test_feed_seed_detection.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feed_seed_detection.py`:

```python
"""Tests that extraction pipelines pass is_seed=True on first extraction."""

import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("CONTENT_MAX_CHARS", "4000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, MagicMock
import pytest


class TestExtractDataSeedDetection:
    """extract_data_from_htmls passes is_seed based on is_first_extraction."""

    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction")
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    @patch("main.Researcher.get_all_researcher_urls")
    def test_first_extraction_passes_is_seed_true(
        self, mock_urls, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links,
    ):
        mock_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "https://example.com", "page_type": "personal"}
        ]
        mock_first.return_value = True

        from main import extract_data_from_htmls
        extract_data_from_htmls()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is True

    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction")
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    @patch("main.Researcher.get_all_researcher_urls")
    def test_subsequent_extraction_passes_is_seed_false(
        self, mock_urls, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links,
    ):
        mock_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "https://example.com", "page_type": "personal"}
        ]
        mock_first.return_value = False

        from main import extract_data_from_htmls
        extract_data_from_htmls()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is False


class TestProcessOneUrlSeedDetection:
    """_process_one_url passes is_seed based on is_first_extraction."""

    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction", return_value=True)
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    def test_first_extraction_passes_is_seed_true(
        self, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links,
    ):
        from main import _process_one_url
        _process_one_url(1, 10, "https://example.com", "personal")

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is True


class TestBatchCheckSeedDetection:
    """batch_check passes is_seed based on is_first_extraction."""

    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.match_and_save_paper_links")
    @patch("main.Publication.save_publications")
    @patch("main.HTMLFetcher.is_first_extraction", return_value=True)
    @patch("main.Database.log_llm_usage")
    @patch("main.Database.execute_query")
    @patch("main.Database.fetch_one")
    @patch("main.Database.fetch_all")
    def test_batch_check_first_extraction_passes_is_seed_true(
        self, mock_fetch_all, mock_fetch_one, mock_exec, mock_log,
        mock_first, mock_save, mock_links, mock_mark,
    ):
        """batch_check should pass is_seed=True for URLs never extracted before."""
        import json
        from unittest.mock import MagicMock

        # One pending batch
        mock_fetch_all.return_value = [{"id": 1, "openai_batch_id": "batch_abc"}]

        # url_row lookup and cost aggregation
        mock_fetch_one.side_effect = [
            {"url": "https://example.com"},  # url_row for url_id=5
            {"total_cost": 0.01},            # cost aggregation
        ]

        # Build a valid batch API response
        batch_result = {
            "custom_id": "url_5",
            "response": {
                "body": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "choices": [{"message": {"content": json.dumps([{
                        "title": "Paper",
                        "authors": [["A", "B"]],
                        "year": "2024",
                        "venue": None,
                        "status": "working_paper",
                        "draft_url": None,
                        "abstract": None,
                    }])}}],
                },
            },
        }

        # Mock OpenAI client
        mock_client = MagicMock()
        mock_batch = MagicMock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "file_out"
        mock_client.batches.retrieve.return_value = mock_batch
        mock_client.files.content.return_value.text = json.dumps(batch_result)

        with patch("main.OpenAI", return_value=mock_client):
            from main import batch_check
            batch_check()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_feed_seed_detection.py -v`
Expected: FAIL — `save_publications` called without `is_seed` keyword arg (kwargs will be empty)

- [ ] **Step 3: Modify `extract_data_from_htmls` in `main.py`**

At `main.py:55-57`, change from:

```python
        if extracted_publications:
            Publication.save_publications(url, extracted_publications)
```

To:

```python
        if extracted_publications:
            is_seed = HTMLFetcher.is_first_extraction(id)
            Publication.save_publications(url, extracted_publications, is_seed=is_seed)
```

- [ ] **Step 4: Modify `_process_one_url` in `main.py`**

At `main.py:76-77`, change from:

```python
        if pubs:
            Publication.save_publications(url, pubs)
```

To:

```python
        if pubs:
            is_seed = HTMLFetcher.is_first_extraction(url_id)
            Publication.save_publications(url, pubs, is_seed=is_seed)
```

- [ ] **Step 5: Modify `batch_check` in `main.py`**

At `main.py:300-301`, change from:

```python
                if validated:
                    Publication.save_publications(url, validated)
```

To:

```python
                if validated:
                    is_seed = HTMLFetcher.is_first_extraction(url_id)
                    Publication.save_publications(url, validated, is_seed=is_seed)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/test_feed_seed_detection.py -v`
Expected: All passed

- [ ] **Step 7: Run full test suite**

Run: `poetry run pytest -v`
Expected: All existing tests still pass

- [ ] **Step 8: Commit**

```bash
git add main.py tests/test_feed_seed_detection.py
git commit -m "feat: auto-detect first extraction as seed data to prevent spurious feed events"
```

---

### Task 3: Clean up existing spurious feed events

**Files:**
- Create: `scripts/cleanup_seed_events.py`

- [ ] **Step 1: Create the cleanup script**

Create `scripts/cleanup_seed_events.py`:

```python
"""One-time cleanup: delete feed events created during initial scrapes.

All 1,579 existing new_paper events (March 19-20, 2026) were generated
before is_seed detection was implemented. Every paper found on the first
extraction of each URL was incorrectly treated as a new discovery.

Run: poetry run python scripts/cleanup_seed_events.py
"""
import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

# All timestamps in the DB are UTC (datetime.now(timezone.utc) used throughout codebase)
CUTOFF = "2026-03-21 00:00:00"


def main():
    # Show current state
    count = Database.fetch_one("SELECT COUNT(*) AS c FROM feed_events")
    to_delete = Database.fetch_one(
        "SELECT COUNT(*) AS c FROM feed_events WHERE event_type = 'new_paper' AND created_at < %s",
        (CUTOFF,),
    )
    print(f"Feed events total: {count['c']}")
    print(f"Spurious new_paper events (before {CUTOFF} UTC) to delete: {to_delete['c']}")

    if to_delete['c'] == 0:
        print("Nothing to clean up.")
        return

    confirm = input("Proceed with deletion? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    Database.execute_query(
        "DELETE FROM feed_events WHERE event_type = 'new_paper' AND created_at < %s",
        (CUTOFF,),
    )

    remaining = Database.fetch_one("SELECT COUNT(*) AS c FROM feed_events")
    print(f"Deleted {to_delete['c']} spurious feed events")
    print(f"Feed events remaining: {remaining['c']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the cleanup script**

Run: `poetry run python scripts/cleanup_seed_events.py`
Expected output (confirm with `y` when prompted):
```
Feed events total: 1579
Spurious new_paper events (before 2026-03-21 00:00:00 UTC) to delete: 1579
Proceed with deletion? [y/N] y
Deleted 1579 spurious feed events
Feed events remaining: 0
```

- [ ] **Step 3: Verify the newsfeed is now empty (correct baseline)**

Run: `curl -s http://localhost:8001/api/feed | python -m json.tool | head -5`
Expected: `"total": 0, "items": []`

- [ ] **Step 4: Commit**

```bash
git add scripts/cleanup_seed_events.py
git commit -m "fix: clean up 1579 spurious feed events from initial scrape"
```
