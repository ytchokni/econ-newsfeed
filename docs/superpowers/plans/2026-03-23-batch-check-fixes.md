# Batch-Check Performance & Error Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two crashes in `save_publications` (author unpacking ValueError, cursor leak causing cascading InternalError) and eliminate redundant LLM disambiguation calls that make `batch-check` slow.

**Architecture:** Three targeted fixes in `publication.py:save_publications()`: (1) normalize author lists with != 2 elements instead of crashing, (2) close cursors in `finally` blocks and use `buffered=True` to prevent dirty connection state, (3) module-level cache for author→researcher_id lookups to skip repeated LLM calls for the same co-author across all publications in a batch run.

**Tech Stack:** Python, MySQL (mysql-connector-python), pytest

**Coordination with DOI enrichment plan:** The DOI-first enrichment plan (`docs/superpowers/plans/2026-03-23-doi-first-enrichment.md`) modifies `database/researchers.py:get_researcher_id` to add `openalex_author_id` matching. This plan deliberately avoids modifying `researchers.py` — all changes are in `publication.py` (the caller side). The author cache lives in `publication.py` and works regardless of `get_researcher_id`'s internal implementation, so these two plans can be executed in any order without merge conflicts.

**Pre-existing test failures:** 5 failures in `tests/test_api_search.py` — unrelated, ignore.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `publication.py` | Modify (lines 73-166) | Fix author validation, cursor lifecycle, add author cache |
| `tests/test_save_publications.py` | Create | Tests for author normalization, cursor cleanup, and cache behavior |

---

### Task 1: Fix Author List Validation

**Files:**
- Modify: `publication.py:141-143`
- Create: `tests/test_save_publications.py`

The LLM sometimes returns author lists with != 2 elements (e.g., `["José", "Luis", "García"]` or `["García"]`). Line 143 (`first_name, last_name = author`) crashes with `ValueError: too many values to unpack (expected 2)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_save_publications.py`:

```python
# tests/test_save_publications.py
"""Tests for Publication.save_publications edge cases."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

import pytest
from unittest.mock import patch, MagicMock
from publication import Publication


def _mock_conn():
    """Create a mock DB connection that simulates INSERT IGNORE with new row."""
    mock_cursor = MagicMock()
    mock_cursor.lastrowid = 1  # Simulate new paper inserted
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestAuthorNormalization:
    """Author lists with != 2 elements should not crash save_publications."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_three_element_author_joins_first_names(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['Jose', 'Luis', 'Garcia'] -> first_name='Jose Luis', last_name='Garcia'."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["Jose", "Luis", "Garcia"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("Jose Luis", "Garcia", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_single_element_author_uses_empty_first_name(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['Garcia'] -> first_name='', last_name='Garcia'."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["Garcia"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("", "Garcia", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_empty_author_list_is_skipped(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """[] -> skip, don't crash."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [[]],
            "year": "2024",
        }])

        mock_get_researcher.assert_not_called()

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_normal_two_element_author_unchanged(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['John', 'Doe'] -> first_name='John', last_name='Doe' (normal case)."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("John", "Doe", conn=conn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_save_publications.py::TestAuthorNormalization -v`
Expected: `test_three_element_author` and `test_single_element_author` and `test_empty_author` FAIL with `ValueError: too many values to unpack`

- [ ] **Step 3: Implement author normalization**

In `publication.py`, replace lines 142-143:

```python
                    # Before (line 142-143):
                    for author_order, author in enumerate(pub['authors'], start=1):
                        first_name, last_name = author
```

With:

```python
                    for author_order, author in enumerate(pub['authors'], start=1):
                        if not author:
                            continue
                        if len(author) == 1:
                            first_name, last_name = "", author[0]
                        elif len(author) == 2:
                            first_name, last_name = author
                        else:
                            # e.g. ["Jose", "Luis", "Garcia"] -> "Jose Luis", "Garcia"
                            first_name = " ".join(author[:-1])
                            last_name = author[-1]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_save_publications.py::TestAuthorNormalization -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add publication.py tests/test_save_publications.py
git commit -m "fix: handle author lists with != 2 elements in save_publications"
```

---

### Task 2: Fix Cursor Leak Causing Cascading Errors

**Files:**
- Modify: `publication.py:80-164` (restructure try/except/finally)
- Modify: `tests/test_save_publications.py` (add cursor cleanup tests)

When an exception occurs mid-publication (e.g., the ValueError from Task 1, or a DB error), the cursor opened at line 85 is never closed. The `except` block (line 159) calls `conn.rollback()` but skips `cursor.close()`. On the next loop iteration, `mysql-connector-python` raises `InternalError: Unread result found` because the old cursor's state pollutes the connection. This cascades: once one publication fails, all subsequent publications in the same batch fail too.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_save_publications.py`:

```python
class TestCursorCleanup:
    """Cursor must be closed even when an exception occurs mid-save."""

    @patch("publication.Database.get_researcher_id", side_effect=RuntimeError("db error"))
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_cursor_closed_on_author_error(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Cursor.close() called even when author processing raises."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        # Should not raise — error is caught and logged
        Publication.save_publications("http://example.com", [{
            "title": "Paper A",
            "authors": [["John", "Doe"]],
            "year": "2024",
        }])

        cursor.close.assert_called()

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_second_pub_succeeds_after_first_pub_fails(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """A failed publication must not prevent subsequent publications from saving."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        # First call raises, second succeeds
        mock_get_researcher.side_effect = [RuntimeError("fail"), 42]

        Publication.save_publications("http://example.com", [
            {"title": "Paper A", "authors": [["Bad", "Author"]], "year": "2024"},
            {"title": "Paper B", "authors": [["Good", "Author"]], "year": "2024"},
        ])

        # Paper B should still be committed
        assert conn.commit.call_count >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_save_publications.py::TestCursorCleanup::test_cursor_closed_on_author_error -v`
