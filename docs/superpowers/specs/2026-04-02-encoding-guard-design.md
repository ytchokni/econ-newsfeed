# Encoding Guard: Data Integrity for Text Fields

**Date:** 2026-04-02
**Status:** Approved

## Problem

Mojibake (double-encoded UTF-8) is appearing in database text fields. Example: "fÃ¼r" instead of "für". This affects researcher names, paper titles, affiliations, and other text fields across the system. The root cause is likely the MySQL connection not specifying `charset=utf8mb4`, causing encoding mismatches between the Python client and the database.

## Solution Overview

Five components, layered for defense in depth:

1. **Root cause fix** — connection-level charset configuration
2. **`encoding_guard.py`** — centralized detection, auto-fix, and logging module
3. **Database integration** — guard called before writes in key data access methods
4. **Automated tests** — unit, integration, and regression tests
5. **Audit script** — scan and fix existing data

## 1. Root Cause Fix

### db_config.py

Add `charset='utf8mb4'` and `collation='utf8mb4_unicode_ci'` to the MySQL connection pool configuration. This ensures the connection negotiates UTF-8 encoding with the server, matching the table-level declarations already in place.

### docker-compose.yml

Add `--character-set-server=utf8mb4` and `--collation-server=utf8mb4_unicode_ci` to the MySQL service command. This sets the server default so any new databases/tables inherit the correct charset.

## 2. encoding_guard.py

A standalone module in the project root (alongside `html_fetcher.py`, `publication.py`). Depends on the `ftfy` library.

### Functions

**`has_mojibake(text: str) -> bool`**
- Compares `ftfy.fix_text(text) != text` to detect garbled text
- Fast check, no side effects

**`fix_encoding(text: str) -> tuple[str, bool]`**
- Runs `ftfy.fix_text(text)`
- Returns `(fixed_text, was_changed)`
- Handles double-encoding, triple-encoding, Windows-1252 misinterpretation

**`guard_text_fields(row: dict, fields: list[str], context: str) -> dict`**
- Takes a dict of column values, a list of text field names to check, and a context string (e.g. `"papers.title"`)
- For each field: detect, auto-fix, log corrections
- Returns the cleaned dict
- Skips None values gracefully

### Logging

Uses standard Python `logging` at WARNING level. Format:

```
WARNING encoding_guard: Mojibake fixed in papers.title (paper_id=42): "fÃ¼r" → "für"
```

No new database tables for audit trail — plain log output, grep-able.

## 3. Database Integration

The guard is called explicitly before SQL execution in write methods. No magic wrapping or monkey-patching.

### Integration Points

| File | Method/Area | Fields Guarded |
|------|-------------|----------------|
| `publication.py` | `save_publications()` — main INSERT into `papers` | title, abstract, venue |
| `database/papers.py` | `update_openalex_data()` — enrichment UPDATE | abstract |
| `database/researchers.py` | `update_researcher_bio()`, CSV loading | first_name, last_name, affiliation, position, description |
| `openalex.py` | `enrich_publication()` — coauthor INSERT | display_name |

### Pattern

```python
from encoding_guard import guard_text_fields

# Before SQL execute:
row = guard_text_fields(row, ["title", "abstract", "venue"], context=f"papers (researcher_id={rid})")
cursor.execute(...)
```

## 4. Automated Tests

New file: `tests/test_encoding_guard.py`

### Unit Tests

- Known mojibake strings are detected and fixed (parametrized with a fixture list):
  - `"fÃ¼r"` → `"für"`
  - `"Ã©conomie"` → `"économie"`
  - `"Ã¶konometrie"` → `"ökonometrie"`
  - `"seÃ±or"` → `"señor"`
- Clean Unicode passes through unchanged: `"München"`, `"café"`, `"señor"`
- Empty strings and None values handled gracefully
- Logging output verified (assert WARNING logged with before/after values)

### Integration Tests

- Feed HTML containing non-ASCII text (German, French, Spanish) through `html_fetcher` and assert clean extraction
- Mock an OpenAI response with accented characters, run through publication extraction, assert no mojibake
- Mock an OpenAlex response with international author names, verify clean output

### Regression Tests

- A parametrized fixture list of known mojibake→correct pairs drawn from real data
- Adding new regression cases is just adding a tuple to the list

All tests follow existing patterns: `unittest.mock`, class-based organization, no real database connection.

## 5. Audit Script

New file: `scripts/audit_encoding.py`

### Dry-Run Mode (default)

```bash
poetry run python scripts/audit_encoding.py
```

- Scans all text columns across: `papers`, `researchers`, `openalex_coauthors`, `paper_topics`
- Outputs affected rows as CSV to stdout: `table, column, row_id, original_value, fixed_value`
- Summary line: `"Found N mojibake values across M rows in K tables"`

### Fix Mode

```bash
poetry run python scripts/audit_encoding.py --fix
```

- Same scan, applies `ftfy.fix_text()` corrections via UPDATE statements
- Logs every change at WARNING level (same format as the guard)
- Prints summary: `"Fixed N values across M rows"`

Uses the existing `Database` connection. Queries each table's text columns and runs `fix_encoding()` on every value.

## Dependencies

- **New:** `ftfy` (MIT license) — the standard library for fixing Unicode mojibake in Python. Handles edge cases far better than hand-rolled regex.
- **Existing:** `logging`, `argparse` (stdlib)

## Tables Scanned by Audit

| Table | Text Columns |
|-------|-------------|
| `papers` | title, abstract, venue |
| `researchers` | first_name, last_name, affiliation, position, description |
| `openalex_coauthors` | display_name |
| `paper_topics` | topic_name, subfield_name, field_name, domain_name |

## Non-Goals

- No new database tables for logging/audit (use Python logging)
- No MySQL triggers or stored procedures
- No changes to the LLM extraction prompts
- No changes to the frontend
