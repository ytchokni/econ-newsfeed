# Test Coverage Analysis

## Overview

The project has **~8,900 lines of Python tests** across 41 test files and **~1,380 lines of frontend tests** across 12 test files. This is solid coverage for the core domain logic, but there are meaningful gaps worth addressing.

---

## Current Coverage Summary

### Python Backend — Well Covered

| Source Module | Test File(s) | Notes |
|---|---|---|
| `publication.py` (validate_publication) | `test_validate_publication.py` (200 lines) | Good edge case coverage |
| `publication.py` (extraction) | `test_publication_extraction.py` (920 lines) | Thorough: prompt building, HTML extraction, JSON parsing |
| `publication.py` (save) | `test_save_publications.py` (320 lines) | Covers dedup, backfill, author fallback |
| `publication.py` (title dedup) | `test_title_dedup.py` (155 lines) | Hash-based dedup logic |
| `html_fetcher.py` | `test_html_fetcher.py` (434 lines) | Fetch, diff, robots.txt, draft URL validation |
| `link_extractor.py` | `test_link_extractor.py` (205 lines), `test_link_validation.py` (224 lines) | Domain matching, URL extraction |
| `doi_resolver.py` | `test_doi_resolver.py` (163 lines) | DOI resolution and edge cases |
| `jel_classifier.py` | `test_jel_classifier.py` (148 lines) | LLM-based classification |
| `jel_enrichment.py` | `test_jel_enrichment.py` (216 lines) | Paper topic enrichment pipeline |
| `openalex.py` | `test_openalex.py` (603 lines) | Search, DOI lookup, abstract reconstruction, budget |
| `paper_merge.py` | `test_paper_merge.py` (161 lines) | Duplicate detection and merging |
| `encoding_guard.py` | `test_encoding_guard.py` (118 lines) + 4 encoding test files | Mojibake detection across modules |
| `scheduler.py` | `test_scheduler.py` (510 lines) | Scrape orchestration, advisory locks |
| `api.py` | 8 `test_api_*.py` files (~2,400 lines total) | Endpoints, filters, search, middleware, security |
| `topic_jel_map.py` | `test_topic_jel_map.py` (58 lines) | Mapping logic |
| `db_config.py` | `test_db_config.py` (111 lines) | Configuration loading |
| `database/snapshots.py` | `test_snapshots.py` (181 lines), `test_snapshots_integration.py` (202 lines) | Content hashing, append-only versioning |

### Frontend — Well Covered

| Component | Test File | Notes |
|---|---|---|
| `NewsfeedContent.tsx` | `NewsfeedContent.test.tsx` (259 lines) | Feed rendering, filtering |
| `PaperDetailContent.tsx` | `PaperDetailContent.test.tsx` (96 lines) | Paper detail page |
| `ResearcherDetailContent.tsx` | `ResearcherDetailContent.test.tsx` (115 lines) | Researcher detail page |
| `ResearchersContent.tsx` | `ResearchersContent.test.tsx` (114 lines) | Researcher listing |
| `Header.tsx` | `Header.test.tsx` (20 lines) | Basic render test |
| `SearchInput.tsx` | `SearchInput.test.tsx` (54 lines) | Search interaction |
| `PublicationCard.tsx` | `PublicationCard.test.tsx` (119 lines) | Card rendering |
| `ResearcherCard.tsx` | `ResearcherCard.test.tsx` (45 lines) | Card rendering |
| `lib/api.ts` | `api.test.ts` (242 lines) | API client functions |
| Feed filters | `feed-filters.test.ts` (232 lines) | Filter logic |

---

## Coverage Gaps — Prioritized Recommendations

### Priority 1: High-Impact, Untested Source Code

#### 1. `database/schema.py` (665 lines) — No tests
This is the largest untested file. It contains all DDL statements, migrations, and seeding logic. A bug here can corrupt the database or silently drop columns during deployment.

