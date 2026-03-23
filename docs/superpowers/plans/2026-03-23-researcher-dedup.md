# Researcher Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent and clean up duplicate researcher records caused by first-name initial vs full-name mismatches (e.g., "L. Wren-Lewis" vs "Liam Wren-Lewis").

**Architecture:** A new initial-matching tier in `get_researcher_id()` catches abbreviation variants before LLM disambiguation. A reusable `merge_researchers()` function transfers authorship, JEL codes, and metadata from duplicate to canonical record. A one-time cleanup script scans existing researchers for these patterns.

**Tech Stack:** Python, MySQL (parameterized SQL via mysql-connector-python), pytest with unittest.mock

**Spec:** `docs/superpowers/specs/2026-03-23-researcher-dedup-design.md`

**Worktree:** `.worktrees/researcher-dedup` (branch `feature/researcher-dedup`)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `database/researchers.py` | Add `first_name_is_initial_match()`, `merge_researchers()`, insert Tier 1.5 in `get_researcher_id()` |
| `database/__init__.py` | Expose `merge_researchers` on `Database` facade |
| `scripts/merge_duplicate_researchers.py` | New — one-time cleanup script with dry-run/execute modes |
| `tests/test_researcher_dedup.py` | New — unit tests for initial matching, Tier 1.5 integration, merge logic |

---

## Task 1: `first_name_is_initial_match()` function

**Files:**
- Modify: `database/researchers.py` (add function after line 11)
- Test: `tests/test_researcher_dedup.py` (create)

- [ ] **Step 1: Write failing tests for `first_name_is_initial_match`**

Create `tests/test_researcher_dedup.py`:

```python
"""Tests for researcher deduplication: initial matching and merge logic."""
import pytest
from database.researchers import first_name_is_initial_match


class TestFirstNameIsInitialMatch:
    """first_name_is_initial_match returns True when one name is a single-char
    initial (with or without period) matching the other's first character."""

    def test_initial_with_period_matches_full_name(self):
        assert first_name_is_initial_match("L.", "Liam") is True

    def test_initial_without_period_matches_full_name(self):
        assert first_name_is_initial_match("L", "Liam") is True

    def test_case_insensitive(self):
        assert first_name_is_initial_match("l.", "Liam") is True

    def test_reversed_order(self):
        assert first_name_is_initial_match("Liam", "L.") is True

    def test_exact_match_returns_false(self):
        assert first_name_is_initial_match("Liam", "Liam") is False

    def test_two_char_prefix_returns_false(self):
        assert first_name_is_initial_match("Li", "Liam") is False

    def test_different_initial_returns_false(self):
        assert first_name_is_initial_match("J.", "Liam") is False

    def test_both_initials_same_letter(self):
        assert first_name_is_initial_match("L.", "L") is True

    def test_both_initials_different_letter(self):
        assert first_name_is_initial_match("L.", "J.") is False

    def test_empty_string_returns_false(self):
        assert first_name_is_initial_match("", "Liam") is False

    def test_both_empty_returns_false(self):
        assert first_name_is_initial_match("", "") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest tests/test_researcher_dedup.py -v`
Expected: FAIL with `ImportError: cannot import name 'first_name_is_initial_match'`

- [ ] **Step 3: Implement `first_name_is_initial_match`**

Add to `database/researchers.py` after the imports (after line 11):

```python
def _strip_initial(name: str) -> str | None:
    """If name is a single letter optionally followed by '.', return that letter lowercase. Else None."""
    stripped = name.strip()
    if len(stripped) == 1 and stripped.isalpha():
        return stripped.lower()
    if len(stripped) == 2 and stripped[0].isalpha() and stripped[1] == '.':
        return stripped[0].lower()
    return None


def first_name_is_initial_match(name_a: str, name_b: str) -> bool:
    """Return True when one name is a single-char initial matching the other's first character.

    Handles 'L.', 'L', or 'l.' matching 'Liam'. Returns False for exact matches,
    multi-char prefixes, or different initials.
    """
    if not name_a or not name_b:
        return False
    init_a = _strip_initial(name_a)
    init_b = _strip_initial(name_b)
    # Both are full names (no initial) — not an initial match
    if init_a is None and init_b is None:
        return False
    # Both are initials — compare them
    if init_a is not None and init_b is not None:
        return init_a == init_b
    # One is initial, one is full name — compare initial to first char
    if init_a is not None:
        return init_a == name_b[0].lower()
    return init_b == name_a[0].lower()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest tests/test_researcher_dedup.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup
git add tests/test_researcher_dedup.py database/researchers.py
git commit -m "feat: add first_name_is_initial_match for researcher dedup"
```

