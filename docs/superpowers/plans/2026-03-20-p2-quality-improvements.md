# P2 Quality Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address all actionable P2 code quality issues from the review report — type hints, deprecated SQL, logging cleanup, database indexes, thread safety, Docker hardening, dependency updates, and frontend fixes.

**Architecture:** 12 tasks organized by effort: quick config/infra fixes first, then mechanical code changes, then larger refactors. Each task is independent and produces a clean commit. No task depends on another.

**Tech Stack:** Python 3.12 / FastAPI / MySQL 8 / Next.js 14 / TypeScript / Docker Compose

---

## File Structure Overview

| Task | Files Modified |
|------|---------------|
| 1: MySQL VALUES() syntax | `html_fetcher.py` |
| 2: logging.basicConfig() cleanup | `scheduler.py`, `html_fetcher.py`, `main.py` |
| 3: Composite index on feed_events | `database/schema.py` |
| 4: Pin MySQL image + resource limits | `docker-compose.yml` |
| 5: Thread-safe requests.Session | `html_fetcher.py` |
| 6: getFields() proxy bypass | `app/src/lib/api.ts` |
| 7: App-level caching for filter options | `api.py` |
| 8: Python dependency updates | `pyproject.toml`, `poetry.lock` |
| 9: ESLint 8 → 9 | `app/package.json`, `app/package-lock.json`, `app/eslint.config.mjs` |
| 10: Type hints — database package | `database/*.py` |
| 11: Type hints — core modules | `api.py`, `scheduler.py`, `html_fetcher.py`, `publication.py`, `researcher.py`, `main.py` |
| 12: Sync → async FastAPI endpoints | `api.py` |

---

## Task 1: Fix Deprecated MySQL VALUES() Syntax (#51)

**Files:**
- Modify: `html_fetcher.py:215-222`

MySQL 8.0.20+ deprecated `VALUES(col)` in `INSERT...ON DUPLICATE KEY UPDATE`. Use the row alias syntax instead.

- [ ] **Step 1: Read and update the query**

In `html_fetcher.py`, find the `save_text` method. Change:

```python
query = """
    INSERT INTO html_content (url_id, content, content_hash, timestamp, researcher_id)
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        content = VALUES(content),
        content_hash = VALUES(content_hash),
        timestamp = VALUES(timestamp)
"""
```

To:

```python
query = """
    INSERT INTO html_content (url_id, content, content_hash, timestamp, researcher_id)
    VALUES (%s, %s, %s, %s, %s) AS new_row
    ON DUPLICATE KEY UPDATE
        content = new_row.content,
        content_hash = new_row.content_hash,
        timestamp = new_row.timestamp
"""
```

- [ ] **Step 2: Run tests**

Run: `pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"`

- [ ] **Step 3: Commit**

```bash
git add html_fetcher.py
git commit -m "fix: replace deprecated MySQL VALUES() syntax with row alias (#51)"
```

---

## Task 2: Fix Multiple logging.basicConfig() Calls (#58)

**Files:**
- Modify: `scheduler.py:17`, `html_fetcher.py:17`, `main.py:14`

Only one `basicConfig()` call should exist. The others are no-ops that cause confusion. Remove the calls from `scheduler.py` and `html_fetcher.py`, keeping only `main.py`'s (which runs first for CLI) and adding one to `api.py`'s startup for the API path.

- [ ] **Step 1: Remove duplicate basicConfig calls**

In `scheduler.py`, remove line 17:
```python
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
```

In `html_fetcher.py`, remove line 17:
```python
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
```

Keep the one in `main.py` (CLI entry point).

- [ ] **Step 2: Run tests**

Run: `pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"`

- [ ] **Step 3: Commit**

```bash
git add scheduler.py html_fetcher.py
git commit -m "fix: remove duplicate logging.basicConfig() calls (#58)"
```

---

## Task 3: Add Composite Index on feed_events (#46)

**Files:**
- Modify: `database/schema.py` (feed_events table definition + migration)

The main publications query does `ORDER BY fe.created_at DESC` with a JOIN on `fe.paper_id = p.id`. A composite index on `(created_at DESC, paper_id)` optimizes this.

