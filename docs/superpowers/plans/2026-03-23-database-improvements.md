# Database Structure Improvements Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve database performance, data integrity, and maintainability through targeted schema changes.

**Architecture:** Add missing indexes for key query patterns, introduce FULLTEXT search, enforce FK constraints on `llm_usage`, implement a versioned migration system, and add snapshot archival. Changes are idempotent and backwards-compatible — no data loss.

**Tech Stack:** MySQL 8+, Python (mysql-connector-python), pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `database/migrations.py` | Create | Versioned migration runner with `schema_migrations` tracking table |
| `database/schema.py` | Modify | Replace ad-hoc migrations with calls to `migrations.py`; add new table definitions |
| `database/connection.py` | Modify (minor) | Add pool health-check config |
| `database/__init__.py` | Modify | Export new migration functions |
| `database/papers.py` | Modify | Update `get_unenriched_papers` to use new index |
| `api.py` | Modify | Use FULLTEXT `MATCH ... AGAINST` for search queries |
| `html_fetcher.py` | Modify | Rename `timestamp` references to `fetched_at` |
| `scheduler.py` | Modify | Add snapshot pruning call after scrape |
| `tests/test_migrations.py` | Create | Tests for versioned migration system |
| `tests/test_database_indexes.py` | Create | Tests verifying indexes exist |
| `tests/test_snapshot_archival.py` | Create | Tests for snapshot pruning |

---

## Task 1: Add Missing B-tree Indexes

**Files:**
- Modify: `database/schema.py` (add indexes in `_TABLE_DEFINITIONS` and migration block)
- Create: `tests/test_database_indexes.py`

These indexes address the most common unindexed query patterns found in `api.py` and `scheduler.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database_indexes.py
"""Verify critical indexes exist on key tables."""
import os
import sys
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "root")
os.environ.setdefault("DB_PASSWORD", "")
os.environ.setdefault("DB_NAME", "test_econ")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SCRAPE_API_KEY", "test1234567890ab")

import pytest
from unittest.mock import patch, MagicMock

# We test that the CREATE TABLE DDL contains the expected INDEX definitions
from database.schema import _TABLE_DEFINITIONS


class TestPapersIndexes:
    def test_doi_index_exists(self):
        ddl = _TABLE_DEFINITIONS["papers"]
        assert "idx_doi" in ddl, "papers table should have idx_doi index"

    def test_openalex_id_index_exists(self):
        ddl = _TABLE_DEFINITIONS["papers"]
        assert "idx_openalex_id" in ddl, "papers table should have idx_openalex_id index"

    def test_fulltext_index_exists(self):
        ddl = _TABLE_DEFINITIONS["papers"]
        assert "FULLTEXT" in ddl, "papers table should have a FULLTEXT index"


class TestFeedEventsIndexes:
    def test_composite_created_at_paper_id_index(self):
        ddl = _TABLE_DEFINITIONS["feed_events"]
        assert "idx_created_at_paper" in ddl, "feed_events should have composite (created_at, paper_id) index"


class TestLlmUsageIndexes:
    def test_researcher_id_index(self):
        ddl = _TABLE_DEFINITIONS["llm_usage"]
        assert "idx_researcher_id" in ddl, "llm_usage should have idx_researcher_id index"

    def test_batch_job_id_index(self):
        ddl = _TABLE_DEFINITIONS["llm_usage"]
        assert "idx_batch_job_id" in ddl, "llm_usage should have idx_batch_job_id index"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_database_indexes.py -v`
Expected: FAIL — indexes don't exist in DDL yet

- [ ] **Step 3: Add indexes to table definitions**

In `database/schema.py`, update `_TABLE_DEFINITIONS`:

**papers** — add after `INDEX idx_is_seed (is_seed)`:
```python
            INDEX idx_doi (doi),
            INDEX idx_openalex_id (openalex_id),
            FULLTEXT idx_ft_title_abstract (title, abstract)
```

