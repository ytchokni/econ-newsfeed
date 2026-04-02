# Bad Name Cleanup & Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete 8 researcher records with bad names (empty first_name or initial-only last_name) and prevent future bad names via dual-layer validation.

**Architecture:** Layer 1 filters bad names at two entry points (OpenAlex coauthors, LLM extraction). Layer 2 validates in `get_researcher_id()` before INSERT as a safety net. A one-time script cleans existing bad data.

**Tech Stack:** Python, MySQL (parameterized queries), Pydantic, pytest

---

### Task 1: Add name validation helper to `database/researchers.py`

**Files:**
- Modify: `database/researchers.py:1-22` (add helper near existing helpers)
- Test: `tests/test_researcher_dedup.py` (add new test class)

- [ ] **Step 1: Write failing tests for `is_bad_researcher_name`**

Add to `tests/test_researcher_dedup.py`:

```python
class TestIsBadResearcherName:
    """is_bad_researcher_name rejects empty first names and initial-only last names."""

    def test_empty_first_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("", "Smith") is True

    def test_whitespace_first_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("  ", "Smith") is True

    def test_initial_only_last_name_with_period(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("Eric", "A.") is True

    def test_initial_only_last_name_without_period(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("David", "K") is True

    def test_empty_last_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("John", "") is True

    def test_whitespace_last_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("John", "  ") is True

    def test_valid_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("John", "Smith") is False

    def test_short_but_valid_last_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("Yi", "Li") is False

    def test_initial_first_name_is_ok(self):
        """Single-letter first names like 'J.' are fine — it's last names we reject."""
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("J.", "Smith") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_researcher_dedup.py::TestIsBadResearcherName -v`
Expected: FAIL with `ImportError: cannot import name 'is_bad_researcher_name'`

- [ ] **Step 3: Implement `is_bad_researcher_name`**

Add to `database/researchers.py` after the existing `_strip_initial` function (after line 21):

```python
def is_bad_researcher_name(first_name: str, last_name: str) -> bool:
    """Return True if the name is too malformed to create a researcher record.

    Rejects: empty/whitespace first or last names, single-letter/initial-only last names.
    """
    if not first_name or not first_name.strip():
        return True
    if not last_name or not last_name.strip():
        return True
    # Single letter with optional period: "A", "A.", "K", "K."
    stripped_last = last_name.strip()
    if re.match(r'^[A-Za-z]\.?$', stripped_last):
        return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_researcher_dedup.py::TestIsBadResearcherName -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add database/researchers.py tests/test_researcher_dedup.py
git commit -m "feat: add is_bad_researcher_name validation helper (#73)"
```

---

### Task 2: Add DB-level guard in `get_researcher_id()` (Layer 2)

**Files:**
- Modify: `database/researchers.py:94-200` (add validation before INSERT at line 195)
- Test: `tests/test_researcher_dedup.py` (add new test class)

- [ ] **Step 1: Write failing tests for the guard**

Add to `tests/test_researcher_dedup.py`:

```python
class TestGetResearcherIdNameGuard:
    """get_researcher_id returns None for bad names instead of inserting."""

    @patch("database.researchers._disambiguate_researcher", return_value=None)
    @patch("database.researchers.fetch_all", return_value=[])
    @patch("database.researchers.fetch_one", return_value=None)
    @patch("database.researchers.execute_query")
    def test_rejects_empty_first_name(self, mock_exec, mock_one, mock_all, mock_disamb):
        result = get_researcher_id("", "Anastakis")
        assert result is None
        mock_exec.assert_not_called()

    @patch("database.researchers._disambiguate_researcher", return_value=None)
    @patch("database.researchers.fetch_all", return_value=[])
    @patch("database.researchers.fetch_one", return_value=None)
    @patch("database.researchers.execute_query")
    def test_rejects_initial_last_name(self, mock_exec, mock_one, mock_all, mock_disamb):
        result = get_researcher_id("Eric", "A.")
        assert result is None
        mock_exec.assert_not_called()

    @patch("database.researchers.fetch_all", return_value=[])
    @patch("database.researchers.fetch_one", return_value=None)
    @patch("database.researchers.execute_query", return_value=99)
    def test_allows_valid_name(self, mock_exec, mock_one, mock_all):
        result = get_researcher_id("John", "Smith")
        assert result == 99
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_researcher_dedup.py::TestGetResearcherIdNameGuard -v`
Expected: `test_rejects_empty_first_name` and `test_rejects_initial_last_name` FAIL (they return an id instead of None)