- [ ] **Step 1: Add composite index to CREATE TABLE**

In `database/schema.py`, in the `feed_events` table definition, add after the existing indexes:

```sql
INDEX idx_created_at_paper (created_at DESC, paper_id)
```

- [ ] **Step 2: Add migration for existing databases**

In the migrations section of `create_tables()`, add:

```python
try:
    cursor.execute(
        "ALTER TABLE feed_events ADD INDEX idx_created_at_paper (created_at DESC, paper_id)"
    )
    conn.commit()
except Exception as e:
    if getattr(e, 'errno', None) != 1061:  # Duplicate key name
        logging.warning("Migration: feed_events.idx_created_at_paper: %s", e)
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"
git add database/schema.py
git commit -m "perf: add composite index on feed_events(created_at, paper_id) (#46)"
```

---

## Task 4: Pin MySQL Image + Add Container Resource Limits (#55, #57)

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Pin MySQL version and add resource limits**

In `docker-compose.yml`:

1. Change `image: mysql:8` to `image: mysql:8.0.40`
2. Add resource limits and restart policies to all services:

```yaml
services:
  db:
    image: mysql:8.0.40
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
    # ... rest unchanged

  api:
    # ... existing config ...
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"

  frontend:
    # ... existing config ...
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: "0.5"
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "infra: pin MySQL 8.0.40, add resource limits and restart policies (#55, #57)"
```

---

## Task 5: Fix Thread-Unsafe requests.Session (#49)

**Files:**
- Modify: `html_fetcher.py:50-54`
- Test: `tests/test_html_fetcher.py`

`requests.Session` is not thread-safe. The class-level `session` is shared across threads when scraping runs concurrently. Fix: create a session per-thread using `threading.local`.

- [ ] **Step 1: Write test for thread-local session**

In `tests/test_html_fetcher.py`, add:

```python
import threading

class TestThreadSafety:
    def test_sessions_are_thread_local(self):
        """Each thread should get its own Session instance."""
        sessions = {}

        def capture_session():
            sessions[threading.current_thread().name] = HTMLFetcher._get_session()

        t1 = threading.Thread(target=capture_session, name="t1")
        t2 = threading.Thread(target=capture_session, name="t2")
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sessions["t1"] is not sessions["t2"]
```

- [ ] **Step 2: Run test — expected FAIL**

Run: `pytest tests/test_html_fetcher.py::TestThreadSafety -v`

- [ ] **Step 3: Implement thread-local sessions**

In `html_fetcher.py`, replace the class-level `session` with a thread-local getter:

```python
class HTMLFetcher:
    _thread_local = threading.local()

    @staticmethod
    def _get_session() -> requests.Session:
        """Get or create a thread-local requests.Session."""
        if not hasattr(HTMLFetcher._thread_local, 'session'):
            s = requests.Session()
            s.headers.update({'User-Agent': SCRAPER_USER_AGENT})
            HTMLFetcher._thread_local.session = s
        return HTMLFetcher._thread_local.session
```

Then update all references from `HTMLFetcher.session` to `HTMLFetcher._get_session()`:
- `fetch_html`: `session = HTMLFetcher._get_session()` (already assigns to local var)
- `validate_draft_url`: `response = HTMLFetcher._get_session().head(url, ...)`

Remove the old class-level `session = requests.Session()` and `session.headers.update(...)`.

- [ ] **Step 4: Update tests that patch `HTMLFetcher.session`**

Tests that use `patch.object(HTMLFetcher.session, 'get', ...)` need to change to `patch.object(HTMLFetcher, '_get_session')` returning a mock session, or patch the thread-local directly.

The simplest approach: in test fixtures, set `HTMLFetcher._thread_local.session = mock_session`.

- [ ] **Step 5: Run all tests and commit**

```bash
pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "fix: make requests.Session thread-local for concurrent safety (#49)"
```

---

## Task 6: Fix getFields() Proxy Bypass (#59)

**Files:**
- Modify: `app/src/lib/api.ts:75-80`

- [ ] **Step 1: Change absolute URL to relative path**