---

## Task 2: Tier 1.5 initial matching in `get_researcher_id()`

**Files:**
- Modify: `database/researchers.py:97-103` (insert new tier after exact match)
- Test: `tests/test_researcher_dedup.py` (add integration tests)

- [ ] **Step 1: Write failing tests for Tier 1.5**

Append to `tests/test_researcher_dedup.py`:

```python
from unittest.mock import patch, MagicMock


class TestGetResearcherIdInitialTier:
    """Tier 1.5: initial matching in get_researcher_id()."""

    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    @patch("database.researchers.execute_query")
    def test_initial_matches_single_candidate(self, mock_exec, mock_one, mock_all):
        """'L.' + existing 'Liam' with same last name -> returns existing id."""
        # Tier 1 exact match fails
        mock_one.side_effect = [None]
        # Tier 1.5 same-last-name query returns one candidate
        mock_all.return_value = [{"id": 49, "first_name": "L.", "last_name": "Wren-Lewis"}]
        # UPDATE first_name returns None (no lastrowid needed)
        mock_exec.return_value = None

        result = get_researcher_id("Liam", "Wren-Lewis")
        assert result == 49
        # Should have updated first_name to the longer name
        update_call = mock_exec.call_args
        assert "UPDATE researchers SET first_name" in update_call[0][0]
        assert "Liam" in update_call[0][1]

    @patch("database.researchers._disambiguate_researcher")
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    def test_multiple_initial_matches_falls_through(self, mock_one, mock_all, mock_disamb):
        """Multiple initial matches -> skip Tier 1.5, fall through to LLM."""
        mock_one.side_effect = [None, None]  # Tier 1 + Tier 2 fail
        mock_all.return_value = [
            {"id": 10, "first_name": "L.", "last_name": "Smith"},
            {"id": 20, "first_name": "Liam", "last_name": "Smith"},
        ]
        mock_disamb.return_value = 10

        result = get_researcher_id("L", "Smith")
        assert result == 10
        mock_disamb.assert_called_once()

    @patch("database.researchers._disambiguate_researcher", return_value=None)
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    @patch("database.researchers.execute_query")
    def test_no_initial_match_falls_through_to_insert(self, mock_exec, mock_one, mock_all, mock_disamb):
        """No initial match and no LLM match -> inserts new researcher."""
        mock_one.side_effect = [None, None]  # Tier 1 + Tier 2 fail
        mock_all.return_value = [
            {"id": 10, "first_name": "John", "last_name": "Smith"},
        ]
        mock_exec.return_value = 99  # new id from INSERT

        result = get_researcher_id("Robert", "Smith")
        assert result == 99
```

Also add the import at the top of the file (alongside the existing import):

```python
from database.researchers import first_name_is_initial_match, get_researcher_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest tests/test_researcher_dedup.py::TestGetResearcherIdInitialTier -v`
Expected: FAIL — `test_initial_matches_single_candidate` will not return 49 because Tier 1.5 doesn't exist yet

- [ ] **Step 3: Implement Tier 1.5 in `get_researcher_id()`**

In `database/researchers.py`, modify `get_researcher_id()`. After the Tier 1 exact match block (after `return result['id']` on line 103), insert:

```python
    # 1.5. Initial match — "L." matches "Liam" for same last name
    candidates = _fetch_all(
        "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
        (last_name,),
    )
    if candidates:
        initial_matches = [
            c for c in candidates
            if first_name_is_initial_match(first_name, c['first_name'])
        ]
        if len(initial_matches) == 1:
            match = initial_matches[0]
            longer_name = first_name if len(first_name) > len(match['first_name']) else match['first_name']
            _execute(
                "UPDATE researchers SET first_name = %s WHERE id = %s",
                (longer_name, match['id']),
            )
            logging.info(
                f"Initial matched '{first_name} {last_name}' to researcher id={match['id']} ('{match['first_name']} {match['last_name']}')"
            )
            return match['id']
```

Also update the existing Tier 3 (LLM disambiguation) to reuse the `candidates` variable already fetched above, instead of querying again. Replace the old Tier 3 block:

```python
    # 3. Same-last-name candidates — let LLM decide if any is the same person
    if candidates is None:
        candidates = _fetch_all(
            "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
            (last_name,),
        )
    if candidates:
```

And update the docstring to reflect the new matching priority:

```python
    """Get the researcher ID based on name.

    Matching priority:
    1. Exact first_name + last_name match
    1.5. Initial match — single-char initial matches full first name (same last name)
    2. OpenAlex author ID match (deterministic, free)
    3. LLM disambiguation for same-last-name candidates
    4. Insert new researcher
    """
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest tests/test_researcher_dedup.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest --ignore=tests/test_api_search.py -v`
Expected: All tests PASS (excluding the 5 pre-existing search test failures)

- [ ] **Step 6: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup
git add database/researchers.py tests/test_researcher_dedup.py
git commit -m "feat: add Tier 1.5 initial matching in get_researcher_id"
```

---

## Task 3: `merge_researchers()` function

**Files:**
- Modify: `database/researchers.py` (add function)
- Modify: `database/__init__.py` (expose on facade)
- Test: `tests/test_researcher_dedup.py` (add merge tests)

- [ ] **Step 1: Write failing tests for `merge_researchers`**

Append to `tests/test_researcher_dedup.py`:

```python
from database.researchers import merge_researchers


