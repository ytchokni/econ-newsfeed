# Encoding Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent and fix mojibake (double-encoded UTF-8) across all text fields in the database.

**Architecture:** A centralized `encoding_guard.py` module using `ftfy` for detection/fixing, integrated at database write points. Root cause fix at connection level. One-time audit script for existing data.

**Tech Stack:** Python, ftfy, mysql-connector-python, pytest

---

### Task 1: Add ftfy dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add ftfy to project dependencies**

```bash
poetry add ftfy
```

- [ ] **Step 2: Verify installation**

```bash
poetry run python -c "import ftfy; print(ftfy.fix_text('fÃ¼r'))"
```

Expected output: `für`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "deps: add ftfy for mojibake detection and fixing"
```

---

### Task 2: Fix MySQL connection charset (root cause)

**Files:**
- Modify: `db_config.py:24-31`
- Modify: `docker-compose.yml:1-16`

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_config_charset.py`:

```python
"""Verify db_config includes charset for UTF-8 MySQL connections."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from db_config import db_config


class TestDbConfigCharset:
    def test_charset_is_utf8mb4(self):
        assert db_config.get("charset") == "utf8mb4"

    def test_collation_is_unicode_ci(self):
        assert db_config.get("collation") == "utf8mb4_unicode_ci"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_db_config_charset.py -v
```

Expected: FAIL — `db_config` currently has no `charset` or `collation` keys.

- [ ] **Step 3: Add charset to db_config.py**

In `db_config.py`, add `charset` and `collation` to the `db_config` dict (after `'database'`):

```python
# MySQL configuration
db_config = {
    'host': os.environ['DB_HOST'],
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ['DB_USER'],
    'password': os.environ['DB_PASSWORD'],
    'database': _DB_NAME,
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci',
}
```

- [ ] **Step 4: Add charset to docker-compose.yml MySQL service**

Add `command` to the `db` service in `docker-compose.yml`, after the `image` line:

```yaml
  db:
    image: mysql:8.0.40
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
    restart: unless-stopped
```

- [ ] **Step 5: Run test to verify it passes**

```bash
poetry run pytest tests/test_db_config_charset.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add db_config.py docker-compose.yml tests/test_db_config_charset.py
git commit -m "fix: add utf8mb4 charset to MySQL connection and Docker config"
```

---

### Task 3: Create encoding_guard.py module

**Files:**
- Create: `encoding_guard.py`
- Create: `tests/test_encoding_guard.py`

- [ ] **Step 1: Write unit tests for has_mojibake**

Create `tests/test_encoding_guard.py`:

```python
"""Tests for encoding_guard module — mojibake detection, fixing, and field guarding."""
import logging

import pytest


# Known mojibake → correct pairs from real data
MOJIBAKE_PAIRS = [
    ("fÃ¼r", "für"),
    ("Ã©conomie", "économie"),
    ("Ã¶konometrie", "ökonometrie"),
    ("seÃ±or", "señor"),
    ("schÃ¤tzen", "schätzen"),
    ("UniversitÃ¤t", "Universität"),
    ("FrÃ©dÃ©ric", "Frédéric"),
    ("GÃ¶ttingen", "Göttingen"),
]

CLEAN_UNICODE = [
    "München",
    "café",
    "señor",
    "naïve",
    "Zürich",
    "François",
    "Ströbele",
]


class TestHasMojibake:
    @pytest.mark.parametrize("garbled,_", MOJIBAKE_PAIRS)
    def test_detects_mojibake(self, garbled, _):
        from encoding_guard import has_mojibake
        assert has_mojibake(garbled) is True

    @pytest.mark.parametrize("clean", CLEAN_UNICODE)
    def test_clean_unicode_not_flagged(self, clean):
        from encoding_guard import has_mojibake
        assert has_mojibake(clean) is False

    def test_empty_string(self):
        from encoding_guard import has_mojibake
        assert has_mojibake("") is False

    def test_ascii_only(self):
        from encoding_guard import has_mojibake
        assert has_mojibake("hello world") is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/test_encoding_guard.py::TestHasMojibake -v
```

