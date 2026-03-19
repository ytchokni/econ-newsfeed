# Database Schema Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix data integrity gaps (missing ON DELETE CASCADE), improve column naming clarity (`papers.url` → `source_url`, `papers.timestamp` → `discovered_at`), add missing indexes and utf8mb4 charset across all tables.

**Architecture:** All schema changes go through the existing advisory-locked migration block in `database.py:create_tables()`. CREATE TABLE statements are updated for fresh installs; migrations handle existing databases. SQL queries in `api.py` and `publication.py` are updated to match renamed columns. Frontend is unaffected — the API already transforms column names at the response layer.

**Tech Stack:** Python 3.12, MySQL 8.0+, mysql-connector-python, pytest

---

## File Structure

| File | Changes |
|------|---------|
| `database.py:99-360` | CREATE TABLE definitions + migration block |
| `api.py:408,442,732,737` | SELECT queries referencing `papers.url` and `papers.timestamp` |
| `publication.py:91,279` | INSERT + SELECT queries referencing `papers.url` and `papers.timestamp` |
| `tests/test_api_publications.py` | Column comment updates |
| `tests/test_api_filters.py` | Column comment updates |
| `tests/test_api_integration.py` | Column comment updates |
| `tests/test_api_response_shapes.py` | Column comment updates |

---

## Phase A: Non-Breaking Schema Improvements (no query changes)

### Task 1: Add ON DELETE CASCADE to researcher_urls, html_content, authorship

**Files:**
- Modify: `database.py:104-176` (CREATE TABLE statements)
- Modify: `database.py:310-360` (migration block)

- [ ] **Step 1: Write a migration helper function**

Add above `create_tables()` (around line 98):

```python
def _migrate_fk_cascade(cursor, table, column, ref_table, ref_column):
    """Ensure FK on (table.column → ref_table.ref_column) has ON DELETE CASCADE.
    Idempotent: skips if CASCADE already in place."""
    cursor.execute(
        "SELECT rc.CONSTRAINT_NAME, rc.DELETE_RULE "
        "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
        "JOIN information_schema.KEY_COLUMN_USAGE kcu "
        "  ON rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
        "  AND rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA "
        "WHERE kcu.TABLE_SCHEMA = DATABASE() AND kcu.TABLE_NAME = %s "
        "AND kcu.COLUMN_NAME = %s AND kcu.REFERENCED_TABLE_NAME = %s",
        (table, column, ref_table),
    )
    row = cursor.fetchone()
    if row and row[1] == 'CASCADE':
        return  # Already correct

    # Drop existing FK(s) for this column
    cursor.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND COLUMN_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
        (table, column),
    )
    for (name,) in cursor.fetchall():
        cursor.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{name}`")

    # Recreate with CASCADE
    cursor.execute(
        f"ALTER TABLE `{table}` ADD FOREIGN KEY (`{column}`) "
        f"REFERENCES `{ref_table}`(`{ref_column}`) ON DELETE CASCADE"
    )
```

- [ ] **Step 2: Update CREATE TABLE statements for fresh installs**

In the `researchers_urls` table definition (line 124), change:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id)
```
to:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE
```

In the `html_content` table definition (lines 161-162), change:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id),
FOREIGN KEY (url_id) REFERENCES researcher_urls(id)
```
to:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
FOREIGN KEY (url_id) REFERENCES researcher_urls(id) ON DELETE CASCADE
```

In the `authorship` table definition (lines 174-175), change:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id),
FOREIGN KEY (publication_id) REFERENCES papers(id)
```
to:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
FOREIGN KEY (publication_id) REFERENCES papers(id) ON DELETE CASCADE
```

In the `researcher_fields` table definition (lines 191-192), change:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id),
FOREIGN KEY (field_id) REFERENCES research_fields(id)
```
to:
```python
FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
FOREIGN KEY (field_id) REFERENCES research_fields(id) ON DELETE CASCADE
```

- [ ] **Step 3: Add migration calls in the advisory-locked block**

Inside the `if got_lock == 1:` block (after the existing migrations around line 340), add:

```python
# Migrate FKs to ON DELETE CASCADE
_cascade_fks = [
    ("researcher_urls", "researcher_id", "researchers", "id"),
    ("html_content", "researcher_id", "researchers", "id"),
    ("html_content", "url_id", "researcher_urls", "id"),
    ("authorship", "researcher_id", "researchers", "id"),
    ("authorship", "publication_id", "papers", "id"),
    ("researcher_fields", "researcher_id", "researchers", "id"),
    ("researcher_fields", "field_id", "research_fields", "id"),
]
for table, col, ref_table, ref_col in _cascade_fks:
    try:
        _migrate_fk_cascade(cursor, table, col, ref_table, ref_col)
        conn.commit()
    except Exception as e:
        logging.warning("Migration: CASCADE for %s.%s: %s", table, col, e)
```