**feed_events** — add after `INDEX idx_event_type (event_type)`:
```python
            INDEX idx_created_at_paper (created_at DESC, paper_id)
```

**llm_usage** — add after `INDEX idx_scrape_log (scrape_log_id)`:
```python
            INDEX idx_researcher_id (researcher_id),
            INDEX idx_batch_job_id (batch_job_id)
```

Also add ALTER TABLE migration statements in the migration block for existing databases:

```python
_index_migrations = [
    ("papers", "idx_doi", "(doi)"),
    ("papers", "idx_openalex_id", "(openalex_id)"),
    ("llm_usage", "idx_researcher_id", "(researcher_id)"),
    ("llm_usage", "idx_batch_job_id", "(batch_job_id)"),
    ("feed_events", "idx_created_at_paper", "(created_at DESC, paper_id)"),
]
for table, idx_name, cols in _index_migrations:
    try:
        cursor.execute(f"ALTER TABLE `{table}` ADD INDEX `{idx_name}` {cols}")
        conn.commit()
    except Exception as e:
        if getattr(e, 'errno', None) != 1061:  # Duplicate key name
            logging.warning("Migration: index %s.%s: %s", table, idx_name, e)

# FULLTEXT index (separate — different syntax)
try:
    cursor.execute(
        "ALTER TABLE papers ADD FULLTEXT INDEX idx_ft_title_abstract (title, abstract)"
    )
    conn.commit()
except Exception as e:
    if getattr(e, 'errno', None) != 1061:
        logging.warning("Migration: FULLTEXT index: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_database_indexes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/schema.py tests/test_database_indexes.py
git commit -m "feat: add missing indexes for papers, feed_events, llm_usage"
```

---

## Task 2: Use FULLTEXT Search in API

**Files:**
- Modify: `api.py:547-551` (search clause in `list_publications`)
- Modify: `tests/test_api_search.py` (update existing tests + add new test)

Replace `LIKE '%term%'` with `MATCH ... AGAINST` for the papers search endpoint. The researchers endpoint stays with LIKE since name searches work well with prefix matching.

**Important:** MySQL FULLTEXT in `BOOLEAN MODE` ignores words shorter than `ft_min_word_len` (default 4 for InnoDB) and common stop words. For short search terms (< 4 chars), fall back to LIKE to avoid silent zero-result regressions on queries like "GDP", "tax", "EU".

- [ ] **Step 1: Update existing tests that will break**

The existing `test_search_passes_like_to_sql` (line 36-47) asserts `"p.title LIKE"` in the SQL. This test must be updated to expect FULLTEXT for long terms. Also update `test_empty_search_ignored` (line 73-83) to check for `MATCH` instead of `LIKE`.

In `tests/test_api_search.py`, replace `test_search_passes_like_to_sql`:

```python
    def test_search_passes_fulltext_to_sql(self, client):
        """Search param generates a MATCH AGAINST clause for terms >= 4 chars."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 0}) as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            client.get("/api/publications?search=monetary+policy")

        count_sql = mock_one.call_args[0][0]
        assert "MATCH" in count_sql, "Long search terms should use FULLTEXT"

    def test_short_search_falls_back_to_like(self, client):
        """Search terms < 4 chars fall back to LIKE (FULLTEXT ignores short words)."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 0}) as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            client.get("/api/publications?search=GDP")

        count_sql = mock_one.call_args[0][0]
        assert "LIKE" in count_sql, "Short search terms should use LIKE fallback"
```

Update `test_empty_search_ignored` assertion:

```python
        # Neither LIKE nor MATCH should be present for empty search
        assert "LIKE" not in count_sql
        assert "MATCH" not in count_sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_api_search.py -v`
Expected: FAIL — `test_search_passes_fulltext_to_sql` fails (LIKE still in SQL), `test_short_search_falls_back_to_like` may pass or fail

- [ ] **Step 3: Update search clause in api.py**

In `api.py`, replace the LIKE-based search (around line 547-551):