Expected: FAIL — `encoding_guard` module does not exist yet.

- [ ] **Step 3: Implement has_mojibake**

Create `encoding_guard.py`:

```python
"""Centralized mojibake detection, auto-fix, and logging for text fields.

Uses ftfy to detect and repair double-encoded UTF-8 (e.g. "fÃ¼r" → "für").
Designed to be called before database writes and by the audit script.
"""
import logging

import ftfy

logger = logging.getLogger(__name__)


def has_mojibake(text: str) -> bool:
    """Return True if text contains mojibake (double-encoded UTF-8)."""
    if not text:
        return False
    return ftfy.fix_text(text) != text
```

- [ ] **Step 4: Run has_mojibake tests to verify they pass**

```bash
poetry run pytest tests/test_encoding_guard.py::TestHasMojibake -v
```

Expected: PASS

- [ ] **Step 5: Write unit tests for fix_encoding**

Append to `tests/test_encoding_guard.py`:

```python
class TestFixEncoding:
    @pytest.mark.parametrize("garbled,expected", MOJIBAKE_PAIRS)
    def test_fixes_mojibake(self, garbled, expected):
        from encoding_guard import fix_encoding
        fixed, was_changed = fix_encoding(garbled)
        assert fixed == expected
        assert was_changed is True

    @pytest.mark.parametrize("clean", CLEAN_UNICODE)
    def test_clean_unicode_unchanged(self, clean):
        from encoding_guard import fix_encoding
        fixed, was_changed = fix_encoding(clean)
        assert fixed == clean
        assert was_changed is False

    def test_empty_string(self):
        from encoding_guard import fix_encoding
        fixed, was_changed = fix_encoding("")
        assert fixed == ""
        assert was_changed is False
```

- [ ] **Step 6: Run tests to verify they fail**

```bash
poetry run pytest tests/test_encoding_guard.py::TestFixEncoding -v
```

Expected: FAIL — `fix_encoding` not defined.

- [ ] **Step 7: Implement fix_encoding**

Add to `encoding_guard.py`:

```python
def fix_encoding(text: str) -> tuple[str, bool]:
    """Fix mojibake in text. Returns (fixed_text, was_changed)."""
    if not text:
        return text, False
    fixed = ftfy.fix_text(text)
    return fixed, fixed != text
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
poetry run pytest tests/test_encoding_guard.py::TestFixEncoding -v
```

Expected: PASS

- [ ] **Step 9: Write unit tests for guard_text_fields**

Append to `tests/test_encoding_guard.py`:

```python
class TestGuardTextFields:
    def test_fixes_mojibake_fields(self):
        from encoding_guard import guard_text_fields
        row = {"title": "fÃ¼r die Wirtschaft", "year": "2024", "venue": None}
        result = guard_text_fields(row, ["title", "venue"], context="papers (id=1)")
        assert result["title"] == "für die Wirtschaft"
        assert result["year"] == "2024"  # untouched — not in fields list
        assert result["venue"] is None   # None skipped

    def test_clean_unicode_unchanged(self):
        from encoding_guard import guard_text_fields
        row = {"title": "Universität München", "abstract": "café society"}
        result = guard_text_fields(row, ["title", "abstract"], context="papers (id=2)")
        assert result["title"] == "Universität München"
        assert result["abstract"] == "café society"

    def test_missing_field_skipped(self):
        from encoding_guard import guard_text_fields
        row = {"title": "Hello"}
        result = guard_text_fields(row, ["title", "abstract"], context="papers (id=3)")
        assert result["title"] == "Hello"
        assert "abstract" not in result

    def test_logs_warning_on_fix(self, caplog):
        from encoding_guard import guard_text_fields
        row = {"title": "fÃ¼r"}
        with caplog.at_level(logging.WARNING, logger="encoding_guard"):
            guard_text_fields(row, ["title"], context="papers (id=42)")
        assert "Mojibake fixed" in caplog.text
        assert "fÃ¼r" in caplog.text
        assert "für" in caplog.text
        assert "papers (id=42)" in caplog.text

    def test_no_warning_for_clean_text(self, caplog):
        from encoding_guard import guard_text_fields
        row = {"title": "München"}
        with caplog.at_level(logging.WARNING, logger="encoding_guard"):
            guard_text_fields(row, ["title"], context="papers (id=1)")
        assert "Mojibake fixed" not in caplog.text
```