**Recommended tests:**
- Verify `create_tables()` is idempotent (running twice doesn't error)
- Test that migrations apply cleanly on a fresh schema
- Test `seed_research_fields()` and `seed_jel_codes()` produce expected row counts
- Test `backfill_seed_publications()` logic

#### 2. `database/researchers.py` (348 lines) — Minimal coverage
Only `get_researcher_id` is tested (via `test_researcher_disambiguation.py`, 43 lines). The file also contains `_disambiguate_researcher` (LLM-based), `update_researcher_bio`, `add_researcher_url`, `import_data_from_file`, and `merge_researchers`.

**Recommended tests:**
- `import_data_from_file`: CSV parsing, duplicate URL handling, edge cases (missing columns, Unicode names)
- `merge_researchers`: verify authorship transfer, URL reassignment, snapshot preservation
- `add_researcher_url`: duplicate prevention, page_type validation
- `_disambiguate_researcher`: mock LLM responses, test fallback behavior

#### 3. `database/jel.py` (264 lines) — No direct tests
JEL code management (save, query, sync fields from JEL) is only tested indirectly via `test_jel_enrichment.py`. The `sync_researcher_fields_from_jel` function and `add_researcher_jel_codes` have no coverage.

**Recommended tests:**
- `sync_researcher_fields_from_jel`: verify field derivation from JEL codes
- `save_researcher_jel_codes`: idempotency, replacing old codes
- `get_jel_codes_for_researchers`: batch query correctness

#### 4. `main.py` — CLI commands `batch_submit` and `batch_check` (165 lines)
These OpenAI Batch API integration commands have no tests. A regression could silently submit malformed batches or fail to process results.

**Recommended tests:**
- `batch_submit`: JSONL generation, file upload mocking, duplicate batch warning
- `batch_check`: result parsing, status transitions, error handling for failed batches
- `batch_check`: validate that `PublicationExtraction` validation is applied to batch results

### Priority 2: Moderate Gaps in Existing Coverage

#### 5. `publication.py` — `reconcile_title_renames` (untested as a unit)
Referenced in `test_scheduler.py` and `test_publication_extraction.py` but only as part of integration flows. The rename-detection algorithm (Jaccard similarity, greedy matching, duplicate cleanup) is complex enough to warrant dedicated unit tests.

**Recommended tests:**
- Exact threshold behavior (similarity = 0.49 vs 0.51)
- Multiple renames in one batch
- Duplicate paper cleanup after rename
- Feed event creation for title changes

#### 6. `database/papers.py` (98 lines) — Only indirectly tested
`normalize_title`, `compute_title_hash` are tested via `test_title_dedup.py`, but `update_draft_url_status`, `get_unchecked_draft_urls`, `update_openalex_data`, and `get_unenriched_papers` have no direct tests.

**Recommended tests:**
- `update_openalex_data`: verify DOI/abstract/coauthor storage
- `get_unenriched_papers`: verify correct filtering and priority ordering

#### 7. `database/llm.py` (46 lines) — No tests
LLM usage logging and cost estimation. A bug here would silently miscalculate costs.

**Recommended tests:**
- Cost calculation for different models
- Token counting accuracy
- Batch vs. non-batch cost multipliers

#### 8. `openalex.py` — `_backfill_researcher_openalex_ids` (untested)
This function matches researchers to OpenAlex author IDs after enrichment. A matching bug could link the wrong researcher.

**Recommended tests:**
- Correct last-name matching
- Skip when openalex_author_id already set
- Handle researchers with no authorship link

### Priority 3: Frontend Gaps

#### 9. `lib/publication-utils.ts` — No tests
Contains `formatAuthor`, `formatDate`, and `statusPillConfig`. Pure utility functions ideal for unit testing.

**Recommended tests:**
- `formatAuthor`: initial extraction, edge cases (empty first name)
- `formatDate`: ISO string formatting, timezone handling

#### 10. `SearchableCheckboxDropdown.tsx` — No tests
The most complex untested frontend component. Contains dropdown open/close, search filtering, keyboard interaction, and click-outside-to-close.

**Recommended tests:**
- Open/close behavior
- Search filtering
- Checkbox selection/deselection
- Click-outside-to-dismiss

#### 11. `EmptyState.tsx`, `ErrorMessage.tsx` — No tests
Simple components, but testing them ensures they render correctly and display the right messages.

### Priority 4: Scripts and Operational Code

#### 12. `scripts/` directory (11 scripts, ~800 lines) — No tests at all
Scripts like `merge_duplicate_researchers.py`, `cleanup_garbage_papers.py`, `backfill_researcher_fields.py` modify production data. While they're run manually, a regression could cause data loss.

**Recommended approach:** Extract core logic into testable functions and add unit tests for the data-transforming logic (not the full DB interaction).

---

## Qualitative Gaps in Existing Tests

### Error/edge case coverage
- **`test_scheduler.py`**: Tests the happy path well but doesn't test concurrent scrape lock contention, SIGTERM mid-scrape, or OpenAlex enrichment failure during scrape.
- **`test_api_*.py`**: Good endpoint coverage but lacks tests for rate limiting behavior (slowapi), CORS configuration, and request validation error formatting.
- **`test_html_fetcher.py`**: Missing tests for `extract_description` (LLM-based), `compute_diff` edge cases (identical content, very large diffs).

### Integration test gaps
- No end-to-end test for the full scrape cycle (fetch -> extract -> save -> enrich -> merge). The pieces are tested individually but the pipeline is not.
- No test for the `api.py` lifespan handler (startup/shutdown sequence with scheduler).

---

## Summary Table

| Priority | Area | Lines Untested | Risk |
|---|---|---|---|
| P1 | `database/schema.py` | 665 | Schema corruption, migration failures |
| P1 | `database/researchers.py` | ~300 | Data import bugs, merge data loss |
| P1 | `database/jel.py` | ~264 | Wrong field derivation |
| P1 | `main.py` batch commands | ~165 | Malformed batches, lost results |
| P2 | `reconcile_title_renames` | ~130 | Incorrect rename detection |
| P2 | `database/papers.py` | ~50 | OpenAlex data storage bugs |
| P2 | `database/llm.py` | 46 | Cost miscalculation |
| P2 | `openalex.py` backfill | ~30 | Wrong researcher-author linking |
| P3 | Frontend utilities/components | ~150 | UI rendering bugs |
| P4 | `scripts/` | ~800 | Data corruption in manual ops |