In `app/src/lib/api.ts`, change `getFields()`:

```typescript
export async function getFields(): Promise<ResearchField[]> {
  const data = await fetchJson<{ items: ResearchField[] }>(
    `/api/fields`  // was: `${API_BASE_URL}/api/fields`
  );
  return data.items;
}
```

- [ ] **Step 2: Verify frontend builds**

```bash
cd app && npx tsc --noEmit && npm run lint
```

- [ ] **Step 3: Commit**

```bash
git add app/src/lib/api.ts
git commit -m "fix: route getFields() through Next.js proxy instead of direct API call (#59)"
```

---

## Task 7: Add Server-Side Caching for /api/filter-options (#48)

**Files:**
- Modify: `api.py` (filter-options endpoint)

The endpoint runs 3 DB queries on every request for data that changes only when new researchers are imported. Add a simple in-memory cache with TTL.

- [ ] **Step 1: Add a cache decorator or simple caching logic**

Above the endpoint, add a simple TTL cache:

```python
import time as _time

_filter_options_cache = {"data": None, "expires_at": 0}
_FILTER_OPTIONS_TTL = 600  # 10 minutes
```

Then in `get_filter_options`:

```python
@app.get("/api/filter-options")
@limiter.limit("30/minute")
def get_filter_options(request: Request, response: Response):
    now = _time.time()
    if _filter_options_cache["data"] and now < _filter_options_cache["expires_at"]:
        response.headers["Cache-Control"] = "public, max-age=600"
        return _filter_options_cache["data"]

    institutions = Database.fetch_all(
        "SELECT DISTINCT affiliation FROM researchers "
        "WHERE affiliation IS NOT NULL AND affiliation != '' "
        "ORDER BY affiliation"
    )
    positions = Database.fetch_all(
        "SELECT DISTINCT position FROM researchers "
        "WHERE position IS NOT NULL AND position != '' "
        "ORDER BY position"
    )
    fields = Database.fetch_all(
        "SELECT id, name, slug FROM research_fields ORDER BY name"
    )
    result = {
        "institutions": [r['affiliation'] for r in institutions],
        "positions": [r['position'] for r in positions],
        "fields": [{"id": r['id'], "name": r['name'], "slug": r['slug']} for r in fields],
    }
    _filter_options_cache["data"] = result
    _filter_options_cache["expires_at"] = now + _FILTER_OPTIONS_TTL

    response.headers["Cache-Control"] = "public, max-age=600"
    return result
```

- [ ] **Step 2: Run tests and commit**

```bash
pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"
git add api.py
git commit -m "perf: add server-side caching for /api/filter-options (#48)"
```

---

## Task 8: Update Python Dependencies (#53)

**Files:**
- Modify: `pyproject.toml`, `poetry.lock`

- [ ] **Step 1: Update all dependencies**

```bash
poetry update
```

- [ ] **Step 2: Run tests to verify nothing breaks**

```bash
pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "chore: update Python dependencies to latest compatible versions (#53)"
```

---

## Task 9: Upgrade ESLint 8 → 9 (#52)

**Files:**
- Modify: `app/package.json`, `app/package-lock.json`
- May create: `app/eslint.config.mjs` (ESLint 9 flat config)

- [ ] **Step 1: Update ESLint and related packages**

```bash
cd app
npm install eslint@^9 eslint-config-next@latest --save-dev
```

ESLint 9 uses "flat config" by default. If `eslint-config-next` supports ESLint 9, the migration may be seamless. If not, check compatibility and adjust.

- [ ] **Step 2: Verify lint passes**

```bash
npm run lint
```

If the flat config format is required, create `eslint.config.mjs` based on the existing `.eslintrc.json` and **delete `.eslintrc.json`** (ESLint 9 does not support both config formats simultaneously).

- [ ] **Step 3: Run all frontend checks**

```bash
npx tsc --noEmit && npm test -- --passWithNoTests && npm run lint
```

- [ ] **Step 4: Commit**

```bash
git add app/package.json app/package-lock.json app/eslint.config.mjs app/.eslintrc*
git commit -m "chore: upgrade ESLint 8 to 9 (#52)"
```

