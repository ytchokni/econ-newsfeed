# Phase 1: Code Quality & Architecture Review

**Date:** 2026-03-20

---

## Code Quality Findings

### Critical

| ID | Finding | File | Line(s) |
|----|---------|------|---------|
| CQ-C1 | `CONTENT_MAX_CHARS` crashes on startup if env var missing — `int(None)` TypeError | `html_fetcher.py` | 21 |
| CQ-C2 | Tuple unpacking bug in `_validate_draft_urls` — `get_unchecked_draft_urls()` returns dicts but code expects tuples | `scheduler.py` | 120-127 |
| CQ-C3 | `OPENAI_MODEL` has no default — `None` passed to OpenAI API causes validation error | `publication.py` | 14 |
| CQ-C4 | Inconsistent `CONTENT_MAX_CHARS` between modules — `html_fetcher.py` has no default, `publication.py` defaults to `'4000'` | `html_fetcher.py:21`, `publication.py:17` | — |

### High

| ID | Finding | File | Line(s) |
|----|---------|------|---------|
| CQ-H1 | `list_publications` excessive complexity (~18 cyclomatic) — 123 lines, deeply nested SQL building | `api.py` | 389-512 |
| CQ-H2 | `list_researchers` duplicates query-building pattern from `list_publications` | `api.py` | 699-789 |
| CQ-H3 | `Database` facade class is a maintenance liability — every new function requires two-file changes | `database/__init__.py` | 1-84 |
| CQ-H4 | Connection pool leak risk in scheduler lock management — raw connections outside pool | `scheduler.py` | 27-53 |
| CQ-H5 | `database/researchers.py` creates nested closures duplicating connection logic on every call | `database/researchers.py` | 55-84 |
| CQ-H6 | `save_publications` performs N+1 queries for author resolution inside a loop | `publication.py` | 142-153 |
| CQ-H7 | Hardcoded year range in frontend `YEAR_OPTIONS` — will need code change in 2027 | `app/src/app/NewsfeedContent.tsx` | 61-67 |
| CQ-H8 | `HTMLFetcher` uses class-level mutable state without thread-safe access to `_robots_cache` | `html_fetcher.py` | 51-65, 72-88 |

### Medium

| ID | Finding | File | Line(s) |
|----|---------|------|---------|
| CQ-M1 | Duplicated `CheckboxDropdown` component alongside `SearchableCheckboxDropdown` | `NewsfeedContent.tsx:71-163`, `SearchableCheckboxDropdown.tsx` | — |
| CQ-M2 | Frontend API client inconsistent URL base — `getFields()` uses absolute URL, others use relative | `app/src/lib/api.ts` | 77 |
| CQ-M3 | `schema.py` `create_tables` is 125 lines of linear procedural migration code | `database/schema.py` | 267-393 |
| CQ-M4 | No input length validation on query string parameters — arbitrarily long IN clauses possible | `api.py` | 396-401, 706-710 |
| CQ-M5 | `_iso_z` doesn't handle timezone-aware datetimes correctly | `api.py` | 283-285 |
| CQ-M6 | `Publication` class mixes data class and service class responsibilities (SRP violation) | `publication.py` | 63-285 |
| CQ-M7 | `Researcher` class has the same SRP violation as `Publication` | `researcher.py` | — |
| CQ-M8 | `_disambiguate_researcher` creates inline OpenAI client on every call (inconsistent with singleton pattern) | `database/researchers.py` | 29-30 |
| CQ-M9 | `run_scrape_job` is 120 lines with ~22 cognitive complexity | `scheduler.py` | 135-261 |
| CQ-M10 | Health endpoint returns `ok` without checking database connectivity | `api.py` | 362-365 |
| CQ-M11 | Test `client` fixture duplicated across multiple test files, shadowing `conftest.py` | `tests/test_api_publications.py`, `tests/test_security.py` | — |
| CQ-M12 | `batch_check` function is 100 lines with multiple responsibilities (God Function) | `main.py` | 198-308 |

### Low

| ID | Finding | File | Line(s) |
|----|---------|------|---------|
| CQ-L1 | Redundant `logging.basicConfig` calls in multiple modules | `html_fetcher.py:17`, `main.py:14`, `scheduler.py:17` | — |
| CQ-L2 | `_TOP20_DEPT_KEYWORDS` hardcoded in API layer — business logic in web layer | `api.py` | 644-661 |
| CQ-L3 | f-strings used in logging calls instead of lazy `%s` formatting | Multiple files | — |
| CQ-L4 | `Makefile` `seed` target references `database.py` which no longer exists as standalone file | `Makefile` | 13 |
| CQ-L5 | `_UsageDict` helper duplicates what `types.SimpleNamespace` provides | `main.py` | 190-195 |
| CQ-L6 | `parse_openai_response` / `is_valid_json` partially dead code (only used in batch fallback) | `publication.py` | 239-275 |
| CQ-L7 | `get_previous_text` is a trivial alias for `get_latest_text` | `html_fetcher.py` | 424-431 |
| CQ-L8 | `extract_bio` is dead legacy wrapper | `html_fetcher.py` | 302-305 |
| CQ-L9 | Docker Compose does not pin MySQL image version | `docker-compose.yml` | 3 |

