# Testing Suite Design Spec

## Problem

Multiple PRs have passed existing tests but then failed when running `make dev`. Past failures include:
- API rewrite target defaulting to Docker service name instead of localhost
- `SCRAPE_API_KEY` too short, crashing startup
- Advisory lock "Unread result found" errors from unconsumed cursor results
- CSP missing `unsafe-eval` breaking webpack HMR in dev mode

Root cause: existing tests mock too aggressively, bypassing the real import chain and startup path. Real configuration and wiring bugs slip through.

## Goal

Add low-effort, high-value tests that catch "make dev breaks" failures before they reach the dev loop, plus a single `make check` command to run everything.

## Design

### 1. `make check` pre-flight command

A new Makefile target running in sequence, failing fast:

```
make check
```

Steps (in order):
1. **Env validation** — `.venv/bin/python scripts/check_env.py` — a standalone script that reads `.env` via `dotenv_values()` (no side effects), checks all required vars (`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `OPENAI_API_KEY`, `SCRAPE_API_KEY`) exist, validates `SCRAPE_API_KEY` >= 16 chars, and validates `DB_NAME` format. Does NOT import any app code.
2. **Pytest** — `.venv/bin/pytest` (all Python tests including import smoke and contract tests)
3. **TypeScript check** — `cd app && npx tsc --noEmit` (catches TS errors before runtime)
4. **Jest** — `cd app && npm test` (all frontend tests)

If any step fails, the command exits immediately with a non-zero status.

The Makefile `.PHONY` line must be updated to include `check`.

### 2. New test files

#### `tests/test_imports.py` — Module import smoke tests

Dynamically discovers all `.py` files in the project root (not `.venv/`, not `tests/`) and verifies each imports without error. This catches:
- Syntax errors
- Missing dependencies
- Circular imports
- Module-level code that crashes

**Critical implementation details:**
- `publication.py` and `compare_models.py` create `OpenAI()` clients at module scope. The test must `patch('openai.OpenAI')` globally before importing these modules.
- Mock `mysql.connector.pooling.MySQLConnectionPool` to prevent real DB connection attempts.
- Env vars are set by `conftest.py` via `os.environ.setdefault()`. Since `load_dotenv()` also defaults to `override=False`, conftest values survive — no extra `load_dotenv` mocking needed for import tests.

#### `tests/test_db_config.py` — Environment variable validation

Tests for `db_config.py` behavior:
- Missing required env vars raise `EnvironmentError`
- Invalid `DB_NAME` (special chars, too long) raises `EnvironmentError`
- Valid config produces correct `db_config` dict
- Optional `DB_SSL_CA` adds SSL config when present
- Optional `DB_PORT` defaults to 3306

**Critical implementation detail:** `db_config.py` calls `load_dotenv()` at module scope, which reads `.env` from disk and overwrites patched env vars. The test must `patch('db_config.load_dotenv')` as a no-op before each `importlib.reload(db_config)` call.

#### `tests/test_api_response_shapes.py` — API response contract tests

Verifies that API endpoints return exactly the keys the frontend `types.ts` expects. This catches frontend/backend drift.

**Publication fields** (from `types.ts` `Publication` interface):
`id`, `title`, `authors`, `year`, `venue`, `source_url`, `discovered_at`, `status`, `abstract`, `draft_url`, `draft_url_status`, `draft_available`

Note: the existing `test_publication_item_shape` in `test_api_publications.py` checks most of these but **omits `draft_url_status` and `abstract`**. This new test fills that gap and adds researcher shape coverage.

**Researcher fields** (from `types.ts` `Researcher` interface):
`id`, `first_name`, `last_name`, `position`, `affiliation`, `description`, `urls`, `website_url`, `publication_count`, `fields`

**Researcher sub-object: `ResearcherUrl`** (from `types.ts`):
`id`, `page_type`, `url`

**Researcher sub-object: `ResearchField`** (from `types.ts`):
`id`, `name`, `slug`

**Researcher detail fields** (from `types.ts` `ResearcherDetail` interface):
All `Researcher` fields plus `publications` (array of `Publication` objects)

**Author fields** (from `types.ts` `Author` interface):
`id`, `first_name`, `last_name`

**Paginated response fields** (from `types.ts` `PaginatedResponse` interface):
`items`, `total`, `page`, `per_page`, `pages`

**Scrape status fields** (top-level, no `types.ts` interface — API-only contract):
`last_scrape`, `next_scrape_at`, `interval_hours`

**Scrape status `last_scrape` sub-object** (from `api.py` `scrape_status` endpoint):
`id`, `status`, `started_at`, `finished_at`, `urls_checked`, `urls_changed`, `pubs_extracted`

Implementation: uses `TestClient` with mocked DB (same pattern as existing integration tests). Assertions check key presence using `set(item.keys()) >= expected_keys`, not values. Sub-objects (`urls`, `fields`, `authors`, `last_scrape`) are also verified for their nested key sets.

### 3. What's NOT changing

The following are already well-covered and do NOT need new tests:
- `normalize_title` / `compute_title_hash` — 31 tests in `test_title_dedup.py`
- `_compute_researcher_content_hash` / `_compute_paper_content_hash` — 22 tests in `test_snapshots.py`
- Startup smoke tests — covered in `test_api_integration.py` `TestPublicationsSmoke`

### 4. Out of scope

- Docker-based integration tests
- Tests for `scheduler.py`, `html_fetcher.py`, `publication.py` internals (heavy external deps)
- GitHub Actions CI pipeline
- E2E browser tests
- Lint/format checks in `make check`

## Success Criteria

After implementation, running `make check` on a clean checkout with valid `.env` must:
1. Complete in under 60 seconds
2. Catch all historical failure modes (env vars, import errors, TS errors, config defaults)
3. Fail if any backend module has a syntax error or missing import
4. Fail if API response shapes drift from frontend `types.ts` expectations