```python
# Before:
# conditions.append("(p.title LIKE %s ESCAPE '\\\\' OR p.abstract LIKE %s ESCAPE '\\\\')")
# escaped = f"%{_escape_like(search_term)}%"
# params.extend([escaped, escaped])

# After — FULLTEXT for terms >= 4 chars, LIKE fallback for short terms:
if len(search_term) >= 4:
    conditions.append("MATCH(p.title, p.abstract) AGAINST(%s IN BOOLEAN MODE)")
    params.append(search_term)
else:
    conditions.append("(p.title LIKE %s ESCAPE '\\\\' OR p.abstract LIKE %s ESCAPE '\\\\')")
    escaped = f"%{_escape_like(search_term)}%"
    params.extend([escaped, escaped])
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_api_search.py -v`
Expected: PASS (all tests including the updated ones)

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_api_search.py
git commit -m "feat: use FULLTEXT search for publications, LIKE fallback for short terms"
```

---

## Task 3: Add FK Constraints to llm_usage

**Files:**
- Modify: `database/schema.py` (llm_usage table definition + migration)
- Modify: `tests/test_database_indexes.py` (add FK verification)

The `llm_usage` table references `researchers.id`, `scrape_log.id`, and `batch_jobs.id` but has no FK constraints. Orphaned rows accumulate when parent rows are deleted.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database_indexes.py`:

```python
class TestLlmUsageForeignKeys:
    def test_researcher_fk_in_ddl(self):
        ddl = _TABLE_DEFINITIONS["llm_usage"]
        assert "FOREIGN KEY (researcher_id) REFERENCES researchers(id)" in ddl

    def test_scrape_log_fk_in_ddl(self):
        ddl = _TABLE_DEFINITIONS["llm_usage"]
        assert "FOREIGN KEY (scrape_log_id) REFERENCES scrape_log(id)" in ddl

    def test_batch_job_fk_in_ddl(self):
        ddl = _TABLE_DEFINITIONS["llm_usage"]
        assert "FOREIGN KEY (batch_job_id) REFERENCES batch_jobs(id)" in ddl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_database_indexes.py::TestLlmUsageForeignKeys -v`
Expected: FAIL

- [ ] **Step 3: Add FK constraints to llm_usage DDL**

In `database/schema.py`, update the `llm_usage` table definition to add FKs with `ON DELETE SET NULL` (since these columns are nullable — when a researcher or scrape_log is deleted, we keep the usage record but null out the reference):

```python
    "llm_usage": """
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INT AUTO_INCREMENT PRIMARY KEY,
            called_at DATETIME NOT NULL,
            call_type ENUM('publication_extraction','description_extraction','researcher_disambiguation','jel_classification') NOT NULL,
            model VARCHAR(100) NOT NULL,
            prompt_tokens INT NOT NULL DEFAULT 0,
            completion_tokens INT NOT NULL DEFAULT 0,
            total_tokens INT NOT NULL DEFAULT 0,
            estimated_cost_usd DECIMAL(10,6) DEFAULT NULL,
            is_batch BOOLEAN NOT NULL DEFAULT FALSE,
            context_url VARCHAR(2048) DEFAULT NULL,
            researcher_id INT DEFAULT NULL,
            scrape_log_id INT DEFAULT NULL,
            batch_job_id INT DEFAULT NULL,
            INDEX idx_called_at (called_at),
            INDEX idx_call_type (call_type),
            INDEX idx_scrape_log (scrape_log_id),
            INDEX idx_researcher_id (researcher_id),
            INDEX idx_batch_job_id (batch_job_id),
            FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE SET NULL,
            FOREIGN KEY (scrape_log_id) REFERENCES scrape_log(id) ON DELETE SET NULL,
            FOREIGN KEY (batch_job_id) REFERENCES batch_jobs(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
```

Add migration for existing databases in the migration block:

```python
_llm_fks = [
    ("llm_usage", "researcher_id", "researchers", "id"),
    ("llm_usage", "scrape_log_id", "scrape_log", "id"),
    ("llm_usage", "batch_job_id", "batch_jobs", "id"),
]
for table, col, ref_table, ref_col in _llm_fks:
    try:
        # Check if FK already exists
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND COLUMN_NAME = %s AND REFERENCED_TABLE_NAME = %s",
            (table, col, ref_table),
        )
        if cursor.fetchone()[0] == 0:
            # Clean up orphaned references first — ALTER TABLE ADD FK will fail
            # if any row references a deleted parent (error 1452)
            cursor.execute(
                f"UPDATE `{table}` SET `{col}` = NULL "
                f"WHERE `{col}` IS NOT NULL AND `{col}` NOT IN "
                f"(SELECT `{ref_col}` FROM `{ref_table}`)"
            )
            cursor.execute(
                f"ALTER TABLE `{table}` ADD FOREIGN KEY (`{col}`) "
                f"REFERENCES `{ref_table}`(`{ref_col}`) ON DELETE SET NULL"
            )
            conn.commit()
    except Exception as e:
        logging.warning("Migration: FK %s.%s -> %s: %s", table, col, ref_table, e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_database_indexes.py::TestLlmUsageForeignKeys -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/schema.py tests/test_database_indexes.py
git commit -m "feat: add FK constraints on llm_usage for data integrity"
```

---

## Task 4: Implement Versioned Migration System

**Files:**
- Create: `database/migrations.py`
- Modify: `database/schema.py` (delegate ad-hoc migrations to versioned system)
- Modify: `database/__init__.py` (export new function)
- Create: `tests/test_migrations.py`

Replace the growing block of try/except migrations with a version-tracked system. Each migration has a unique version string and runs at most once.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrations.py
"""Tests for the versioned migration system."""
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "root")
os.environ.setdefault("DB_PASSWORD", "")
os.environ.setdefault("DB_NAME", "test_econ")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SCRAPE_API_KEY", "test1234567890ab")

import pytest
from unittest.mock import patch, MagicMock, call


def test_migrations_module_exists():
    from database import migrations
    assert hasattr(migrations, "run_migrations")
    assert hasattr(migrations, "MIGRATIONS")


def test_migration_has_version_and_callable():
    from database.migrations import MIGRATIONS
    assert isinstance(MIGRATIONS, list)
    for m in MIGRATIONS:
        assert "version" in m, f"Migration missing 'version': {m}"
        assert "description" in m, f"Migration missing 'description': {m}"
        assert callable(m.get("up")), f"Migration 'up' not callable: {m}"


