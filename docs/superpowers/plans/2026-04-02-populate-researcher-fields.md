# Populate Researcher Fields from JEL Codes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the field filter on `/researchers` functional by deriving `researcher_fields` from existing JEL code assignments.

**Architecture:** Add a JEL-to-field mapping in `database/jel.py`, call it automatically whenever JEL codes are saved/added, and backfill existing researchers. The `migration` field has no direct JEL code — use keyword matching on researcher descriptions.

**Tech Stack:** Python, MySQL, existing `database/` module patterns

**Closes:** #68

---

### File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `database/jel.py` | Modify (add ~60 lines) | JEL-to-field mapping dict + `sync_researcher_fields_from_jel()` + hook into `save_researcher_jel_codes()` and `add_researcher_jel_codes()` |
| `database/__init__.py` | Modify (add 2 lines) | Export `sync_researcher_fields_from_jel` on `Database` facade |
| `scripts/backfill_researcher_fields.py` | Create | One-time backfill for all researchers with JEL codes |
| `Makefile` | Modify (add 1 target) | `make populate-fields` target |
| `tests/test_researcher_fields.py` | Create | Tests for mapping, sync function, and integration |

---

### Task 1: Tests for JEL-to-Field Mapping

**Files:**
- Create: `tests/test_researcher_fields.py`

- [ ] **Step 1: Write failing tests for the mapping dict and sync function**

```python
# tests/test_researcher_fields.py
"""Tests for researcher_fields derivation from JEL codes."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import MagicMock, patch


class TestJelToFieldMapping:
    """Tests for _JEL_TO_FIELD_SLUGS mapping."""

    def test_macro_maps_to_macroeconomics(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        assert _JEL_TO_FIELD_SLUGS["E"] == ["macroeconomics"]

    def test_labour_maps_to_labour_economics(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        assert _JEL_TO_FIELD_SLUGS["J"] == ["labour-economics"]

    def test_finance_maps_to_finance(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        assert _JEL_TO_FIELD_SLUGS["G"] == ["finance"]

    def test_all_11_mapped_codes_present(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        expected_codes = {"C", "E", "F", "G", "H", "I", "J", "L", "O", "P", "Z"}
        assert set(_JEL_TO_FIELD_SLUGS.keys()) == expected_codes

    def test_unmapped_codes_not_present(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        for code in ["A", "B", "D", "K", "M", "N", "Q", "R", "Y"]:
            assert code not in _JEL_TO_FIELD_SLUGS


class TestSyncResearcherFieldsFromJel:
    """Tests for sync_researcher_fields_from_jel."""

    def _make_mock_conn(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn, mock_cursor

    def test_deletes_existing_and_inserts_new_fields(self):
        mock_conn, mock_cursor = self._make_mock_conn()
        # Simulate: researcher has description "studies labour markets"
        # fetch_one returns the description
        mock_cursor.fetchall.return_value = [(1,), (2,)]  # two field_ids

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel.fetch_one", return_value={"description": "studies labour markets"}),
        ):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=["J", "E"])

        all_sql = [c[0][0] for c in mock_cursor.execute.call_args_list]
        # Should delete existing fields
        assert any("DELETE FROM researcher_fields" in sql for sql in all_sql)
        # Should select field IDs from research_fields
        assert any("SELECT id FROM research_fields" in sql for sql in all_sql)
        # Should insert for each field_id
        assert any("INSERT IGNORE INTO researcher_fields" in sql for sql in all_sql)
        mock_conn.commit.assert_called_once()

    def test_empty_jel_codes_still_clears_fields(self):
        """If a researcher has no JEL codes, their fields should be cleared."""
        mock_conn, mock_cursor = self._make_mock_conn()

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel.fetch_one", return_value={"description": None}),
        ):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=[])

        all_sql = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("DELETE FROM researcher_fields" in sql for sql in all_sql)
        # No inserts since no codes mapped
        assert not any("INSERT" in sql for sql in all_sql)

    def test_migration_keyword_matching(self):
        """Researcher description containing 'migration' should add migration field."""
        mock_conn, mock_cursor = self._make_mock_conn()
        mock_cursor.fetchall.return_value = [(1,), (2,)]

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel.fetch_one", return_value={"description": "studies international migration patterns"}),
        ):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=["J"])

        # The SELECT should include both labour-economics (from J) and migration (from keyword)
        select_call = [c for c in mock_cursor.execute.call_args_list if "SELECT id FROM research_fields" in c[0][0]]
        assert len(select_call) == 1
        slugs_param = select_call[0][0][1]
        assert "migration" in slugs_param
        assert "labour-economics" in slugs_param

    def test_no_description_skips_migration_check(self):
        """When description is None, migration keyword check is skipped gracefully."""
        mock_conn, mock_cursor = self._make_mock_conn()
        mock_cursor.fetchall.return_value = [(1,)]

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel.fetch_one", return_value=None),
        ):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=["E"])

        # Should still work — just maps E to macroeconomics
        select_call = [c for c in mock_cursor.execute.call_args_list if "SELECT id FROM research_fields" in c[0][0]]
        assert len(select_call) == 1
        slugs_param = select_call[0][0][1]
        assert "macroeconomics" in slugs_param


class TestSaveResearcherJelCodesCallsSync:
    """Verify save_researcher_jel_codes triggers field sync."""

    def test_calls_sync_after_saving_codes(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel.sync_researcher_fields_from_jel") as mock_sync,
        ):
            from database.jel import save_researcher_jel_codes
            save_researcher_jel_codes(researcher_id=1, jel_codes=["J", "E"])

        mock_sync.assert_called_once_with(1, ["J", "E"])


class TestAddResearcherJelCodesCallsSync:
    """Verify add_researcher_jel_codes triggers field sync."""

    def test_calls_sync_after_adding_codes(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel.sync_researcher_fields_from_jel") as mock_sync,
        ):
            from database.jel import add_researcher_jel_codes
            add_researcher_jel_codes(researcher_id=1, jel_codes=["F", "G"])

        mock_sync.assert_called_once_with(1, ["F", "G"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_researcher_fields.py -v`