Expected: FAIL — `cursor.close()` not called on error path

Note: `test_second_pub_succeeds_after_first_pub_fails` may pass even before the fix because mock cursors don't maintain real MySQL connection state. It serves as a regression/intent test. Only `test_cursor_closed_on_author_error` is expected to fail at this step.

- [ ] **Step 3: Restructure cursor lifecycle with finally block**

In `publication.py`, restructure the `for pub in publications:` loop (lines 80-164).

Three changes:
1. Move `cursor = None` before `try`, add `cursor.close()` in a `finally` block
2. Use `buffered=True` on `conn.cursor()` to prevent `Unread result found`
3. Remove the two existing `cursor.close()` calls (line 128 and line 156) — `finally` handles it

Before (structure):
```python
            for pub in publications:
                try:
                    ...
                    cursor = conn.cursor()           # line 85
                    ...
                    if not row:
                        cursor.close()               # line 128
                        continue
                    ...
                    conn.commit()
                    cursor.close()                   # line 156
                    logging.info(...)
                except Exception as e:
                    logging.error(...)
                    conn.rollback()
```

After (structure):
```python
            for pub in publications:
                cursor = None
                try:
                    ...
                    cursor = conn.cursor(buffered=True)
                    ...
                    if not row:
                        continue                     # no cursor.close() here
                    ...
                    conn.commit()
                    logging.info(...)                # no cursor.close() here
                except Exception as e:
                    logging.error(...)
                    conn.rollback()
                finally:
                    if cursor:
                        cursor.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_save_publications.py::TestCursorCleanup -v`
Expected: 2 passed

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest -v`
Expected: All existing tests still pass (ignore 5 pre-existing failures in test_api_search.py)

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_save_publications.py
git commit -m "fix: close cursor in finally block to prevent cascading InternalError"
```

---

### Task 3: Cache Author Lookups to Eliminate Redundant LLM Calls

**Files:**
- Modify: `publication.py` (add module-level cache + use in save_publications)
- Modify: `tests/test_save_publications.py` (add cache tests)

During `batch-check`, `get_researcher_id` makes an OpenAI API call (~600ms each) for every co-author that doesn't have an exact name match in the DB. The same co-author appears across many publications and across many researcher pages — e.g., "D. Contreras Suarez" was disambiguated 3 separate times in the logs. A module-level cache eliminates all repeated lookups for the lifetime of the process (one `batch-check` run).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_save_publications.py`:

```python
from publication import _author_id_cache


@pytest.fixture(autouse=True)
def clear_author_cache():
    """Ensure each test starts with a clean author cache."""
    _author_id_cache.clear()
    yield
    _author_id_cache.clear()


class TestAuthorLookupCache:
    """get_researcher_id should be called once per unique author, not per occurrence."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_same_author_across_pubs_looked_up_once(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """If 'John Doe' appears in 3 publications, get_researcher_id called once."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        pubs = [
            {"title": f"Paper {i}", "authors": [["John", "Doe"], ["Jane", "Smith"]], "year": "2024"}
            for i in range(3)
        ]

        Publication.save_publications("http://example.com", pubs)

        # 2 unique authors x 1 call each = 2 calls total (not 6)
        assert mock_get_researcher.call_count == 2

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_cache_persists_across_save_publications_calls(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Cache carries over between save_publications calls (same process, different URLs)."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        pub = [{"title": "Paper A", "authors": [["John", "Doe"]], "year": "2024"}]

        Publication.save_publications("http://url1.com", pub)
        Publication.save_publications("http://url2.com", pub)

        # John Doe looked up once across both calls
        assert mock_get_researcher.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_save_publications.py::TestAuthorLookupCache -v`
Expected: FAIL — `_author_id_cache` doesn't exist yet (ImportError), and `get_researcher_id` called once per occurrence

- [ ] **Step 3: Add module-level author cache**

In `publication.py`, add after line 17 (after the module-level constants):

```python
# Thread safety: benign races under CPython GIL; worst case is a redundant LLM lookup
_author_id_cache: dict[tuple[str, str], int] = {}
```

Then in `save_publications`, replace line 144:

```python
                        # Before:
                        author_id = Database.get_researcher_id(first_name, last_name, conn=conn)
```

With:

```python
                        cache_key = (first_name, last_name)
                        if cache_key in _author_id_cache:
                            author_id = _author_id_cache[cache_key]
                        else:
                            author_id = Database.get_researcher_id(first_name, last_name, conn=conn)
                            _author_id_cache[cache_key] = author_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_save_publications.py::TestAuthorLookupCache -v`
Expected: 2 passed

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests pass (ignore 5 pre-existing failures in test_api_search.py)

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_save_publications.py
git commit -m "perf: cache author lookups in save_publications to skip redundant LLM calls"
```

---

### Task 4: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests pass (ignore 5 pre-existing failures in test_api_search.py)

- [ ] **Step 2: Verify no conflicts with DOI enrichment plan**

Confirm changes are limited to `publication.py` and `tests/test_save_publications.py`. No modifications to `database/researchers.py` or any other files the DOI enrichment plan touches.

Run: `git diff --name-only`
Expected: Only `publication.py` and `tests/test_save_publications.py`

- [ ] **Step 3: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: batch-check performance and error fixes complete"
```