- [ ] **Step 3: Add the guard to `get_researcher_id()`**

In `database/researchers.py`, add the validation at the very top of `get_researcher_id()`, right after the function docstring (before the `_fetch_one` helper definition at line 107):

```python
    # Name validation guard — reject bad names before any DB interaction
    if is_bad_researcher_name(first_name, last_name):
        logging.warning(
            "Rejected bad researcher name: first_name=%r last_name=%r", first_name, last_name
        )
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_researcher_dedup.py::TestGetResearcherIdNameGuard -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Run all researcher dedup tests to check for regressions**

Run: `poetry run pytest tests/test_researcher_dedup.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add database/researchers.py tests/test_researcher_dedup.py
git commit -m "feat: guard get_researcher_id against bad names (#73)"
```

---

### Task 3: Filter bad coauthor names in OpenAlex ingestion (Layer 1A)

**Files:**
- Modify: `openalex.py:154-165` (filter inside `_parse_work()`)
- Test: `tests/test_openalex.py` (add new test class)

- [ ] **Step 1: Write failing tests for coauthor filtering**

Add to `tests/test_openalex.py`:

```python
class TestParseWorkCoauthorFiltering:
    """_parse_work skips coauthors with bad display_names."""

    def test_skips_empty_display_name(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "", "id": "https://openalex.org/A1"}},
                {"author": {"display_name": "John Smith", "id": "https://openalex.org/A2"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 1
        assert result["coauthors"][0]["display_name"] == "John Smith"

    def test_skips_initial_only_first_name(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "A. Smith", "id": "https://openalex.org/A1"}},
                {"author": {"display_name": "John Doe", "id": "https://openalex.org/A2"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        # "A. Smith" has initial-only first part — should be skipped
        assert len(result["coauthors"]) == 1
        assert result["coauthors"][0]["display_name"] == "John Doe"

    def test_keeps_valid_coauthors(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "Jane Doe", "id": "https://openalex.org/A1"}},
                {"author": {"display_name": "John Smith", "id": "https://openalex.org/A2"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 2

    def test_skips_whitespace_only_name(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "   ", "id": "https://openalex.org/A1"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 0

    def test_keeps_single_word_name(self):
        """Some authors have mononyms (e.g. 'Sukarno'). These are valid."""
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "Sukarno", "id": "https://openalex.org/A1"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_openalex.py::TestParseWorkCoauthorFiltering -v`
Expected: `test_skips_empty_display_name`, `test_skips_initial_only_first_name`, `test_skips_whitespace_only_name` FAIL (coauthors list still contains them)

- [ ] **Step 3: Add filtering to `_parse_work()`**

In `openalex.py`, add a helper function before `_parse_work()` and modify the coauthor loop:

Add before `_parse_work()` (around line 153):

```python
def _is_bad_coauthor_name(display_name: str) -> bool:
    """Return True if a coauthor display_name is too incomplete to store."""
    name = display_name.strip()
    if not name:
        return True
    parts = name.split()
    # Single-initial first part: "A.", "A", "J." etc.
    if len(parts) >= 2:
        first = parts[0]
        if len(first.rstrip('.')) <= 1:
            return True
    return False
```

Then modify the coauthor loop in `_parse_work()` (lines 159-165) from:

```python
    coauthors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        coauthors.append({
            "display_name": author.get("display_name", ""),
            "openalex_author_id": _strip_prefix(author.get("id"), _OPENALEX_PREFIX),
        })
```

to:

```python
    coauthors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        display_name = author.get("display_name", "")
        if _is_bad_coauthor_name(display_name):
            logger.debug("Skipping coauthor with bad name: %r", display_name)
            continue
        coauthors.append({
            "display_name": display_name,
            "openalex_author_id": _strip_prefix(author.get("id"), _OPENALEX_PREFIX),
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_openalex.py::TestParseWorkCoauthorFiltering -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Run all openalex tests to check for regressions**

Run: `poetry run pytest tests/test_openalex.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add openalex.py tests/test_openalex.py
git commit -m "feat: filter bad coauthor names in OpenAlex ingestion (#73)"
```

---

### Task 4: Discard LLM publications with bad author names (Layer 1B)

**Files:**
- Modify: `publication.py:102-161` (add check in `validate_publication()`)
- Test: `tests/test_validate_publication.py` (add new test class)

- [ ] **Step 1: Write failing tests for bad author name rejection**

Add to `tests/test_validate_publication.py`:

```python
class TestBadAuthorNameRejection:
    """Reject entire publication if any author has a bad name."""

    def test_rejects_empty_first_name(self):
        pub = {
            "title": "The Effect of Trade on Growth",
            "authors": [["", "Anastakis"], ["John", "Smith"]],
        }
        assert validate_publication(pub) is False

    def test_rejects_initial_only_last_name_with_period(self):
        pub = {
            "title": "The Effect of Trade on Growth",
            "authors": [["Eric", "A."]],
        }
        assert validate_publication(pub) is False

    def test_rejects_initial_only_last_name_without_period(self):
        pub = {
            "title": "The Effect of Trade on Growth",
            "authors": [["David", "K"]],
        }
        assert validate_publication(pub) is False

    def test_accepts_valid_authors(self):
        pub = {
            "title": "The Effect of Trade on Growth",
            "authors": [["John", "Smith"], ["Jane", "Doe"]],
        }
        assert validate_publication(pub) is True

    def test_accepts_initial_first_name(self):
        """First name initials like 'J.' are fine — only last name initials are bad."""
        pub = {
            "title": "The Effect of Trade on Growth",
            "authors": [["J.", "Smith"]],
        }
        assert validate_publication(pub) is True

    def test_rejects_whitespace_first_name(self):
        pub = {
            "title": "The Effect of Trade on Growth",
            "authors": [["  ", "Smith"]],
        }
        assert validate_publication(pub) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_validate_publication.py::TestBadAuthorNameRejection -v`
Expected: `test_rejects_empty_first_name`, `test_rejects_initial_only_last_name_with_period`, `test_rejects_initial_only_last_name_without_period`, `test_rejects_whitespace_first_name` FAIL

- [ ] **Step 3: Add bad author name check to `validate_publication()`**

In `publication.py`, add the following check inside `validate_publication()` after the existing `# Reject copyright notices` block (after line 127) and before `# Reject GitHub as venue`:

```python
    # Reject if any author has empty first name or initial-only last name
    for author in authors:
        if not author or len(author) < 2:
            continue
        first = author[0].strip() if author[0] else ""
        last = author[-1].strip() if author[-1] else ""
        if not first:
            return False
        if re.match(r'^[A-Za-z]\.?$', last):
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_validate_publication.py::TestBadAuthorNameRejection -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Run all validate_publication tests to check for regressions**

Run: `poetry run pytest tests/test_validate_publication.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_validate_publication.py
git commit -m "feat: reject publications with bad author names (#73)"
```

---

### Task 5: Create one-time cleanup script

**Files:**
- Create: `scripts/cleanup_bad_names.py`

- [ ] **Step 1: Write the cleanup script**

Create `scripts/cleanup_bad_names.py` (following the pattern from `scripts/cleanup_garbage_papers.py`):

```python
"""One-time cleanup: delete researcher records with bad names.

Identifies researchers with empty first names or initial-only last names
(from OpenAlex coauthors or LLM misparsing). Uses CASCADE deletion so
authorship, researcher_jel_codes, researcher_urls, etc. are cleaned up.

Usage: poetry run python scripts/cleanup_bad_names.py [--dry-run]
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def find_bad_name_researchers() -> list[dict]:
    """Find researchers with empty first names or initial-only last names."""
    return Database.fetch_all("""
        SELECT r.id, r.first_name, r.last_name,
               COUNT(a.publication_id) AS pub_count
        FROM researchers r
        LEFT JOIN authorship a ON a.researcher_id = r.id
        WHERE
            TRIM(r.first_name) = ''
            OR r.first_name IS NULL
            OR TRIM(r.last_name) = ''
            OR r.last_name IS NULL
            OR r.last_name REGEXP '^[A-Za-z]\\.?$'
        GROUP BY r.id
        ORDER BY r.last_name, r.first_name
    """)


def find_suspicious_researchers() -> list[dict]:
    """Find researchers with other suspicious name patterns for manual review."""
    return Database.fetch_all("""
        SELECT r.id, r.first_name, r.last_name,
               COUNT(a.publication_id) AS pub_count
        FROM researchers r
        LEFT JOIN authorship a ON a.researcher_id = r.id
        WHERE
            LENGTH(TRIM(r.first_name)) = 1
            OR r.first_name REGEXP '^[^a-zA-Z]'
            OR r.last_name REGEXP '^[^a-zA-Z]'
        GROUP BY r.id
        ORDER BY r.last_name, r.first_name
    """)


def main():
    dry_run = "--dry-run" in sys.argv

    # Auto-delete: clearly bad records
    bad = find_bad_name_researchers()
    logger.info("Found %d researchers with bad names:", len(bad))
    for r in bad:
        logger.info("  [%d] '%s' '%s' (%d pubs)", r["id"], r["first_name"], r["last_name"], r["pub_count"])

    # Manual review: suspicious but not auto-deleted
    suspicious = find_suspicious_researchers()
    # Exclude already-found bad records
    bad_ids = {r["id"] for r in bad}
    suspicious = [r for r in suspicious if r["id"] not in bad_ids]
    if suspicious:
        logger.info("\nSuspicious names (manual review, NOT auto-deleted):")
        for r in suspicious:
            logger.info("  [%d] '%s' '%s' (%d pubs)", r["id"], r["first_name"], r["last_name"], r["pub_count"])

    if dry_run:
        logger.info("\nDry run -- no deletions made. Remove --dry-run to delete.")
        return

    if not bad:
        logger.info("No bad name researchers to delete.")
        return

    ids = [r["id"] for r in bad]
    placeholders = ",".join(["%s"] * len(ids))
    Database.execute_query(
        f"DELETE FROM researchers WHERE id IN ({placeholders})",
        tuple(ids),
    )
    logger.info("\nDeleted %d researchers with bad names (CASCADE cleaned child rows)", len(ids))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script loads without errors**

Run: `poetry run python scripts/cleanup_bad_names.py --dry-run 2>&1 || echo "Script loaded but needs DB connection (expected in dev)"`

Expected: either lists bad researchers or fails with a DB connection error (expected if no local DB). The important thing is no import errors.

- [ ] **Step 3: Commit**

```bash
git add scripts/cleanup_bad_names.py
git commit -m "feat: add cleanup script for bad researcher names (#73)"
```

---

### Task 6: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run all Python tests**

Run: `poetry run pytest -v`
Expected: all tests PASS, no regressions

- [ ] **Step 2: Run TypeScript type check**

Run: `cd app && npx tsc --noEmit`
Expected: PASS (no frontend changes, but verify nothing is broken)

- [ ] **Step 3: Final commit (if any fixes needed)**

Only if tests revealed issues that needed fixing.
