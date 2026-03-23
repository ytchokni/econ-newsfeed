# HTML Snapshot History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store compressed historical `raw_html` snapshots whenever a researcher page's text content changes, for auditability and debugging.

**Architecture:** New `html_snapshots` table stores zlib-compressed full `raw_html` alongside integrity hashes. Archiving is triggered inside `HTMLFetcher.save_text()` before the existing upsert. Each call uses its own auto-committing connection (matching existing `Database.execute_query()` pattern); the `UNIQUE KEY + INSERT IGNORE` prevents duplicates if a crash occurs between the archive INSERT and the upsert. Retrieval methods decompress and verify integrity. Zero impact on the pipeline hot path.

**Tech Stack:** Python `zlib`, `hashlib`, MySQL `MEDIUMBLOB`, `mysql-connector-python` connection pool

**Spec:** `docs/superpowers/specs/2026-03-23-html-snapshot-history-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `database/schema.py` | Modify | Add `html_snapshots` table definition and charset migration entry |
| `html_fetcher.py` | Modify | Add `archive_snapshot()`, `get_snapshot()`, `list_snapshots()`; update `save_text()` to call archive before upsert |
| `tests/test_html_fetcher.py` | Modify | Add test classes for archiving, retrieval, and edge cases |

---

### Task 1: Add `html_snapshots` table to schema

**Files:**
- Modify: `database/schema.py:66` (add to `_TABLE_DEFINITIONS` dict)
- Modify: `database/schema.py:404-413` (add to `_ALL_TABLES` list)

- [ ] **Step 1: Add table definition to `_TABLE_DEFINITIONS`**

In `database/schema.py`, add a new entry to `_TABLE_DEFINITIONS` after the `"html_content"` entry (after line 129):

```python
    "html_snapshots": """
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
    """,