---

## Task 10: Add Type Hints — Database Package (#44, part 1)

**Files:**
- Modify: `database/connection.py`, `database/papers.py`, `database/llm.py`, `database/snapshots.py`, `database/researchers.py`, `database/schema.py`

Add return type annotations and parameter type hints to all public functions in the database package.

- [ ] **Step 1: Add type hints to `database/connection.py`**

```python
from mysql.connector.pooling import PooledMySQLConnection

def get_connection() -> PooledMySQLConnection: ...
def execute_query(query: str, params: tuple | None = None) -> int: ...
def fetch_all(query: str, params: tuple | None = None) -> list[dict]: ...
def fetch_one(query: str, params: tuple | None = None) -> dict | None: ...
```

- [ ] **Step 2: Add type hints to `database/papers.py`**

```python
def normalize_title(title: str | None) -> str: ...
def compute_title_hash(title: str | None) -> str: ...
def update_draft_url_status(paper_id: int, status: str) -> None: ...
def get_unchecked_draft_urls(limit: int = 100) -> list[dict]: ...
```

- [ ] **Step 3: Add type hints to `database/llm.py`**

```python
def log_llm_usage(call_type: str, model: str, usage: object, ...) -> None: ...
```

- [ ] **Step 4: Add type hints to `database/snapshots.py`**

```python
def append_researcher_snapshot(...) -> bool: ...
def get_researcher_snapshots(researcher_id: int, limit: int = 20) -> list[dict]: ...
def append_paper_snapshot(...) -> bool: ...
def get_paper_snapshots(paper_id: int, limit: int = 20) -> list[dict]: ...
```

- [ ] **Step 5: Add type hints to `database/researchers.py`**

```python
def get_researcher_id(first_name: str, last_name: str, ...) -> int: ...
def update_researcher_bio(researcher_id: int, bio: str) -> None: ...
def add_researcher_url(researcher_id: int, page_type: str, url: str) -> None: ...
def import_data_from_file(file_path: str) -> None: ...
```

- [ ] **Step 6: Add type hints to `database/schema.py`**

```python
def create_database() -> None: ...
def create_tables() -> None: ...
def seed_research_fields() -> None: ...
def backfill_seed_publications() -> int: ...
```

- [ ] **Step 7: Run tests and commit**

```bash
pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"
git add database/
git commit -m "refactor: add type hints to database package (#44)"
```

---

## Task 11: Add Type Hints — Core Modules (#44, part 2)

**Files:**
- Modify: `scheduler.py`, `html_fetcher.py`, `publication.py`, `researcher.py`, `main.py`, `api.py`

Add return type annotations to all functions in core modules. For `api.py`, add return types to helper functions (not endpoints — FastAPI infers those from response_model).

- [ ] **Step 1: Add type hints to `scheduler.py`**

All functions: `_acquire_db_lock`, `_release_db_lock`, `is_scrape_running`, `create_scrape_log`, `update_scrape_log`, `_validate_draft_urls`, `run_scrape_job`, `_handle_sigterm`, `start_scheduler`, `shutdown_scheduler`.

- [ ] **Step 2: Add type hints to `html_fetcher.py`**

All static methods on `HTMLFetcher`: `_get_robots_parser`, `is_allowed_by_robots`, `validate_url`, `validate_url_with_pin`, `_rate_limit`, `fetch_html`, `extract_text_content`, `hash_text_content`, `save_text`, `has_text_changed`, `_was_fetched_recently`, `fetch_and_save_if_changed`, `extract_description`, `validate_draft_url`, `get_latest_text`, `needs_extraction`, `mark_extracted`, `get_previous_text`, `compute_diff`.

- [ ] **Step 3: Add type hints to `publication.py`**

All static methods on `Publication`: `save_publications`, `extract_relevant_html`, `build_extraction_prompt`, `extract_publications`, `parse_openai_response`, `dump_invalid_json`, `is_valid_json`, `get_all_publications`.

- [ ] **Step 4: Add type hints to `researcher.py`**