- [ ] **Step 10: Run tests to verify they fail**

```bash
poetry run pytest tests/test_encoding_guard.py::TestGuardTextFields -v
```

Expected: FAIL — `guard_text_fields` not defined.

- [ ] **Step 11: Implement guard_text_fields**

Add to `encoding_guard.py`:

```python
def guard_text_fields(row: dict, fields: list[str], context: str) -> dict:
    """Check and fix mojibake in specified text fields of a row dict.

    Args:
        row: dict of column values (modified in place and returned)
        fields: list of field names to check
        context: human-readable context for log messages (e.g. "papers (id=42)")

    Returns:
        The row dict with any mojibake fields fixed.
    """
    for field in fields:
        value = row.get(field)
        if value is None:
            continue
        fixed, was_changed = fix_encoding(value)
        if was_changed:
            logger.warning(
                'Mojibake fixed in %s.%s: "%s" → "%s"',
                context, field, value, fixed,
            )
            row[field] = fixed
    return row
```

- [ ] **Step 12: Run all encoding_guard tests**

```bash
poetry run pytest tests/test_encoding_guard.py -v
```

Expected: All PASS

- [ ] **Step 13: Commit**

```bash
git add encoding_guard.py tests/test_encoding_guard.py
git commit -m "feat: add encoding_guard module for mojibake detection and fixing"
```

---

### Task 4: Integrate guard into publication.py (paper writes)

**Files:**
- Modify: `publication.py:198-228`

- [ ] **Step 1: Write the failing test**

Create `tests/test_encoding_integration.py`:

```python
"""Integration tests: encoding guard is called during database writes."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")

from unittest.mock import patch, MagicMock

import pytest


class TestPublicationEncodingGuard:
    """Verify save_publications passes text through encoding guard."""

    @patch("publication.Database")
    def test_mojibake_title_is_fixed_before_insert(self, mock_db):
        """A paper with mojibake in title should be cleaned before DB insert."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn

        from publication import Publication

        publications = [{
            "title": "Ergebnisse fÃ¼r die Wirtschaft",
            "year": "2024",
            "venue": "Ã–konometrie Journal",
            "abstract": "Eine Analyse der GÃ¼terpreise",
            "status": "published",
            "draft_url": None,
        }]

        Publication.save_publications("http://example.com", publications)

        # Find the INSERT INTO papers call
        insert_calls = [
            call for call in mock_cursor.execute.call_args_list
            if call[0][0].strip().startswith("INSERT IGNORE INTO papers")
        ]
        assert len(insert_calls) >= 1

        params = insert_calls[0][0][1]
        # params order: (url, title, title_hash, year, venue, abstract, ...)
        title_param = params[1]
        venue_param = params[4]
        abstract_param = params[5]

        assert title_param == "Ergebnisse für die Wirtschaft"
        assert venue_param == "Ökonometrie Journal"
        assert abstract_param == "Eine Analyse der Güterpreise"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_encoding_integration.py::TestPublicationEncodingGuard -v
```

Expected: FAIL — encoding guard not yet called in `save_publications`.

- [ ] **Step 3: Add guard to publication.py save_publications**

At the top of `publication.py`, add import:

```python
from encoding_guard import guard_text_fields
```

In the `save_publications` method, after `title = pub['title'].strip() if pub['title'] else ''` (line 214), add the guard call:

```python
                title = pub['title'].strip() if pub['title'] else ''
                # Fix any mojibake before saving
                pub = guard_text_fields(
                    dict(pub, title=title),
                    ["title", "abstract", "venue"],
                    context=f"papers (url={url})",
                )
                title = pub['title']
                title_hash = Database.compute_title_hash(pub['title'])
```

And update the INSERT parameters to use the guarded `pub` dict values:

```python
                    cursor.execute(
                        """
                        INSERT IGNORE INTO papers (source_url, title, title_hash, year, venue, abstract, discovered_at, status, draft_url, is_seed)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (url, title, title_hash, pub.get('year'), pub.get('venue'),
                         pub.get('abstract'), datetime.now(timezone.utc), pub.get('status'),
                         pub.get('draft_url'), is_seed),
                    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_encoding_integration.py::TestPublicationEncodingGuard -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
poetry run pytest tests/ -v --timeout=30
```

Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_encoding_integration.py
git commit -m "feat: integrate encoding guard into publication save path"
```

---

### Task 5: Integrate guard into database/papers.py (OpenAlex enrichment)

**Files:**
- Modify: `database/papers.py:45-67`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_encoding_integration.py`:

```python
class TestOpenAlexEncodingGuard:
    """Verify update_openalex_data passes text through encoding guard."""

    @patch("database.papers.get_connection")
    def test_mojibake_abstract_is_fixed(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        from database.papers import update_openalex_data

        update_openalex_data(
            paper_id=1,
            doi="10.1234/test",
            openalex_id="W123",
            coauthors=[{"display_name": "FrÃ©dÃ©ric Dupont", "openalex_author_id": "A1"}],
            abstract="Eine Analyse fÃ¼r Ã–konomen",
        )

        # Check the UPDATE papers call
        update_calls = [
            call for call in mock_cursor.execute.call_args_list
            if "UPDATE papers" in str(call)
        ]
        assert len(update_calls) >= 1
        params = update_calls[0][0][1]
        # params: (doi, openalex_id, abstract, paper_id)
        abstract_param = params[2]
        assert abstract_param == "Eine Analyse für Ökonomen"

        # Check the INSERT coauthors call
        executemany_calls = mock_cursor.executemany.call_args_list
        assert len(executemany_calls) >= 1
        coauthor_params = executemany_calls[0][0][1]
        display_name = coauthor_params[0][1]
        assert display_name == "Frédéric Dupont"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_encoding_integration.py::TestOpenAlexEncodingGuard -v
```

Expected: FAIL — guard not yet called in `update_openalex_data`.

- [ ] **Step 3: Add guard to database/papers.py**

Add import at the top of `database/papers.py`:

```python
from encoding_guard import fix_encoding
```

In `update_openalex_data`, add guard calls before the SQL executes:

```python
def update_openalex_data(paper_id, doi, openalex_id, coauthors, abstract=None):
    """Store OpenAlex enrichment data for a paper."""
    # Guard abstract against mojibake
    if abstract:
        abstract, _ = fix_encoding(abstract)

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE papers SET doi = %s, openalex_id = %s, abstract = COALESCE(%s, abstract) WHERE id = %s",
                (doi, openalex_id, abstract, paper_id),
            )
            cursor.execute(
                "DELETE FROM openalex_coauthors WHERE paper_id = %s", (paper_id,)
            )
            if coauthors:
                # Guard coauthor display names
                for ca in coauthors:
                    ca["display_name"], _ = fix_encoding(ca["display_name"])
                cursor.executemany(
                    "INSERT IGNORE INTO openalex_coauthors (paper_id, display_name, openalex_author_id) "
                    "VALUES (%s, %s, %s)",
                    [(paper_id, ca["display_name"], ca.get("openalex_author_id")) for ca in coauthors],
                )
            conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_encoding_integration.py::TestOpenAlexEncodingGuard -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/papers.py tests/test_encoding_integration.py
git commit -m "feat: integrate encoding guard into OpenAlex enrichment writes"
```

---

### Task 6: Integrate guard into database/researchers.py