- [ ] **Step 4: Run tests to verify no regressions**

Run: `poetry run pytest tests/ -v`
Expected: All existing tests pass (tests mock the DB, schema changes don't affect them)

- [ ] **Step 5: Commit**

```bash
git add database.py
git commit -m "fix: add ON DELETE CASCADE to researcher_urls, html_content, authorship, researcher_fields FKs"
```

---

### Task 2: Add scrape_log.status index and downgrade html_content to MEDIUMTEXT

**Files:**
- Modify: `database.py:195-207` (scrape_log CREATE TABLE)
- Modify: `database.py:149-163` (html_content CREATE TABLE)
- Modify: `database.py:310-360` (migration block)

- [ ] **Step 1: Update CREATE TABLE for scrape_log**

Add an index line inside the `scrape_log` table definition, after the `error_message TEXT` line (line 206):

```python
INDEX idx_scrape_status (status)
```

- [ ] **Step 2: Update CREATE TABLE for html_content**

Change `content LONGTEXT` (line 152) to:

```python
content MEDIUMTEXT,
```

- [ ] **Step 3: Add migrations for existing databases**

Inside the advisory-locked migration block, add:

```python
# Add index on scrape_log.status
try:
    cursor.execute("ALTER TABLE scrape_log ADD INDEX idx_scrape_status (status)")
    conn.commit()
except Exception as e:
    if getattr(e, 'errno', None) != 1061:  # Duplicate key name
        logging.warning("Migration: scrape_log.idx_scrape_status: %s", e)

# Downgrade html_content.content from LONGTEXT to MEDIUMTEXT
try:
    cursor.execute("ALTER TABLE html_content MODIFY content MEDIUMTEXT")
    conn.commit()
except Exception as e:
    logging.warning("Migration: html_content.content type: %s", e)
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/ -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add database.py
git commit -m "perf: add scrape_log.status index, downgrade html_content to MEDIUMTEXT"
```

---

### Task 3: Set utf8mb4 charset on all tables

**Files:**
- Modify: `database.py:103-302` (all CREATE TABLE statements)
- Modify: `database.py:310-360` (migration block)

- [ ] **Step 1: Append charset clause to all 14 CREATE TABLE statements**

Add `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci` after the closing `)` of each CREATE TABLE. For example, researchers becomes:

```python
"researchers": """
    CREATE TABLE IF NOT EXISTS researchers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        last_name VARCHAR(255) NOT NULL,
        first_name VARCHAR(255) NOT NULL,
        position VARCHAR(255),
        affiliation VARCHAR(255),
        description TEXT,
        description_updated_at DATETIME DEFAULT NULL,
        INDEX idx_name (last_name, first_name),
        INDEX idx_affiliation (affiliation)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
```

Apply the same suffix to all 14 tables: `researchers`, `researcher_urls`, `papers`, `html_content`, `authorship`, `research_fields`, `researcher_fields`, `scrape_log`, `researcher_snapshots`, `paper_snapshots`, `paper_urls`, `llm_usage`, `feed_events`, `batch_jobs`.

- [ ] **Step 2: Add charset migration for existing databases**

Inside the advisory-locked migration block, add:

```python
# Convert all tables to utf8mb4
_ALL_TABLES = [
    "researchers", "researcher_urls", "papers", "html_content",
    "authorship", "research_fields", "researcher_fields",
    "scrape_log", "researcher_snapshots", "paper_snapshots",
    "paper_urls", "llm_usage", "feed_events", "batch_jobs",
]
for tbl in _ALL_TABLES:
    try:
        cursor.execute(
            f"ALTER TABLE `{tbl}` CONVERT TO CHARACTER SET utf8mb4 "
            f"COLLATE utf8mb4_unicode_ci"
        )
        conn.commit()
    except Exception as e:
        logging.warning("Migration: utf8mb4 for %s: %s", tbl, e)
```

Note: `CONVERT TO CHARACTER SET` is idempotent — running it on a table already using utf8mb4 is a no-op (MySQL detects no change needed).

- [ ] **Step 3: Run tests**

Run: `poetry run pytest tests/ -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "fix: set utf8mb4 charset on all tables for international character support"
```

---

## Phase B: Column Renames (coordinated code changes)

These are cosmetic improvements for code clarity. The API already maps `papers.url` → `source_url` and `papers.timestamp` → `discovered_at` at the response layer. These tasks align the DB column names with the API field names.

### Task 4: Rename papers.url → papers.source_url

**Files:**
- Modify: `database.py:128-147` (CREATE TABLE papers)
- Modify: `database.py:310-360` (migration block)
- Modify: `api.py:408` (`p.url` in feed events SELECT)
- Modify: `api.py:442` (`url` in publication detail SELECT)
- Modify: `api.py:732` (`p.url` in researcher publications SELECT)
- Modify: `publication.py:91` (`url` in INSERT column list)
- Modify: `publication.py:279` (`url` in SELECT query)

- [ ] **Step 1: Update CREATE TABLE papers**

Change line 130:
```python
url VARCHAR(2048),
```
to:
```python
source_url VARCHAR(2048),
```

- [ ] **Step 2: Add rename migration**

Inside the advisory-locked migration block, add:

```python
# Rename papers.url → papers.source_url
cursor.execute(
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'papers' "
    "AND COLUMN_NAME = 'url'"
)
if cursor.fetchone()[0] > 0:
    cursor.execute("ALTER TABLE papers RENAME COLUMN url TO source_url")
    conn.commit()
```

- [ ] **Step 3: Update SELECT queries in api.py**

Line 408 — change `p.url` to `p.source_url`:
```python
p.id, p.title, p.year, p.venue, p.source_url, p.timestamp,
```

Line 442 — change `url` to `source_url`:
```python
"SELECT id, title, year, venue, source_url, timestamp, status, draft_url, abstract, draft_url_status FROM papers WHERE id = %s",
```

Line 732 — change `p.url` to `p.source_url`:
```python
SELECT p.id, p.title, p.year, p.venue, p.source_url, p.timestamp, p.status, p.draft_url,
```

Note: `_format_publication` and `_format_feed_event` use positional indexing (`row[4]`, `row[9]`), so they require NO changes. However, update their **docstrings** to reflect the new column name:

- `api.py:253` — change `url` to `source_url` in the `_format_publication` docstring:
  ```
  Expected row columns: id, title, year, venue, source_url, timestamp, status, draft_url, abstract, draft_url_status
  ```

- `api.py:280` — change `9: url` to `9: source_url` in the `_format_feed_event` docstring:
  ```
  9: source_url, 10: timestamp,
  ```

- [ ] **Step 4: Update INSERT query in publication.py**

Line 91 — change `url` to `source_url` in the column list:
```python
INSERT IGNORE INTO papers (source_url, title, title_hash, year, venue, abstract, timestamp, status, draft_url, is_seed)
```

- [ ] **Step 5: Update SELECT query in publication.py**

Line 279 — change `url` to `source_url`:
```python
SELECT id, source_url, title, year, venue
```

Note: The `Publication.__init__` kwarg `url=row[1]` stays unchanged — it's a Python class attribute, not a DB column reference.

- [ ] **Step 6: Run tests**

Run: `poetry run pytest tests/ -v`
Expected: All pass (tests mock `Database.fetch_all`/`fetch_one`, so column name changes in SQL strings don't affect test assertions)

- [ ] **Step 7: Commit**

```bash
git add database.py api.py publication.py
git commit -m "refactor: rename papers.url to papers.source_url for clarity"
```

---

### Task 5: Rename papers.timestamp → papers.discovered_at

**Files:**
- Modify: `database.py:128-147` (CREATE TABLE papers)
- Modify: `database.py:310-360` (migration block)
- Modify: `api.py:408` (`p.timestamp` in feed events SELECT)
- Modify: `api.py:442` (`timestamp` in publication detail SELECT)
- Modify: `api.py:732,737` (`p.timestamp` in researcher publications SELECT + ORDER BY)
- Modify: `publication.py:91` (`timestamp` in INSERT column list)

- [ ] **Step 1: Update CREATE TABLE papers**

Change line 136:
```python
timestamp DATETIME,
```
to:
```python
discovered_at DATETIME,
```

Also change the index at line 143:
```python
INDEX idx_timestamp (timestamp),
```
to:
```python
INDEX idx_discovered_at (discovered_at),
```

- [ ] **Step 2: Add rename migration**

Inside the advisory-locked migration block, add:

```python
# Rename papers.timestamp → papers.discovered_at
cursor.execute(
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'papers' "
    "AND COLUMN_NAME = 'timestamp'"
)
if cursor.fetchone()[0] > 0:
    cursor.execute("ALTER TABLE papers RENAME COLUMN `timestamp` TO discovered_at")
    # Rename the index too
    try:
        cursor.execute("ALTER TABLE papers DROP INDEX idx_timestamp")
    except Exception:
        pass  # Index may not exist
    cursor.execute("ALTER TABLE papers ADD INDEX idx_discovered_at (discovered_at)")
    conn.commit()
```

Note: `timestamp` requires backticks in the RENAME since it's a MySQL reserved word.

- [ ] **Step 3: Update SELECT queries in api.py**

Line 408 — change `p.timestamp` to `p.discovered_at`:
```python
p.id, p.title, p.year, p.venue, p.source_url, p.discovered_at,
```

Line 442 — change `timestamp` to `discovered_at`:
```python
"SELECT id, title, year, venue, source_url, discovered_at, status, draft_url, abstract, draft_url_status FROM papers WHERE id = %s",
```

Line 732 — change `p.timestamp` to `p.discovered_at`:
```python
SELECT p.id, p.title, p.year, p.venue, p.source_url, p.discovered_at, p.status, p.draft_url,
```

Line 737 — change ORDER BY:
```python
ORDER BY p.discovered_at DESC
```

Also update the docstrings (completing the renames started in Task 4):

- `api.py:253` — change `timestamp` to `discovered_at`:
  ```
  Expected row columns: id, title, year, venue, source_url, discovered_at, status, draft_url, abstract, draft_url_status
  ```

- `api.py:280` — change `10: timestamp` to `10: discovered_at`:
  ```
  10: discovered_at,
  ```

- [ ] **Step 4: Update INSERT query in publication.py**

Line 91 — change `timestamp` to `discovered_at`:
```python
INSERT IGNORE INTO papers (source_url, title, title_hash, year, venue, abstract, discovered_at, status, draft_url, is_seed)
```

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add database.py api.py publication.py
git commit -m "refactor: rename papers.timestamp to papers.discovered_at for clarity"
```

---

## Phase C: Test Documentation

### Task 6: Update test comments for renamed columns

**Files:**
- Modify: `tests/test_api_publications.py:25`
- Modify: `tests/test_api_filters.py:40,54-55`
- Modify: `tests/test_api_integration.py:64`
- Modify: `tests/test_api_response_shapes.py:71`

- [ ] **Step 1: Update column-order comments in test files**

These are documentation-only changes. Update comments that reference the old column names.

In each file, replace patterns like:
```python
#   p.id, p.title, p.year, p.venue, p.url, p.timestamp, ...
```
with:
```python
#   p.id, p.title, p.year, p.venue, p.source_url, p.discovered_at, ...
```

In `test_api_filters.py`, update inline comments:
```python
"https://example.com/pub1",       # p.source_url
datetime(2026, 3, 15, 14, 30),    # p.discovered_at
```

- [ ] **Step 2: Run full test suite**

Run: `poetry run pytest tests/ -v && cd app && npm test`
Expected: All Python and JavaScript tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "docs: update test comments for renamed papers columns"
```

---

## Phase D: Verification

### Task 7: Verify migrations with local database

- [ ] **Step 1: Reset and seed local database**

```bash
make reset-db && make seed
```

- [ ] **Step 2: Check startup logs**

```bash
make dev
```

Verify: "All tables created successfully" in logs, no migration warnings.

- [ ] **Step 3: Verify schema in MySQL**

```sql
SHOW CREATE TABLE papers\G
-- Expect: source_url VARCHAR(2048), discovered_at DATETIME, utf8mb4 charset

SHOW CREATE TABLE researcher_urls\G
-- Expect: ON DELETE CASCADE on researcher_id FK

SHOW CREATE TABLE html_content\G
-- Expect: ON DELETE CASCADE on both FKs, content MEDIUMTEXT

SHOW CREATE TABLE authorship\G
-- Expect: ON DELETE CASCADE on both FKs

SHOW CREATE TABLE scrape_log\G
-- Expect: idx_scrape_status index on status column
```

- [ ] **Step 4: Run full check**

```bash
make check
```

Expected: env validation, pytest, tsc, and jest all pass.

- [ ] **Step 5: Final commit if any fixes needed**

---

## Notes

- **Phase A is safe and independent** — no query changes, no risk of breaking existing code. Can be deployed on its own.
- **Phase B is cosmetic** — the API already maps old column names to the correct response field names. These renames align internal DB names with external API names for developer clarity.
- **Migration idempotency** — all migrations check state before acting (information_schema lookups, duplicate-key error suppression). Safe to run multiple times.
- **Existing tests are unaffected** — they mock `Database.fetch_all`/`fetch_one` and use positional tuple indexing, so SQL string changes don't affect assertions.
- **`CONVERT TO CHARACTER SET utf8mb4`** rewrites table data. Tables in this project are small (thousands of rows), so this is fast (<1s per table).