def test_run_migrations_creates_tracking_table():
    """run_migrations should CREATE TABLE schema_migrations if not exists."""
    from database.migrations import run_migrations
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []  # no migrations applied yet
    mock_cursor.fetchone.return_value = (1,)  # got lock

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("database.migrations.get_connection", return_value=mock_conn):
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        run_migrations()

    # Check that CREATE TABLE schema_migrations was executed
    create_calls = [c for c in mock_cursor.execute.call_args_list
                    if "schema_migrations" in str(c) and "CREATE" in str(c)]
    assert len(create_calls) > 0, "Should create schema_migrations table"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_migrations.py -v`
Expected: FAIL — `database.migrations` module doesn't exist

- [ ] **Step 3: Create database/migrations.py**

```python
"""Versioned migration system with tracking table.

Each migration runs at most once. Applied versions are recorded in
the `schema_migrations` table so they survive restarts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from database.connection import get_connection

# Each migration is a dict with:
#   version: str  — unique, sortable identifier (YYYY-MM-DD-NN or similar)
#   description: str — human-readable summary
#   up: callable(cursor, conn) — the migration function
MIGRATIONS: list[dict] = [
    # Future migrations go here. Example:
    # {
    #     "version": "2026-03-23-01",
    #     "description": "Add idx_doi to papers table",
    #     "up": lambda cursor, conn: cursor.execute(
    #         "ALTER TABLE papers ADD INDEX idx_doi (doi)"
    #     ),
    # },
]


def run_migrations() -> None:
    """Run all pending migrations under an advisory lock."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # Create tracking table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(100) PRIMARY KEY,
                    description VARCHAR(500),
                    applied_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            conn.commit()

            # Advisory lock to prevent concurrent migration runs
            cursor.execute("SELECT GET_LOCK('econ_migrations_v2', 10)")
            got_lock = cursor.fetchone()[0]
            if got_lock != 1:
                logging.info("Skipping migrations — another process holds the lock")
                return

            try:
                # Get already-applied versions
                cursor.execute("SELECT version FROM schema_migrations")
                applied = {row[0] for row in cursor.fetchall()}

                for migration in MIGRATIONS:
                    version = migration["version"]
                    if version in applied:
                        continue
                    try:
                        logging.info("Running migration %s: %s", version, migration["description"])
                        migration["up"](cursor, conn)
                        cursor.execute(
                            "INSERT INTO schema_migrations (version, description, applied_at) "
                            "VALUES (%s, %s, %s)",
                            (version, migration["description"], datetime.now(timezone.utc)),
                        )
                        conn.commit()
                        logging.info("Migration %s applied successfully", version)
                    except Exception as e:
                        logging.error("Migration %s failed: %s", version, e)
                        conn.rollback()
                        raise
            finally:
                cursor.execute("SELECT RELEASE_LOCK('econ_migrations_v2')")
                cursor.fetchone()
```

- [ ] **Step 4: Export from __init__.py**

Add to `database/__init__.py`:

```python
from database.migrations import run_migrations as _run_migrations
```

And in the `Database` class:
```python
    run_migrations = staticmethod(_run_migrations)
```

- [ ] **Step 5: Call run_migrations from create_tables**

In `database/schema.py`, at the end of `create_tables()`, after `seed_jel_codes()`:

```python
    from database.migrations import run_migrations
    run_migrations()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add database/migrations.py database/__init__.py database/schema.py tests/test_migrations.py
git commit -m "feat: implement versioned migration system with schema_migrations tracking"
```

---

## Task 5: Rename html_content.timestamp to fetched_at

**Files:**
- Modify: `database/schema.py` (table definition + migration)
- Modify: `html_fetcher.py:219,253` (references to `timestamp`)
- Modify: `tests/test_html_fetcher.py` (update assertions if column name appears)

The column name `timestamp` is ambiguous — it could mean creation time, last modified time, or fetch time. Renaming to `fetched_at` makes the intent clear and matches the naming convention used elsewhere (`discovered_at`, `extracted_at`, `scraped_at`).

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_database_indexes.py
class TestHtmlContentSchema:
    def test_uses_fetched_at_not_timestamp(self):
        ddl = _TABLE_DEFINITIONS["html_content"]
        assert "fetched_at" in ddl, "html_content should use 'fetched_at' column name"
        # 'timestamp' should not appear as a standalone column name
        # (it can appear in index names or comments)
        lines = [l.strip() for l in ddl.split("\n") if l.strip() and not l.strip().startswith("INDEX") and not l.strip().startswith("FOREIGN") and not l.strip().startswith("UNIQUE")]
        timestamp_as_column = any("timestamp DATETIME" in l or "timestamp," in l for l in lines)
        assert not timestamp_as_column, "html_content should not use 'timestamp' as column name"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_database_indexes.py::TestHtmlContentSchema -v`
Expected: FAIL

- [ ] **Step 3: Update html_content table definition**

In `database/schema.py`, update `_TABLE_DEFINITIONS["html_content"]`:

Replace `timestamp DATETIME` with `fetched_at DATETIME` and update the index:
```python
    "html_content": """
        CREATE TABLE IF NOT EXISTS html_content (
            id INT AUTO_INCREMENT PRIMARY KEY,
            url_id INT NOT NULL,
            content MEDIUMTEXT,
            raw_html MEDIUMTEXT DEFAULT NULL,
            content_hash VARCHAR(64),
            fetched_at DATETIME,
            researcher_id INT,
            extracted_at DATETIME,
            extracted_hash VARCHAR(64),
            UNIQUE KEY uq_url_id (url_id),
            INDEX idx_url_id_ts (url_id, fetched_at),
            FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
            FOREIGN KEY (url_id) REFERENCES researcher_urls(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
```

Add migration for existing databases:
```python
# Rename html_content.timestamp → html_content.fetched_at
try:
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'html_content' "
        "AND COLUMN_NAME = 'timestamp'"
    )
    if cursor.fetchone()[0] > 0:
        cursor.execute("ALTER TABLE html_content RENAME COLUMN `timestamp` TO fetched_at")
        conn.commit()
except Exception as e:
    logging.warning("Migration: html_content.timestamp rename: %s", e)
```

- [ ] **Step 4: Update all html_fetcher.py references to `timestamp`**

There are 5 occurrences to update (search: `grep -n 'timestamp' html_fetcher.py` — skip the rate-limiting comments):

1. **Line ~219** — INSERT column name in `save_text`:
   `timestamp` → `fetched_at`

2. **Line ~224** — ON DUPLICATE KEY UPDATE clause in `save_text`:
   `timestamp = new_row.timestamp` → `fetched_at = new_row.fetched_at`

3. **Line ~253** — SELECT column in `_was_fetched_recently`:
   `"SELECT timestamp FROM ...` → `"SELECT fetched_at FROM ...`

4. **Line ~255** — dict key access in `_was_fetched_recently`:
   `result['timestamp']` → `result['fetched_at']`

5. **Line ~259** — dict key access in `_was_fetched_recently`:
   `ts = result['timestamp']` → `ts = result['fetched_at']`

Full replacement for `save_text` query:
```python
query = """
    INSERT INTO html_content (url_id, content, content_hash, fetched_at, researcher_id, raw_html)
    VALUES (%s, %s, %s, %s, %s, %s) AS new_row
    ON DUPLICATE KEY UPDATE
        content = new_row.content,
        content_hash = new_row.content_hash,
        fetched_at = new_row.fetched_at,
        raw_html = new_row.raw_html
"""
```

Full replacement for `_was_fetched_recently`:
```python
result = Database.fetch_one(
    "SELECT fetched_at FROM html_content WHERE url_id = %s", (url_id,)
)
if not result or not result['fetched_at']:
    return False
ts = result['fetched_at']
```

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/test_database_indexes.py::TestHtmlContentSchema tests/test_html_fetcher.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add database/schema.py html_fetcher.py tests/test_database_indexes.py
git commit -m "refactor: rename html_content.timestamp to fetched_at for clarity"
```

---

## Task 6: Add Snapshot Pruning

**Files:**
- Create: `database/snapshots.py:~140` (add `prune_old_snapshots` function)
- Modify: `database/__init__.py` (export new function)
- Modify: `scheduler.py:~270` (call pruning after scrape)
- Create: `tests/test_snapshot_archival.py`

Snapshot tables grow unbounded. Add a pruning function that keeps the N most recent snapshots per entity and deletes older ones. Default: keep 10 snapshots per researcher/paper.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snapshot_archival.py
"""Tests for snapshot pruning logic."""
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "root")
os.environ.setdefault("DB_PASSWORD", "")
os.environ.setdefault("DB_NAME", "test_econ")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SCRAPE_API_KEY", "test1234567890ab")

import pytest
from unittest.mock import patch, MagicMock


def test_prune_function_exists():
    from database.snapshots import prune_old_snapshots
    assert callable(prune_old_snapshots)


def test_prune_executes_delete_queries():
    """prune_old_snapshots should delete old snapshots beyond the keep limit."""
    from database.snapshots import prune_old_snapshots

    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("database.snapshots.get_connection", return_value=mock_conn):
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        result = prune_old_snapshots(keep=5)

    # Should execute DELETE for both researcher_snapshots and paper_snapshots
    delete_calls = [c for c in mock_cursor.execute.call_args_list if "DELETE" in str(c)]
    assert len(delete_calls) == 2, "Should delete from both snapshot tables"
    assert "researcher_snapshots" in str(delete_calls[0]), "First delete should target researcher_snapshots"
    assert "paper_snapshots" in str(delete_calls[1]), "Second delete should target paper_snapshots"


def test_prune_default_keep_is_10():
    """Default keep parameter should be 10."""
    import inspect
    from database.snapshots import prune_old_snapshots
    sig = inspect.signature(prune_old_snapshots)
    assert sig.parameters["keep"].default == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_snapshot_archival.py -v`