**Files:**
- Modify: `database/researchers.py:110-113` (get_researcher_id INSERT)
- Modify: `database/researchers.py:303-308` (update_researcher_bio)
- Modify: `database/researchers.py:319-334` (import_data_from_file)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_encoding_integration.py`:

```python
class TestResearcherEncodingGuard:
    """Verify researcher write paths pass text through encoding guard."""

    @patch("database.researchers.fetch_one", return_value=None)
    @patch("database.researchers.fetch_all", return_value=[])
    @patch("database.researchers.execute_query", return_value=99)
    def test_mojibake_name_fixed_on_insert(self, mock_exec, mock_fetch_all, mock_fetch_one):
        from database.researchers import get_researcher_id

        result = get_researcher_id(
            first_name="FrÃ©dÃ©ric",
            last_name="MÃ¼ller",
            position="Professor",
            affiliation="UniversitÃ¤t ZÃ¼rich",
        )

        assert result == 99
        # Check the INSERT call
        insert_call = mock_exec.call_args
        params = insert_call[0][1]
        # params: (first_name, last_name, position, affiliation)
        assert params[0] == "Frédéric"
        assert params[1] == "Müller"
        assert params[2] == "Professor"  # ASCII, unchanged
        assert params[3] == "Universität Zürich"

    @patch("database.researchers.execute_query")
    def test_mojibake_bio_fixed_on_update(self, mock_exec):
        from database.researchers import update_researcher_bio

        update_researcher_bio(1, "Forschung Ã¼ber Ã–konomie")

        params = mock_exec.call_args[0][1]
        assert params[0] == "Forschung über Ökonomie"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_encoding_integration.py::TestResearcherEncodingGuard -v
```

Expected: FAIL — guard not called in researcher write paths.

- [ ] **Step 3: Add guard to database/researchers.py**

Add import at top of `database/researchers.py`:

```python
from encoding_guard import fix_encoding
```

In `get_researcher_id`, right after the `is_bad_researcher_name` check (around line 128), add:

```python
    # Fix any mojibake in name/affiliation fields
    first_name, _ = fix_encoding(first_name)
    last_name, _ = fix_encoding(last_name)
    if position:
        position, _ = fix_encoding(position)
    if affiliation:
        affiliation, _ = fix_encoding(affiliation)
```

In `update_researcher_bio` (line 303), add guard before the query:

```python
def update_researcher_bio(researcher_id: int, bio: str) -> None:
    """Legacy: update researcher description only if the current description is NULL."""
    bio, _ = fix_encoding(bio)
    execute_query(
        "UPDATE researchers SET description = %s WHERE id = %s AND description IS NULL",
        (bio, researcher_id),
    )
```

In `import_data_from_file` (line 329), add guard after reading CSV row:

```python
                first_name, last_name, position, affiliation, page_type, url = row
                first_name, _ = fix_encoding(first_name)
                last_name, _ = fix_encoding(last_name)
                position, _ = fix_encoding(position)
                affiliation, _ = fix_encoding(affiliation)
                researcher_id = get_researcher_id(first_name, last_name, position, affiliation)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_encoding_integration.py::TestResearcherEncodingGuard -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
poetry run pytest tests/ -v --timeout=30
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add database/researchers.py tests/test_encoding_integration.py
git commit -m "feat: integrate encoding guard into researcher write paths"
```

---

### Task 7: Create audit script

**Files:**
- Create: `scripts/audit_encoding.py`

- [ ] **Step 1: Write the audit script**

Create `scripts/audit_encoding.py`:

```python
#!/usr/bin/env python3
"""Scan database text fields for mojibake and optionally fix them.

Usage:
    poetry run python scripts/audit_encoding.py          # dry-run: report only
    poetry run python scripts/audit_encoding.py --fix    # apply fixes
"""
import argparse
import csv
import logging
import sys

from encoding_guard import fix_encoding

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Tables and their text columns to scan, plus the primary key column
SCAN_TARGETS = [
    ("papers", "id", ["title", "abstract", "venue"]),
    ("researchers", "id", ["first_name", "last_name", "affiliation", "position", "description"]),
    ("openalex_coauthors", "id", ["display_name"]),
    ("paper_topics", "id", ["topic_name", "subfield_name", "field_name", "domain_name"]),
]


def scan_table(cursor, table: str, pk_col: str, columns: list[str]) -> list[dict]:
    """Scan a table for mojibake. Returns list of findings."""
    findings = []
    cols_sql = ", ".join([pk_col] + columns)
    cursor.execute(f"SELECT {cols_sql} FROM {table}")
    for row in cursor.fetchall():
        row_id = row[pk_col]
        for col in columns:
            value = row[col]
            if not value:
                continue
            fixed, was_changed = fix_encoding(value)
            if was_changed:
                findings.append({
                    "table": table,
                    "column": col,
                    "row_id": row_id,
                    "original": value,
                    "fixed": fixed,
                })
    return findings