```

- [ ] **Step 2: Add to `_ALL_TABLES` charset migration list**

In the `_ALL_TABLES` list inside `create_tables()` (around line 404-413), add `"html_snapshots"` after `"html_content"`:

```python
                    _ALL_TABLES = [
                        "researchers", "researcher_urls", "papers", "html_content",
                        "html_snapshots",
                        "authorship", "research_fields", "researcher_fields",
```

- [ ] **Step 3: Commit**

```bash
git add database/schema.py
git commit -m "feat: add html_snapshots table definition for snapshot history"
```

---

### Task 2: Add `archive_snapshot()` method with tests

**Files:**
- Modify: `html_fetcher.py:213` (add `archive_snapshot()` before `save_text()`)
- Modify: `tests/test_html_fetcher.py` (add `TestArchiveSnapshot` class)

- [ ] **Step 1: Write failing tests for `archive_snapshot()`**

Add the following test class to `tests/test_html_fetcher.py`:

```python
import zlib
import hashlib


class TestArchiveSnapshot:
    """Tests for HTMLFetcher.archive_snapshot()."""

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_archives_when_prior_row_exists(self, mock_execute, mock_fetch):
        """Should compress and store old raw_html when a prior row exists."""
        old_html = "<html>old content</html>"
        mock_fetch.return_value = {
            "raw_html": old_html,
            "content_hash": "old_text_hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        HTMLFetcher.archive_snapshot(url_id=1)

        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "INSERT IGNORE INTO html_snapshots" in call_args[0]
        params = call_args[1]
        assert params[0] == 1  # url_id
        assert params[1] == "old_text_hash"  # text_content_hash
        expected_html_hash = hashlib.sha256(old_html.encode("utf-8")).hexdigest()
        assert params[2] == expected_html_hash  # raw_html_hash
        assert zlib.decompress(params[3]).decode("utf-8") == old_html  # compressed blob
        assert params[4] == "2026-03-01 12:00:00"  # snapshot_at

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_no_archive_on_first_fetch(self, mock_execute, mock_fetch):
        """No prior row means no snapshot to archive."""
        mock_fetch.return_value = None

        HTMLFetcher.archive_snapshot(url_id=1)

        mock_execute.assert_not_called()

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_no_archive_when_raw_html_null(self, mock_execute, mock_fetch):
        """Legacy rows with raw_html=NULL should be skipped with a warning."""
        mock_fetch.return_value = {
            "raw_html": None,
            "content_hash": "some_hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        with patch("html_fetcher.logging.warning") as mock_warn:
            HTMLFetcher.archive_snapshot(url_id=1)

        mock_execute.assert_not_called()
        mock_warn.assert_called_once()
        assert "NULL" in mock_warn.call_args[0][0] or "null" in str(mock_warn.call_args).lower()

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query", side_effect=Exception("DB error"))
    def test_archive_failure_doesnt_raise(self, mock_execute, mock_fetch):
        """Archive errors should be logged, not raised."""
        mock_fetch.return_value = {
            "raw_html": "<html>old</html>",
            "content_hash": "hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        # Should not raise
        HTMLFetcher.archive_snapshot(url_id=1)

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_duplicate_archive_ignored(self, mock_execute, mock_fetch):
        """Calling archive twice with same content should not error (INSERT IGNORE)."""
        mock_fetch.return_value = {
            "raw_html": "<html>old</html>",
            "content_hash": "same_hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        HTMLFetcher.archive_snapshot(url_id=1)
        HTMLFetcher.archive_snapshot(url_id=1)

        # Both calls execute INSERT IGNORE — no errors
        assert mock_execute.call_count == 2
        for call in mock_execute.call_args_list:
            assert "INSERT IGNORE" in call[0][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_html_fetcher.py::TestArchiveSnapshot -v`
Expected: FAIL — `archive_snapshot` method does not exist yet.

- [ ] **Step 3: Implement `archive_snapshot()`**

Add this method to `HTMLFetcher` in `html_fetcher.py`, before `save_text()` (before line 213):

```python
    @staticmethod
    def archive_snapshot(url_id: int) -> None:
        """Archive the current raw_html for a URL into html_snapshots before overwriting.

        Called before save_text() upserts new content. Compresses the old raw_html
        with zlib and stores it with integrity hashes. Failures are logged, not raised.
        """
        try:
            row = Database.fetch_one(
                "SELECT raw_html, content_hash, timestamp FROM html_content WHERE url_id = %s",
                (url_id,),
            )
            if not row:
                return
            if row["raw_html"] is None:
                logging.warning("Skipping snapshot archive for URL ID %s: raw_html is NULL", url_id)
                return

            old_html = row["raw_html"]
            raw_html_hash = hashlib.sha256(old_html.encode("utf-8")).hexdigest()
            compressed = zlib.compress(old_html.encode("utf-8"))

            Database.execute_query(
                """INSERT IGNORE INTO html_snapshots
                   (url_id, text_content_hash, raw_html_hash, raw_html_compressed, snapshot_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (url_id, row["content_hash"], raw_html_hash, compressed, row["timestamp"]),
            )
            logging.info("Archived snapshot for URL ID %s (hash: %s)", url_id, row["content_hash"])
        except Exception as e:
            logging.warning("Failed to archive snapshot for URL ID %s: %s", url_id, e)
```

Also add `import zlib` at the top of `html_fetcher.py` with the other imports. (`hashlib` is already imported at line 8.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_html_fetcher.py::TestArchiveSnapshot -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "feat: add archive_snapshot() for HTML history preservation"
```

---

### Task 3: Wire `archive_snapshot()` into `save_text()` with transaction

**Files:**
- Modify: `html_fetcher.py:213-231` (update `save_text()` to call `archive_snapshot()` and use a transaction)

- [ ] **Step 1: Write a failing test for save_text archiving integration**

Add to the `TestArchiveSnapshot` class in `tests/test_html_fetcher.py`:

```python
    @patch("html_fetcher.Database.execute_query")
    def test_save_text_calls_archive_before_upsert(self, mock_execute):
        """save_text() should call archive_snapshot() before the upsert."""
        call_order = []

        def track_archive(url_id):
            call_order.append("archive")

        def track_execute(query, params=None):
            if "html_content" in query:
                call_order.append("upsert")
            return 0

        mock_execute.side_effect = track_execute

        with patch.object(HTMLFetcher, "archive_snapshot", side_effect=track_archive) as mock_archive:
            HTMLFetcher.save_text(url_id=1, text_content="new text", text_hash="new_hash", researcher_id=10, raw_html="<html>new</html>")

        mock_archive.assert_called_once_with(1)
        assert call_order == ["archive", "upsert"]

    @patch("html_fetcher.Database.execute_query")
    def test_save_text_still_saves_when_archive_fails(self, mock_execute):
        """save_text() should still upsert html_content even if archive_snapshot() fails internally."""
        with patch.object(HTMLFetcher, "archive_snapshot", side_effect=Exception("archive boom")):
            # archive_snapshot is called but raises — save_text should catch or not be affected
            # since archive_snapshot handles its own exceptions internally, this tests the integration
            HTMLFetcher.save_text(url_id=1, text_content="new text", text_hash="new_hash", researcher_id=10, raw_html="<html>new</html>")

        # The upsert into html_content should still have executed
        assert mock_execute.called
        assert "html_content" in mock_execute.call_args[0][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_html_fetcher.py::TestArchiveSnapshot::test_save_text_calls_archive_before_upsert -v`
Expected: FAIL — `save_text()` doesn't call `archive_snapshot()` yet.

- [ ] **Step 3: Update `save_text()` to call `archive_snapshot()` first**

Modify `save_text()` in `html_fetcher.py` to call `archive_snapshot()` at the start:

```python
    @staticmethod
    def save_text(url_id: int, text_content: str, text_hash: str, researcher_id: int, raw_html=None) -> None:
        """
        Save pre-extracted text content and hash to the database using upsert.
        Also stores raw_html if provided. Archives the old version before overwriting.
        """
        try:
            HTMLFetcher.archive_snapshot(url_id)
        except Exception as e:
            logging.warning("Unexpected error in archive_snapshot for URL ID %s: %s", url_id, e)

        query = """
            INSERT INTO html_content (url_id, content, content_hash, timestamp, researcher_id, raw_html)
            VALUES (%s, %s, %s, %s, %s, %s) AS new_row
            ON DUPLICATE KEY UPDATE
                content = new_row.content,
                content_hash = new_row.content_hash,
                timestamp = new_row.timestamp,
                raw_html = new_row.raw_html
        """
        try:
            Database.execute_query(query, (url_id, text_content, text_hash, datetime.now(timezone.utc), researcher_id, raw_html))
            logging.info(f"Text content saved for URL ID: {url_id} (Researcher ID: {researcher_id})")
        except Exception as e:
            logging.error("Error saving text content for URL ID %s: %s", url_id, type(e).__name__)
```

**Note on transactions:** The spec originally called for wrapping archive + upsert in a single transaction. However, the current `Database.execute_query()` gets its own connection and auto-commits. Since `archive_snapshot()` handles its own exceptions internally, `save_text()` wraps the call in an additional try/except as a safety net, and the `UNIQUE KEY + INSERT IGNORE` prevents duplicates on crash recovery, the simpler two-call approach is safe here. The worst case (crash between archive and upsert) produces a harmless duplicate snapshot entry that the unique key prevents. The spec should be updated to reflect this decision (see Task 5).

- [ ] **Step 4: Run all tests to verify they pass**

Run: `poetry run pytest tests/test_html_fetcher.py -v`
Expected: All tests PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "feat: wire archive_snapshot into save_text for automatic history"
```

---

### Task 4: Add `get_snapshot()` and `list_snapshots()` retrieval methods with tests

**Files:**
- Modify: `html_fetcher.py` (add methods after `archive_snapshot()`)
- Modify: `tests/test_html_fetcher.py` (add `TestSnapshotRetrieval` class)

- [ ] **Step 1: Write failing tests for retrieval**

Add to `tests/test_html_fetcher.py`:

```python
class TestSnapshotRetrieval:
    """Tests for HTMLFetcher.get_snapshot() and list_snapshots()."""

    @patch("html_fetcher.Database.fetch_one")
    def test_get_snapshot_decompresses_and_verifies(self, mock_fetch):
        """Should decompress and return raw HTML after integrity check."""
        original_html = "<html><body>Test page</body></html>"
        compressed = zlib.compress(original_html.encode("utf-8"))
        html_hash = hashlib.sha256(original_html.encode("utf-8")).hexdigest()

        mock_fetch.return_value = {
            "raw_html_compressed": compressed,
            "raw_html_hash": html_hash,
        }

        result = HTMLFetcher.get_snapshot(url_id=1, snapshot_id=42)
        assert result == original_html

    @patch("html_fetcher.Database.fetch_one")
    def test_get_snapshot_integrity_failure(self, mock_fetch):
        """Should raise ValueError when decompressed HTML doesn't match hash."""
        original_html = "<html>original</html>"
        compressed = zlib.compress(original_html.encode("utf-8"))

        mock_fetch.return_value = {
            "raw_html_compressed": compressed,
            "raw_html_hash": "wrong_hash_value",
        }

        with pytest.raises(ValueError, match="[Ii]ntegrity"):
            HTMLFetcher.get_snapshot(url_id=1, snapshot_id=42)

    @patch("html_fetcher.Database.fetch_one")
    def test_get_snapshot_not_found(self, mock_fetch):
        """Should return None when snapshot doesn't exist."""
        mock_fetch.return_value = None
        result = HTMLFetcher.get_snapshot(url_id=1, snapshot_id=999)
        assert result is None

    @patch("html_fetcher.Database.fetch_all")
    def test_list_snapshots(self, mock_fetch):
        """Should return snapshots ordered by snapshot_at DESC."""
        mock_fetch.return_value = [
            {"id": 2, "text_content_hash": "hash2", "raw_html_hash": "rhash2", "snapshot_at": "2026-03-15"},
            {"id": 1, "text_content_hash": "hash1", "raw_html_hash": "rhash1", "snapshot_at": "2026-03-01"},
        ]

        result = HTMLFetcher.list_snapshots(url_id=1)
        assert len(result) == 2
        assert result[0]["id"] == 2

    @patch("html_fetcher.Database.fetch_all")
    def test_list_snapshots_empty(self, mock_fetch):
        """Should return empty list when no snapshots exist."""
        mock_fetch.return_value = []
        result = HTMLFetcher.list_snapshots(url_id=1)
        assert result == []
```

Also add `import pytest` at the top of the test file if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_html_fetcher.py::TestSnapshotRetrieval -v`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Implement `get_snapshot()` and `list_snapshots()`**

Add these methods to `HTMLFetcher` in `html_fetcher.py`, after `archive_snapshot()`:

```python
    @staticmethod
    def get_snapshot(url_id: int, snapshot_id: int) -> str | None:
        """Retrieve and decompress a historical raw_html snapshot.

        Returns the decompressed HTML string, or None if not found.
        Raises ValueError if integrity check fails.
        """
        row = Database.fetch_one(
            "SELECT raw_html_compressed, raw_html_hash FROM html_snapshots WHERE id = %s AND url_id = %s",
            (snapshot_id, url_id),
        )
        if not row:
            return None

        raw_bytes = zlib.decompress(row["raw_html_compressed"])
        actual_hash = hashlib.sha256(raw_bytes).hexdigest()
        if actual_hash != row["raw_html_hash"]:
            raise ValueError(
                f"Integrity check failed for snapshot {snapshot_id}: "
                f"expected {row['raw_html_hash']}, got {actual_hash}"
            )
        return raw_bytes.decode("utf-8")

    @staticmethod
    def list_snapshots(url_id: int) -> list[dict]:
        """List all snapshots for a URL, ordered by most recent first."""
        return Database.fetch_all(
            "SELECT id, text_content_hash, raw_html_hash, snapshot_at "
            "FROM html_snapshots WHERE url_id = %s ORDER BY snapshot_at DESC",
            (url_id,),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_html_fetcher.py::TestSnapshotRetrieval -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest tests/test_html_fetcher.py -v`
Expected: All tests PASS (existing + archiving + retrieval).

- [ ] **Step 6: Commit**

```bash
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "feat: add get_snapshot() and list_snapshots() for history retrieval"
```

---

### Task 5: Update spec, run full test suite, and verify

**Files:**
- Modify: `docs/superpowers/specs/2026-03-23-html-snapshot-history-design.md` (update transaction section)

- [ ] **Step 1: Update spec to document non-transactional approach**

In `docs/superpowers/specs/2026-03-23-html-snapshot-history-design.md`, replace:

> Steps 2 and 3 are wrapped in a single database transaction to ensure atomicity.

With:

> Steps 2 and 3 use separate auto-committing connections (matching the existing `Database.execute_query()` pattern). The `UNIQUE KEY (url_id, text_content_hash)` with `INSERT IGNORE` prevents duplicate snapshots if a crash occurs between archive and upsert, making a single transaction unnecessary. `save_text()` wraps the `archive_snapshot()` call in a try/except as an additional safety net.

- [ ] **Step 2: Run all Python tests**

Run: `poetry run pytest -v`
Expected: All tests PASS. No regressions — existing tests should be unaffected since `archive_snapshot()` is called from `save_text()` but all existing tests mock `Database.execute_query` and `Database.fetch_one`.

- [ ] **Step 3: Verify schema applies cleanly**

Run: `poetry run python -c "from database.schema import _TABLE_DEFINITIONS; assert 'html_snapshots' in _TABLE_DEFINITIONS; print('OK: html_snapshots in _TABLE_DEFINITIONS')"`
Expected: `OK: html_snapshots in _TABLE_DEFINITIONS`

- [ ] **Step 4: Commit spec update and any cleanup**

```bash
git add docs/superpowers/specs/2026-03-23-html-snapshot-history-design.md
git commit -m "docs: update spec to document non-transactional archive approach"
```