---

## Architecture Findings

### Critical

| ID | Finding | Impact |
|----|---------|--------|
| AR-C1 | `HTMLFetcher` violates SRP — contains LLM calls and database I/O in a data acquisition module, creating circular conceptual dependencies | Any change to OpenAI client config or LLM cost logging can break the HTML fetcher; impossible to test fetching in isolation from LLM subsystem |
| AR-C2 | `Database` facade conflates data access, schema management, and business logic (LLM disambiguation in `get_researcher_id`) — data access layer has runtime dependency on paid external API | Consumers cannot distinguish cheap operations from expensive ones; cost implications hidden from callers |

### High

| ID | Finding | Impact |
|----|---------|--------|
| AR-H1 | Class-level mutable state on `HTMLFetcher` — globals for session, caches, locks; scheduler reaches into internal state to clear caches | Non-deterministic tests, cannot run multiple fetcher configs; implicit temporal coupling |
| AR-H2 | `api.py` contains raw SQL construction belonging in repository layer — SQL logic spread across 5+ files | No reusable query layer; adding endpoints requires SQL in API file |
| AR-H3 | Synchronous blocking I/O in async FastAPI application — `mysql-connector-python` doesn't support async, `requests.Session` is sync, `time.sleep()` blocks threads | Concurrency ceiling; thread pool exhaustion risk under load |
| AR-H4 | Frontend API client inconsistent URL construction — `getFields()` bypasses Next.js proxy | Silent SSR/container failures |
| AR-H5 | No API versioning strategy — all endpoints under `/api/` with no version prefix | Breaking changes require coordinated frontend+backend deployment |
| AR-H6 | Module-level OpenAI client instantiation causes import-time side effects | Import `publication.py` requires `OPENAI_API_KEY` set; fragile boot ordering |

### Medium

| ID | Finding | Impact |
|----|---------|--------|
| AR-M1 | `Publication` class conflates data model, extraction logic, and persistence | SRP violation; legacy `__init__` only used in dead code |
| AR-M2 | `Researcher` class duplicates data access in `database/researchers.py` | Dual data access paths create confusion about authority |
| AR-M3 | `save_publications` swallows exceptions per-publication; scrape log overcounts extractions | Inaccurate `pubs_extracted` metric |
| AR-M4 | `get_unchecked_draft_urls` returns dicts but `_validate_draft_urls` destructures as tuples | Latent runtime bug in draft URL validation |
| AR-M5 | Hardcoded institution keywords in both API layer and frontend — no single source of truth | Requires modifying both codebases to change institution list |
| AR-M6 | Duplicate CheckboxDropdown component | Bug fixes/styling must be applied in two places |
| AR-M7 | Test fixtures duplicated across test files, shadowing `conftest.py` | Maintenance burden; changes to shared fixtures don't propagate |
| AR-M8 | Frontend `useResearchersFiltered` hardcodes `per_page=100` with no pagination | Silent data truncation as researcher count grows |

### Low

| ID | Finding | Impact |
|----|---------|--------|
| AR-L1 | Mixed naming conventions — backend snake_case in TypeScript properties | Non-idiomatic TS but functionally correct |
| AR-L2 | Makefile `seed` target references removed `database.py` file | Broken make target |
| AR-L3 | `Publication.get_all_publications()` constructs instances but no caller uses it | Dead code |
| AR-L4 | Year options hardcoded to 2020-2026 | Requires annual maintenance |
| AR-L5 | Docker API container copies entire project including frontend/tests | Unnecessary image bloat |

---

## Critical Issues for Phase 2 Context

The following findings from Phase 1 should inform the Security and Performance reviews:

### Security-relevant
- **CQ-C1/CQ-C3**: Missing env var defaults could crash app or leak error details in production
- **AR-C2**: LLM call hidden in database layer — unexpected external network calls from data access
- **AR-H6**: Import-time side effects require specific env var ordering
- **CQ-M4**: No input length validation on query parameters — potential for abuse via oversized IN clauses
- **CQ-M10**: Health endpoint doesn't verify database connectivity — could mask failures

### Performance-relevant
- **CQ-H6**: N+1 queries for author resolution (90-270 queries per page)
- **AR-H3**: Synchronous blocking I/O limits concurrency
- **CQ-H1/CQ-H2**: Complex query building with no query caching or optimization
- **CQ-H4**: Connection pool leak risk in scheduler
- **CQ-H8**: Race condition in `_robots_cache` under concurrent extraction
- **AR-M8**: Frontend hardcodes `per_page=100` with no pagination for researchers

### Architectural strengths to preserve
1. Content-hash-based change detection (avoids redundant LLM calls)
2. Append-only snapshot versioning with hash deduplication
3. Advisory locks for distributed scheduling
4. SSRF protection with DNS pinning
5. Parameterized SQL everywhere
6. Feed events architecture (event-sourced newsfeed)
7. LLM cost tracking via `llm_usage` table
8. Constant-time API key comparison via `hmac.compare_digest`