def apply_fixes(cursor, findings: list[dict]) -> None:
    """Apply encoding fixes to the database."""
    for f in findings:
        cursor.execute(
            f"UPDATE {f['table']} SET {f['column']} = %s WHERE id = %s",
            (f["fixed"], f["row_id"]),
        )
        logger.warning(
            'Mojibake fixed in %s.%s (id=%s): "%s" → "%s"',
            f["table"], f["column"], f["row_id"], f["original"], f["fixed"],
        )


def main():
    parser = argparse.ArgumentParser(description="Scan and fix mojibake in database text fields.")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (default: dry-run report only)")
    args = parser.parse_args()

    from database.connection import get_connection

    all_findings = []

    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            for table, pk_col, columns in SCAN_TARGETS:
                findings = scan_table(cursor, table, pk_col, columns)
                all_findings.extend(findings)

            if not all_findings:
                print("No mojibake found. All text fields are clean.")
                return

            # Report as CSV
            writer = csv.DictWriter(
                sys.stdout,
                fieldnames=["table", "column", "row_id", "original", "fixed"],
            )
            writer.writeheader()
            for f in all_findings:
                writer.writerow(f)

            # Summary
            tables_affected = len(set(f["table"] for f in all_findings))
            rows_affected = len(set((f["table"], f["row_id"]) for f in all_findings))
            print(
                f"\nFound {len(all_findings)} mojibake values "
                f"across {rows_affected} rows in {tables_affected} tables.",
                file=sys.stderr,
            )

            if args.fix:
                apply_fixes(cursor, all_findings)
                conn.commit()
                print(
                    f"Fixed {len(all_findings)} values across {rows_affected} rows.",
                    file=sys.stderr,
                )
            else:
                print(
                    "Run with --fix to apply corrections.",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write test for audit script logic**

Append to `tests/test_encoding_guard.py`:

```python
class TestAuditScanTable:
    """Test the scan_table function used by the audit script."""

    def test_finds_mojibake_in_rows(self):
        import sys
        import os
        from unittest.mock import MagicMock

        # Add project root to path so scripts package is importable
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from scripts.audit_encoding import scan_table

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "title": "fÃ¼r die Wirtschaft", "abstract": None, "venue": "Clean Venue"},
            {"id": 2, "title": "Clean Title", "abstract": "Ã©conomie mondiale", "venue": None},
            {"id": 3, "title": "All Clean", "abstract": "No issues", "venue": "Fine"},
        ]

        findings = audit_mod.scan_table(mock_cursor, "papers", "id", ["title", "abstract", "venue"])

        assert len(findings) == 2
        assert findings[0]["table"] == "papers"
        assert findings[0]["column"] == "title"
        assert findings[0]["row_id"] == 1
        assert findings[0]["fixed"] == "für die Wirtschaft"
        assert findings[1]["column"] == "abstract"
        assert findings[1]["row_id"] == 2
        assert findings[1]["fixed"] == "économie mondiale"
```

- [ ] **Step 3: Run audit tests**

```bash
poetry run pytest tests/test_encoding_guard.py::TestAuditScanTable -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/audit_encoding.py tests/test_encoding_guard.py
git commit -m "feat: add audit script for scanning and fixing existing mojibake"
```

---

### Task 8: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

```bash
poetry run pytest tests/ -v --timeout=30
```

Expected: All tests pass.

- [ ] **Step 2: Run type check and frontend tests**

```bash
cd app && npx tsc --noEmit && npx jest
```

Expected: No regressions.

- [ ] **Step 3: Verify audit script runs (dry-run against local DB if available)**

```bash
poetry run python scripts/audit_encoding.py
```

Expected: Either "No mojibake found" or a CSV report of findings.

- [ ] **Step 4: Commit any final fixes if needed, then tag completion**

```bash
git log --oneline -8
```

Verify the commit history shows the incremental implementation.