Expected: FAIL — `_JEL_TO_FIELD_SLUGS` does not exist, `sync_researcher_fields_from_jel` does not exist

- [ ] **Step 3: Commit test file**

```bash
git add tests/test_researcher_fields.py
git commit -m "test: add failing tests for researcher_fields derivation from JEL codes

Covers JEL-to-field mapping, sync function, migration keyword fallback,
and integration with save/add JEL code functions.

Closes #68 (tests)"
```

---

### Task 2: JEL-to-Field Mapping and Sync Function

**Files:**
- Modify: `database/jel.py` (add after line 5, before `get_all_jel_codes`)

- [ ] **Step 1: Add the mapping dict and sync function to `database/jel.py`**

Add after the existing imports (line 5), before `get_all_jel_codes` (line 8):

```python
# ── JEL code → research field slug mapping ──────────────────────────
# Maps top-level JEL codes to research_fields.slug values.
# "Migration" has no top-level JEL code — handled via description keywords.
_JEL_TO_FIELD_SLUGS: dict[str, list[str]] = {
    "C": ["econometrics-methods"],
    "E": ["macroeconomics"],
    "F": ["international-trade"],
    "G": ["finance"],
    "H": ["public-economics"],
    "I": ["health-economics"],
    "J": ["labour-economics"],
    "L": ["industrial-organisation"],
    "O": ["development-economics"],
    "P": ["political-economy"],
    "Z": ["cultural-economics"],
}

_MIGRATION_KEYWORDS = ("migration", "immigrant", "immigration", "migrant", "diaspora")


def sync_researcher_fields_from_jel(researcher_id: int, jel_codes: list[str]) -> None:
    """Derive and replace researcher_fields from JEL codes.

    Clears existing field associations and re-derives them from the
    given JEL codes. The 'migration' field is inferred via keyword
    matching on the researcher's description.
    """
    slugs: set[str] = set()
    for code in jel_codes:
        for slug in _JEL_TO_FIELD_SLUGS.get(code.upper().strip(), []):
            slugs.add(slug)

    # Keyword fallback for the migration field (no direct JEL code)
    row = fetch_one(
        "SELECT description FROM researchers WHERE id = %s", (researcher_id,)
    )
    description = row["description"] if row else None
    if description:
        desc_lower = description.lower()
        if any(kw in desc_lower for kw in _MIGRATION_KEYWORDS):
            slugs.add("migration")

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM researcher_fields WHERE researcher_id = %s",
                (researcher_id,),
            )
            if slugs:
                placeholders = ",".join(["%s"] * len(slugs))
                cursor.execute(
                    f"SELECT id FROM research_fields WHERE slug IN ({placeholders})",
                    tuple(slugs),
                )
                field_ids = [r[0] for r in cursor.fetchall()]
                for field_id in field_ids:
                    cursor.execute(
                        "INSERT IGNORE INTO researcher_fields (researcher_id, field_id) "
                        "VALUES (%s, %s)",
                        (researcher_id, field_id),
                    )
            conn.commit()
```

