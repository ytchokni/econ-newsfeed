# Bad Name Cleanup & Prevention — Design Spec

**Date:** 2026-04-02
**Issue:** #73 (partial — items 1 & 2: empty first names and garbage last names)
**Priority:** Medium

## Problem

8 researcher records have bad names from two sources:
- **6 records** with empty `first_name` — OpenAlex coauthors where first names were unavailable (IDs: 6575, 6580, 6572, 6567, 6573, 6570)
- **2 records** with initial-only `last_name` — LLM misparse of names like "Eric A." into first="Eric", last="A." (IDs: 7395, 6239)

These display poorly in the frontend (e.g. " Anastakis" with leading space) and erode user trust.

## Approach: Cleanup + dual-layer prevention

1. One-time cleanup script to delete existing bad records
2. Source-level filtering to stop bad names at entry points
3. DB-level guard as a safety net for future data sources

## Design

### 1. One-time cleanup script

**File:** `scripts/cleanup_bad_names.py`

**Behavior:**
1. Query all researchers matching bad-name patterns:
   - Empty or whitespace-only `first_name`
   - Single-letter or initial-only `last_name` (regex: `^[A-Z]\.?$`)
2. Log each match (ID, first_name, last_name, publication count) before deletion
3. Also query for similar suspicious patterns (e.g. single-char first names, punctuation-only names) and log them for manual review without auto-deleting
4. Delete confirmed bad records — `ON DELETE CASCADE` handles `authorship`, `researcher_jel_codes`, `researcher_fields`, `researcher_snapshots`, `researcher_urls`

**Run:** `poetry run python scripts/cleanup_bad_names.py`

### 2. Source filtering (Layer 1)

#### A. OpenAlex coauthor ingestion — `openalex.py`

In `_parse_work()`, when building the coauthors list: **skip** any coauthor whose `display_name` is empty, whitespace, or has no identifiable first name (first token is empty or a single initial like "A.").

The paper still gets enriched; we just omit the bad coauthor record from `openalex_coauthors`.

#### B. LLM-extracted publications — `publication.py`

Add a post-extraction validation step after the Pydantic model parses the LLM response. If **any** author on a publication has:
- Empty or whitespace-only `first_name`
- Single-letter or initial-only `last_name` (matches `^[A-Z]\.?$`)

Then **discard the entire publication** and log a warning. A garbled author name signals the LLM misread the page, making the entire extraction suspect.

### 3. DB-level guard (Layer 2)

In `get_researcher_id()` (`database/researchers.py`), before inserting a new researcher, validate:
- `first_name` is not empty or whitespace
- `last_name` is not empty or whitespace
- `last_name` does not match `^[A-Z]\.?$` (single letter/initial)

If validation fails: log a warning and **return `None`**. Callers already handle `None` returns by skipping authorship linking for that author.

This catches anything that bypasses source-level filters — future data sources, edge cases, etc.

## Files modified

| File | Change |
|------|--------|
| `scripts/cleanup_bad_names.py` | New — one-time cleanup script |
| `openalex.py` | Filter bad coauthor names in `_parse_work()` |
| `publication.py` | Discard publications with bad author names |
| `database/researchers.py` | Validate names in `get_researcher_id()` before insert |

## Out of scope

Issue #73 items 3–5 (missing affiliations, zero-pub researchers, null years) are not addressed here.