class TestMergeResearchers:
    """merge_researchers transfers authorship, JEL codes, metadata, then deletes duplicate."""

    def _make_mock_conn(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_transfers_authorship_and_deletes_duplicate(self):
        conn, cursor = self._make_mock_conn()
        # Fetch canonical researcher info
        cursor.fetchone.side_effect = [
            {"first_name": "L.", "last_name": "Smith", "affiliation": None,
             "description": None, "position": None, "openalex_author_id": None},
            {"first_name": "Liam", "last_name": "Smith", "affiliation": "Oxford",
             "description": "Economist", "position": "Prof", "openalex_author_id": "A123"},
        ]
        merge_researchers(10, 20, conn)

        executed = [call[0][0] for call in cursor.execute.call_args_list]
        # Should delete overlapping authorship
        assert any("DELETE FROM authorship" in q for q in executed)
        # Should update remaining authorship
        assert any("UPDATE authorship SET researcher_id" in q for q in executed)
        # Should transfer JEL codes
        assert any("UPDATE IGNORE researcher_jel_codes" in q for q in executed)
        # Should update first_name to longer
        assert any("UPDATE researchers SET first_name" in q for q in executed)
        # Should backfill metadata
        assert any("affiliation" in q and "UPDATE researchers" in q for q in executed)
        # Should delete duplicate
        assert any("DELETE FROM researchers WHERE id" in q for q in executed)
        # Should commit
        conn.commit.assert_called_once()

    def test_keeps_longer_first_name(self):
        conn, cursor = self._make_mock_conn()
        cursor.fetchone.side_effect = [
            {"first_name": "L.", "last_name": "Smith", "affiliation": None,
             "description": None, "position": None, "openalex_author_id": None},
            {"first_name": "Liam", "last_name": "Smith", "affiliation": None,
             "description": None, "position": None, "openalex_author_id": None},
        ]
        merge_researchers(10, 20, conn)

        # Find the UPDATE first_name call
        for call in cursor.execute.call_args_list:
            query = call[0][0]
            if "UPDATE researchers SET first_name" in query:
                params = call[0][1]
                assert params[0] == "Liam"  # longer name
                break

    def test_raises_if_canonical_equals_duplicate(self):
        conn, _ = self._make_mock_conn()
        with pytest.raises(ValueError, match="same"):
            merge_researchers(10, 10, conn)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest tests/test_researcher_dedup.py::TestMergeResearchers -v`
Expected: FAIL with `ImportError: cannot import name 'merge_researchers'`

- [ ] **Step 3: Implement `merge_researchers`**

Add to `database/researchers.py` (after the `get_researcher_id` function):

```python
def merge_researchers(canonical_id: int, duplicate_id: int, conn) -> None:
    """Merge duplicate researcher into canonical: transfer authorship, JEL codes, metadata, then delete.

    All operations run in the provided connection's transaction (caller manages commit/rollback).
    """
    if canonical_id == duplicate_id:
        raise ValueError(f"Cannot merge researcher into itself (same id={canonical_id})")

    c = conn.cursor(dictionary=True)

    # Fetch both researchers (need metadata columns for backfill)
    c.execute(
        "SELECT first_name, last_name, affiliation, description, position, openalex_author_id "
        "FROM researchers WHERE id = %s", (canonical_id,),
    )
    canonical = c.fetchone()
    c.execute(
        "SELECT first_name, last_name, affiliation, description, position, openalex_author_id "
        "FROM researchers WHERE id = %s", (duplicate_id,),
    )
    duplicate = c.fetchone()

    if not canonical or not duplicate:
        c.close()
        raise ValueError(f"Researcher not found: canonical={canonical_id} duplicate={duplicate_id}")

    # 1. Transfer authorship (two-step to avoid unique constraint violations)
    c.execute(
        "DELETE FROM authorship WHERE researcher_id = %s "
        "AND publication_id IN (SELECT publication_id FROM "
        "(SELECT publication_id FROM authorship WHERE researcher_id = %s) AS tmp)",
        (duplicate_id, canonical_id),
    )
    c.execute(
        "UPDATE authorship SET researcher_id = %s WHERE researcher_id = %s",
        (canonical_id, duplicate_id),
    )

    # 2. Transfer JEL codes (IGNORE skips duplicates)
    c.execute(
        "UPDATE IGNORE researcher_jel_codes SET researcher_id = %s WHERE researcher_id = %s",
        (canonical_id, duplicate_id),
    )

    # 3. Upgrade first_name to the longer variant
    longer_name = canonical['first_name'] if len(canonical['first_name']) > len(duplicate['first_name']) else duplicate['first_name']
    c.execute(
        "UPDATE researchers SET first_name = %s WHERE id = %s",
        (longer_name, canonical_id),
    )

    # 4. Backfill metadata where canonical has NULL
    c.execute(
        "UPDATE researchers SET "
        "affiliation = COALESCE(affiliation, %s), "
        "description = COALESCE(description, %s), "
        "position = COALESCE(position, %s), "
        "openalex_author_id = COALESCE(openalex_author_id, %s) "
        "WHERE id = %s",
        (duplicate.get('affiliation'), duplicate.get('description'),
         duplicate.get('position'), duplicate.get('openalex_author_id'),
         canonical_id),
    )

    # 5. Delete duplicate (cascade handles researcher_urls, html_content, researcher_fields, etc.)
    c.execute("DELETE FROM researchers WHERE id = %s", (duplicate_id,))

    c.close()
    conn.commit()

    # 6. Log
    logging.info(
        f"Merged researcher #{duplicate_id} ({duplicate['first_name']} {duplicate['last_name']}) "
        f"into #{canonical_id} ({canonical['first_name']} {canonical['last_name']})"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest tests/test_researcher_dedup.py::TestMergeResearchers -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Expose `merge_researchers` on Database facade**

In `database/__init__.py`, add the import (alongside existing researchers imports):

```python
from database.researchers import (
    get_researcher_id as _get_researcher_id,
    update_researcher_bio as _update_researcher_bio,
    add_researcher_url as _add_researcher_url,
    import_data_from_file as _import_data_from_file,
    first_name_is_initial_match as _first_name_is_initial_match,
    merge_researchers as _merge_researchers,
)
```

And add to the `Database` class under `# Researchers`:

```python
    first_name_is_initial_match = staticmethod(_first_name_is_initial_match)
    merge_researchers = staticmethod(_merge_researchers)
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest --ignore=tests/test_api_search.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup
git add database/researchers.py database/__init__.py tests/test_researcher_dedup.py
git commit -m "feat: add merge_researchers function for researcher dedup"
```

---

## Task 4: One-time cleanup script

**Files:**
- Create: `scripts/merge_duplicate_researchers.py`
- Test: manual dry-run verification

- [ ] **Step 1: Create the cleanup script**

Create `scripts/merge_duplicate_researchers.py`:

```python
"""One-time script to find and merge duplicate researchers with initial-matching names.

Dry-run by default. Pass --execute to actually merge.

Usage:
    poetry run python scripts/merge_duplicate_researchers.py           # dry-run
    poetry run python scripts/merge_duplicate_researchers.py --execute  # merge
"""
import argparse
import logging
import sys

# Ensure project root is importable
sys.path.insert(0, ".")

from database.researchers import first_name_is_initial_match, merge_researchers
from database.connection import fetch_all, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def find_initial_match_pairs() -> list[tuple[dict, dict]]:
    """Find researcher pairs where first names are initial matches within the same last name."""
    researchers = fetch_all(
        "SELECT r.id, r.first_name, r.last_name, "
        "EXISTS(SELECT 1 FROM researcher_urls WHERE researcher_id = r.id) AS has_urls "
        "FROM researchers r ORDER BY r.last_name, r.id",
        (),
    )

    # Group by last_name
    groups: dict[str, list[dict]] = {}
    for r in researchers:
        groups.setdefault(r['last_name'], []).append(r)

    pairs = []
    for last_name, members in groups.items():
        if len(members) < 2:
            continue
        # Check all pairs within the group
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if first_name_is_initial_match(a['first_name'], b['first_name']):
                    # Canonical = has URLs, else lower ID
                    if b['has_urls'] and not a['has_urls']:
                        canonical, duplicate = b, a
                    elif a['has_urls'] and not b['has_urls']:
                        canonical, duplicate = a, b
                    else:
                        canonical, duplicate = (a, b) if a['id'] < b['id'] else (b, a)
                    pairs.append((canonical, duplicate))

    return pairs


def main():
    parser = argparse.ArgumentParser(description="Merge duplicate researchers with initial-matching names.")
    parser.add_argument("--execute", action="store_true", help="Actually merge (default is dry-run)")
    args = parser.parse_args()

    pairs = find_initial_match_pairs()

    if not pairs:
        logging.info("No duplicate researchers found.")
        return

    for canonical, duplicate in pairs:
        label = "MERGING" if args.execute else "WOULD MERGE"
        logging.info(
            f"{label}: #{duplicate['id']} ({duplicate['first_name']} {duplicate['last_name']}) "
            f"-> #{canonical['id']} ({canonical['first_name']} {canonical['last_name']})"
        )

    if not args.execute:
        logging.info(f"Dry run: {len(pairs)} pair(s) found. Re-run with --execute to merge.")
        return

    conn = get_connection()
    try:
        for canonical, duplicate in pairs:
            merge_researchers(canonical['id'], duplicate['id'], conn)
        logging.info(f"Merged {len(pairs)} duplicate researcher(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script is importable (syntax check)**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run python -c "import ast; ast.parse(open('scripts/merge_duplicate_researchers.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup
git add scripts/merge_duplicate_researchers.py
git commit -m "feat: add one-time researcher dedup cleanup script"
```

---

## Task 5: Final verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && poetry run pytest --ignore=tests/test_api_search.py -v`
Expected: All tests PASS (same 397+ count as baseline, plus new tests)

- [ ] **Step 2: Verify no untracked/unstaged changes**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && git status`
Expected: clean working tree

- [ ] **Step 3: Review commit log**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/researcher-dedup && git log --oneline main..HEAD`
Expected: 3-4 commits for the feature