Also add `fetch_one` to the imports on line 5:

```python
from database.connection import execute_query, fetch_all, fetch_one, get_connection
```

- [ ] **Step 2: Run the mapping and sync tests to verify they pass**

Run: `poetry run pytest tests/test_researcher_fields.py::TestJelToFieldMapping tests/test_researcher_fields.py::TestSyncResearcherFieldsFromJel -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add database/jel.py
git commit -m "feat: add JEL-to-field mapping and sync_researcher_fields_from_jel

Maps 11 top-level JEL codes to research field slugs. Migration field
uses keyword matching on researcher descriptions. Sync function
replaces researcher_fields rows based on current JEL assignments."
```

---

### Task 3: Hook Sync into Save and Add Functions

**Files:**
- Modify: `database/jel.py:44-73` (`save_researcher_jel_codes`) — add 1 line at end
- Modify: `database/jel.py:160-189` (`add_researcher_jel_codes`) — add 1 line at end

- [ ] **Step 1: Add sync call at end of `save_researcher_jel_codes`**

After `conn.commit()` on line 73 (inside the function, after the `with` block), add:

```python
    sync_researcher_fields_from_jel(researcher_id, jel_codes)
```

The function should end like:

```python
            conn.commit()
    sync_researcher_fields_from_jel(researcher_id, jel_codes)
```

- [ ] **Step 2: Add sync call at end of `add_researcher_jel_codes`**

After `conn.commit()` on line 189 (inside the function, after the `with` block), add:

```python
    sync_researcher_fields_from_jel(researcher_id, jel_codes)
```

- [ ] **Step 3: Run integration tests to verify sync is called**

Run: `poetry run pytest tests/test_researcher_fields.py::TestSaveResearcherJelCodesCallsSync tests/test_researcher_fields.py::TestAddResearcherJelCodesCallsSync -v`
Expected: PASS

- [ ] **Step 4: Run all existing JEL tests to verify nothing broke**

Run: `poetry run pytest tests/test_jel_enrichment.py tests/test_jel_classifier.py tests/test_researcher_fields.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add database/jel.py
git commit -m "feat: auto-sync researcher_fields when JEL codes change

Both save_researcher_jel_codes (destructive) and add_researcher_jel_codes
(non-destructive) now call sync_researcher_fields_from_jel after persisting
JEL codes, keeping fields in sync automatically."
```

---

### Task 4: Export on Database Facade

**Files:**
- Modify: `database/__init__.py:49-60` (import block) and `database/__init__.py:105-115` (facade class)

- [ ] **Step 1: Add import and static method**

In the import block (around line 59), add `sync_researcher_fields_from_jel`:

```python
from database.jel import (
    get_all_jel_codes as _get_all_jel_codes,
    get_jel_codes_for_researcher as _get_jel_codes_for_researcher,
    get_jel_codes_for_researchers as _get_jel_codes_for_researchers,
    save_researcher_jel_codes as _save_researcher_jel_codes,
    get_researchers_needing_classification as _get_researchers_needing_classification,
    save_paper_topics as _save_paper_topics,
    get_paper_topics_for_researcher as _get_paper_topics_for_researcher,
    get_papers_needing_topics as _get_papers_needing_topics,
    get_all_researcher_topics as _get_all_researcher_topics,
    add_researcher_jel_codes as _add_researcher_jel_codes,
    sync_researcher_fields_from_jel as _sync_researcher_fields_from_jel,
)
```

In the facade class (around line 115), add:

```python
    sync_researcher_fields_from_jel = staticmethod(_sync_researcher_fields_from_jel)
```

- [ ] **Step 2: Verify import works**

Run: `poetry run python -c "from database import Database; print(Database.sync_researcher_fields_from_jel)"`
Expected: Prints function reference without error

- [ ] **Step 3: Commit**

```bash
git add database/__init__.py
git commit -m "feat: export sync_researcher_fields_from_jel on Database facade"
```

---

### Task 5: Backfill Script and Makefile Target

**Files:**
- Create: `scripts/backfill_researcher_fields.py`
- Modify: `Makefile` (add 1 target)

- [ ] **Step 1: Create the backfill script**

```python
#!/usr/bin/env python3
"""Backfill researcher_fields from existing researcher_jel_codes.

Run once after deploying the JEL-to-field sync feature to populate
fields for all researchers who already have JEL codes assigned.

Usage: poetry run python scripts/backfill_researcher_fields.py
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from database import Database
from database.jel import sync_researcher_fields_from_jel

logger = logging.getLogger(__name__)


def backfill() -> int:
    Database.create_tables()

    rows = Database.fetch_all(
        """SELECT r.id, GROUP_CONCAT(rjc.jel_code) AS codes
           FROM researchers r
           JOIN researcher_jel_codes rjc ON rjc.researcher_id = r.id
           GROUP BY r.id"""
    )
    updated = 0
    for row in rows:
        codes = row["codes"].split(",") if row["codes"] else []
        if codes:
            sync_researcher_fields_from_jel(row["id"], codes)
            updated += 1
            if updated % 50 == 0:
                logger.info("Processed %d/%d researchers", updated, len(rows))

    logger.info("Backfilled researcher_fields for %d researchers", updated)
    return updated


if __name__ == "__main__":
    backfill()
```

- [ ] **Step 2: Add Makefile target**

Add to `.PHONY` line and after `backfill-normalize`:

```makefile
populate-fields:  ## Backfill researcher_fields from JEL codes (one-time)
	poetry run python scripts/backfill_researcher_fields.py
```

Update the `.PHONY` line to include `populate-fields`.

- [ ] **Step 3: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_researcher_fields.py Makefile
git commit -m "feat: add backfill script and make target for researcher_fields

make populate-fields runs a one-time backfill that derives
researcher_fields from existing JEL code assignments.

Closes #68"
```

---

### Task 6: Add .dockerignore Entry

**Files:**
- Modify: `.dockerignore`

- [ ] **Step 1: Check if `scripts/backfill_researcher_fields.py` needs to be whitelisted**

Per CLAUDE.md gotcha: `.dockerignore` uses a whitelist. The script needs to be available in the Docker image for production backfill.

Check if `scripts/` files are already whitelisted with a glob pattern like `!scripts/*.py`. If not, add:

```
!scripts/backfill_researcher_fields.py
```

- [ ] **Step 2: Commit if changed**

```bash
git add .dockerignore
git commit -m "chore: whitelist backfill_researcher_fields.py in .dockerignore"
```

---

### Verification Checklist

After all tasks are complete:

1. `poetry run pytest -v` — all tests pass
2. `cd app && npx tsc --noEmit` — no type errors
3. Field filter on `/researchers` returns results after running `make populate-fields` on a DB with JEL codes
4. New researchers classified via `make classify-jel` automatically get fields populated
5. Papers enriched via `make enrich-jel` also propagate fields to their researchers