Expected: FAIL — `prune_old_snapshots` doesn't exist

- [ ] **Step 3: Implement prune_old_snapshots**

Add to `database/snapshots.py`:

```python
def prune_old_snapshots(keep: int = 10) -> dict[str, int]:
    """Delete old snapshots, keeping only the `keep` most recent per entity.

    Uses ROW_NUMBER() window function (MySQL 8+) for O(n log n) performance
    instead of correlated subqueries which would be O(n^2).

    Returns dict with keys 'researcher_snapshots' and 'paper_snapshots',
    values are number of rows deleted from each table.
    """
    deleted = {}
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # Delete researcher snapshots beyond keep limit
            cursor.execute(
                """DELETE rs FROM researcher_snapshots rs
                   INNER JOIN (
                       SELECT id FROM (
                           SELECT id, ROW_NUMBER() OVER (
                               PARTITION BY researcher_id ORDER BY scraped_at DESC
                           ) AS rn
                           FROM researcher_snapshots
                       ) ranked
                       WHERE rn > %s
                   ) old ON rs.id = old.id""",
                (keep,),
            )
            deleted["researcher_snapshots"] = cursor.rowcount

            # Delete paper snapshots beyond keep limit
            cursor.execute(
                """DELETE ps FROM paper_snapshots ps
                   INNER JOIN (
                       SELECT id FROM (
                           SELECT id, ROW_NUMBER() OVER (
                               PARTITION BY paper_id ORDER BY scraped_at DESC
                           ) AS rn
                           FROM paper_snapshots
                       ) ranked
                       WHERE rn > %s
                   ) old ON ps.id = old.id""",
                (keep,),
            )
            deleted["paper_snapshots"] = cursor.rowcount

            conn.commit()

    total = sum(deleted.values())
    if total > 0:
        logging.info("Pruned snapshots: %s", deleted)
    return deleted
```

- [ ] **Step 4: Export from __init__.py**

In `database/__init__.py`, add import:
```python
from database.snapshots import prune_old_snapshots as _prune_old_snapshots
```

In the `Database` class:
```python
    prune_old_snapshots = staticmethod(_prune_old_snapshots)
```

- [ ] **Step 5: Call from scheduler after scrape**

In `scheduler.py`, add after the OpenAlex enrichment block (~line 275):

```python
    # Prune old snapshots to prevent unbounded growth
    try:
        from database import Database
        pruned = Database.prune_old_snapshots()
        logger.info(f"Snapshot pruning: {pruned}")
    except Exception as e:
        logger.error("Snapshot pruning failed: %s", e)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/test_snapshot_archival.py -v`
Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add database/snapshots.py database/__init__.py scheduler.py tests/test_snapshot_archival.py
git commit -m "feat: add snapshot pruning to prevent unbounded table growth"
```

---

## Task 7: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests pass

- [ ] **Step 2: Run TypeScript checks**

Run: `cd app && npx tsc --noEmit`
Expected: No errors (frontend unchanged)

- [ ] **Step 3: Verify schema creates cleanly on fresh database**

Run: `make reset-db && make seed`
Expected: All tables created, migrations applied, seeds populated

- [ ] **Step 4: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: final verification pass"
```
