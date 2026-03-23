# Researcher Deduplication Design

**Date**: 2026-03-23
**Status**: Draft

## Problem

The OpenAlex coauthor import and publication extraction pipeline create duplicate researcher records when an author's name appears in different forms. Example: researcher #49 "L. Wren-Lewis" (manually seeded, has URLs, 3 papers) vs #4048 "Liam Wren-Lewis" (created by pipeline, no URLs, 38 papers). The current `get_researcher_id()` exact-match tier misses these because `"L." != "Liam"`.

## Solution Overview

Two changes:

1. **Prevention**: Add an initial-matching tier to `get_researcher_id()` that catches `"L." <-> "Liam"` variants before falling through to LLM disambiguation or creating a new record.
2. **Cleanup**: A one-time script to scan existing researchers for the same pattern and merge duplicates.

## Design

### Initial-Matching Function

A pure function `first_name_is_initial_match(name_a: str, name_b: str) -> bool` that returns `True` when one name is a single character (with or without trailing period) and matches the first character of the other name, case-insensitive.

Examples:
- `"L.", "Liam"` -> `True`
- `"L", "Liam"` -> `True`
- `"l.", "Liam"` -> `True`
- `"Liam", "Liam"` -> `False` (exact match, handled by Tier 1)
- `"Li", "Liam"` -> `False` (not a single-character initial)
- `"J.", "Liam"` -> `False` (different initial)

### New Tier 1.5 in `get_researcher_id()`

Inserted between the existing Tier 1 (exact match) and Tier 2 (OpenAlex ID match) in `database/researchers.py`:

1. Query `SELECT id, first_name, last_name FROM researchers WHERE last_name = %s` (same query used by Tier 3 today — can be hoisted and shared).
2. Filter candidates using `first_name_is_initial_match(new_first_name, candidate_first_name)`.
3. If **exactly one** candidate matches:
   - Update the candidate's `first_name` to the longer of the two names.
   - Log at INFO level: `"Initial matched '{new_name}' to researcher id={id} ('{existing_name}')"`
   - Return the candidate's `id`.
4. If **zero or multiple** candidates match, fall through to Tier 2 (OpenAlex ID) and Tier 3 (LLM) as today.

### Merge Operation

A reusable function `merge_researchers(canonical_id: int, duplicate_id: int, conn)` in `database/researchers.py`:

1. **Transfer authorship**: Move all authorship records from duplicate to canonical. Use `INSERT IGNORE ... ON DUPLICATE KEY UPDATE researcher_id = researcher_id` pattern to handle cases where both are already authors on the same paper — then delete remaining authorship rows for the duplicate.
2. **Upgrade first name**: Set canonical's `first_name` to the longer of the two.
3. **Backfill metadata**: Copy `affiliation`, `description`, `position`, `openalex_author_id` from duplicate to canonical where canonical's value is NULL.
4. **Delete duplicate**: `DELETE FROM researchers WHERE id = %s` — cascade handles leftover `authorship`, `researcher_urls`, `openalex_coauthors` rows.
5. **Log**: INFO-level message with both IDs, both names, and merge reason.

All steps execute in a single transaction.

**Canonical record selection** (used by the cleanup script): The researcher with rows in `researcher_urls` is canonical. If both have URLs (or neither), the lower `id` wins.

### One-Time Cleanup Script

`scripts/merge_duplicate_researchers.py`:

1. Query all researchers grouped by `last_name`.
2. Within each group, find pairs where `first_name_is_initial_match()` returns `True`.
3. For each pair, determine canonical vs duplicate (URL-holder wins, then lower ID).
4. Call `merge_researchers()` for each pair.
5. **Dry-run by default**: logs what it would merge. Pass `--execute` flag to run merges.

Usage:
```bash
poetry run python scripts/merge_duplicate_researchers.py           # dry-run
poetry run python scripts/merge_duplicate_researchers.py --execute  # actually merge
```

## Files Changed

| File | Change |
|------|--------|
| `database/researchers.py` | Add `first_name_is_initial_match()`, `merge_researchers()`, insert Tier 1.5 in `get_researcher_id()` |
| `scripts/merge_duplicate_researchers.py` | New — one-time cleanup script |
| `tests/test_researcher_dedup.py` | New — unit tests for initial matching, merge logic |

## Edge Cases

- **Multiple initial matches**: e.g., both "L. Smith" and "Liam Smith" exist, new "L Smith" arrives. Tier 1.5 finds two matches -> falls through to LLM disambiguation. Safe.
- **Both records have URLs**: canonical = lower ID. URLs on duplicate are cascade-deleted along with the duplicate (they point to the same person's pages).
- **Shared authorship**: both researchers are co-authors on the same paper. The `ON DUPLICATE KEY` no-op handles this — no duplicate authorship rows created.
- **openalex_author_id conflict**: if both researchers have different OpenAlex IDs, the canonical keeps its own (backfill only when NULL). This is conservative — avoids overwriting known-good data.

## Not In Scope

- Prefix matching ("Li" -> "Liam") — too aggressive, risk of false positives.
- Nickname matching ("Bob" -> "Robert") — complex, better handled by existing LLM tier.
- Persistent merge history table — INFO logs are sufficient.
- API endpoint for manual merging — can be added later if needed.