All static methods on `Researcher`: `get_all_researchers`, `get_all_researcher_urls`, `add_researcher`, `add_url_to_researcher`.

- [ ] **Step 5: Add type hints to `main.py`**

All functions: `import_data`, `download_htmls`, `extract_data_from_htmls`, `_process_one_url`, `extract_data_from_htmls_concurrent`, `batch_submit`, `batch_check`, `main`.

- [ ] **Step 6: Add type hints to `api.py` helper functions**

Only helpers (not endpoints): `_escape_like`, `_iso_z`, `_get_authors_for_publication`, `_get_authors_for_publications`, `_format_publication`, `_format_feed_event`, `_get_urls_for_researcher`, `_get_website_url`, `_get_pub_count_for_researcher`, `_get_fields_for_researcher`, `_get_urls_for_researchers`, `_get_pub_counts_for_researchers`, `_get_fields_for_researchers`.

- [ ] **Step 7: Run tests and commit**

```bash
pytest -q --ignore=tests/test_imports.py -k "not test_private_ip"
git add scheduler.py html_fetcher.py publication.py researcher.py main.py api.py
git commit -m "refactor: add type hints to all core modules (#44)"
```

---

## Task 12: Convert Sync Endpoints to Async (#47)

**Files:**
- Modify: `api.py`

FastAPI runs sync `def` endpoints in a thread pool. For I/O-bound handlers that only call `Database.fetch_*` (which are themselves synchronous), this is functionally correct but wastes a thread. Since the database calls are blocking (synchronous mysql-connector-python), converting to `async def` won't yield true async I/O — it would actually be worse because it would block the event loop.

**Decision:** Do NOT convert endpoints to `async def` unless the database layer is migrated to an async driver (e.g., `aiomysql`). FastAPI's thread pool handling of `def` endpoints is the correct approach for synchronous database drivers.

Instead, document this as a known trade-off and mark as deferred.

*This task is intentionally empty — no code changes needed.*

---

## Deferred Items

These P2 items require infrastructure/process changes beyond code quality:

| # | Item | Reason for Deferral |
|---|------|-------------------|
| #36 | LLM prompt injection | Complex security analysis of content sanitization |
| #37 | Missing HSTS on frontend | Frontend deployment/reverse proxy config |
| #38 | CSP unsafe-inline | Requires audit of inline styles/scripts |
| #39 | DB connection encryption | Infrastructure TLS cert management |
| #40 | Excessive mocking in tests | Testing philosophy, large test refactor |
| #41 | Frontend interaction tests | New test infrastructure (Testing Library) |
| #42 | Duplicated test fixtures | Partially done in P1 (conftest.py); remaining needs gradual migration |
| #43 | No migration framework (Alembic) | Large architectural change |
| #45 | LIKE queries can't use indexes | Requires fulltext index evaluation |
| #47 | Sync handlers in async FastAPI | Requires async DB driver (aiomysql) — see Task 12 |
| #50 | Researcher pagination | Already supports per_page up to 100; frontend hard-codes 100 |
| #54 | No structured logging | Cross-cutting logging overhaul |
| #56 | No environment parity | DevOps process change |
| #60 | No ADRs | Documentation process |

---

## Execution Summary

| Task | Issue(s) | Est. Effort | Risk |
|------|----------|-------------|------|
| 1: VALUES() syntax | #51 | 2 min | Low |
| 2: logging.basicConfig | #58 | 2 min | Low |
| 3: Composite index | #46 | 5 min | Low |
| 4: Docker hardening | #55, #57 | 5 min | Low |
| 5: Thread-safe Session | #49 | 15 min | Medium |
| 6: getFields() proxy | #59 | 2 min | Low |
| 7: Filter options cache | #48 | 10 min | Low |
| 8: Python deps | #53 | 5 min | Medium |
| 9: ESLint upgrade | #52 | 15 min | Medium |
| 10: Type hints (database) | #44 | 15 min | Low |
| 11: Type hints (core) | #44 | 20 min | Low |
| 12: Async endpoints | #47 | 0 min | N/A (deferred) |

**Total: ~11 actionable tasks covering 14 P2 items, ~95 minutes estimated.**
