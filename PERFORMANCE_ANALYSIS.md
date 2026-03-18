# Performance & Scalability Analysis — econ-newsfeed

**Date:** 2026-03-17
**Scope:** Full-stack review — Python/FastAPI backend, Next.js/TypeScript frontend, MySQL database, Docker infrastructure

---

## Executive Summary

The econ-newsfeed application has several **critical performance bottlenecks** that will degrade sharply as data grows. The two most impactful issues are (1) the absence of database connection pooling, which forces a fresh TCP + authentication handshake on every single query, and (2) pervasive N+1 query patterns across all list endpoints, which multiply that connection cost linearly per data item. Together these create a combinatorial scaling problem: at 100 researchers with 10 publications each, the researchers list endpoint alone opens **~300 separate database connections** per request.

Secondary concerns include an unpaginated researchers endpoint, unused SWR client-side caching, synchronous blocking I/O during scraping, a race condition in the manual scrape trigger, and unbounded disk writes from invalid JSON dumps.

---

## Finding Index

| # | Finding | Severity | Section |
|---|---------|----------|---------|
| 1 | No connection pooling — new TCP connection per query | Critical | [DB-1](#db-1-no-connection-pooling) |
| 2 | N+1 queries on all list endpoints | Critical | [DB-2](#db-2-n1-query-patterns) |
| 3 | Missing UNIQUE constraint on authorship table | High | [DB-3](#db-3-missing-unique-constraint-on-authorship) |
| 4 | Full-table-scan deduplication query in publication save | High | [DB-4](#db-4-full-table-scan-deduplication) |
| 5 | No pagination on researchers endpoint | High | [IO-1](#io-1-unpaginated-researchers-endpoint) |
| 6 | Synchronous blocking HTTP in scraper (async event loop) | High | [IO-2](#io-2-synchronous-blocking-io-in-scraper) |
| 7 | Race condition in manual scrape trigger | High | [CC-1](#cc-1-race-condition-in-scrape-trigger) |
| 8 | SWR installed but unused — no client-side caching | High | [FE-1](#fe-1-swr-installed-but-unused) |
| 9 | `NEXT_PUBLIC_API_URL` SSR/Docker mismatch | Medium | [FE-2](#fe-2-next_public_api_url-ssrdocker-mismatch) |
| 10 | Unbounded invalid JSON file dumps | Medium | [MEM-1](#mem-1-unbounded-invalid-json-file-dumps) |
| 11 | Accumulating publications in NewsfeedContent state | Medium | [MEM-2](#mem-2-unbounded-publication-accumulation-in-state) |
| 12 | Single-worker uvicorn in production | Medium | [SC-1](#sc-1-single-worker-uvicorn) |
| 13 | No Cache-Control headers on API responses | Medium | [CA-1](#ca-1-no-cache-control-headers) |
| 14 | Class-level mutable state on HTMLFetcher | Medium | [CC-2](#cc-2-class-level-mutable-state-on-htmlfetcher) |
| 15 | No database connection retry/backoff at startup | Low | [SC-2](#sc-2-no-database-connection-retry-at-startup) |
| 16 | `create_tables` opens 6 separate connections | Low | [DB-5](#db-5-create_tables-opens-6-connections) |
| 17 | robots.txt fetched on every scrape without caching | Low | [IO-3](#io-3-robotstxt-fetched-every-scrape-without-caching) |
| 18 | No Next.js output optimization configured | Low | [FE-3](#fe-3-no-nextjs-output-optimization) |

---

## 1. Database Performance

### DB-1: No Connection Pooling

**Severity: Critical**
**Files:** `database.py:34-43`

Every call to `Database.get_connection()` creates a brand-new TCP connection to MySQL, performs TLS negotiation (if configured), authenticates, and selects the database. This happens on every `fetch_one`, `fetch_all`, and `execute_query` call. The connection is immediately closed after use.

**Measured impact:** A single MySQL connection setup costs 2-10 ms on localhost, 20-50 ms over a Docker network. With N+1 patterns (see DB-2), a page listing 20 publications opens ~21 connections (1 count + 1 list + 20 author lookups = **22 connections**, costing 440-1100 ms in connection overhead alone on Docker).

**Recommendation:** Replace per-call connections with a connection pool.

```python
# database.py — replace get_connection() with a pool
from mysql.connector.pooling import MySQLConnectionPool

class Database:
    _pool = None

    @classmethod
    def _get_pool(cls):
        if cls._pool is None:
            cls._pool = MySQLConnectionPool(
                pool_name="econ_pool",
                pool_size=10,          # tune to expected concurrency
                pool_reset_session=True,
                **db_config,
            )
        return cls._pool

    @staticmethod
    def get_connection():
        return Database._get_pool().get_connection()
```

**Expected improvement:** Eliminates ~95% of connection overhead. Pool checkout is sub-millisecond vs. 2-50 ms per fresh connection.

---

### DB-2: N+1 Query Patterns

**Severity: Critical**
**Files:** `api.py:222-225` (publications list), `api.py:279-291` (researchers list), `api.py:317-320` (researcher detail)

All three list endpoints follow the same pattern: fetch a list of rows, then loop over each row executing 1-2 additional queries per item.

**Publications list** (`/api/publications`):
- 1 COUNT query
- 1 SELECT query for the page
- N queries for `_get_authors_for_publication()` (one per publication)
- **Total: 2 + N connections** (N = per_page, max 100)

**Researchers list** (`/api/researchers`):
- 1 SELECT all researchers
- N queries for `_get_urls_for_researcher()`
- N queries for `_get_pub_count_for_researcher()`
- **Total: 1 + 2N connections** (N = total researcher count, unbounded)

**Researcher detail** (`/api/researchers/{id}`):
- 1 SELECT researcher
- 1 SELECT URLs
- 1 COUNT publications
- 1 SELECT publications
- M queries for `_get_authors_for_publication()` per publication
- **Total: 4 + M connections** (M = all publications for that researcher, unbounded)

**Recommendation:** Replace per-row queries with JOINs or batch-IN queries.

```python
# Example: batch-load authors for a list of publication IDs
def _get_authors_for_publications(pub_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch authors for multiple publications in a single query."""
    if not pub_ids:
        return {}
    placeholders = ",".join(["%s"] * len(pub_ids))
    rows = Database.fetch_all(
        f"""
        SELECT a.publication_id, r.id, r.first_name, r.last_name
        FROM authorship a
        JOIN researchers r ON r.id = a.researcher_id
        WHERE a.publication_id IN ({placeholders})
        ORDER BY a.publication_id, a.author_order
        """,
        tuple(pub_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in pub_ids}
    for pub_id, rid, first, last in rows:
        result[pub_id].append({"id": rid, "first_name": first, "last_name": last})
    return result

# In list_publications:
pub_ids = [row[0] for row in rows]
authors_map = _get_authors_for_publications(pub_ids)
items = [_format_publication(row, authors_map[row[0]]) for row in rows]
```

```python
# Researchers list: single query with aggregation
@app.get("/api/researchers")
async def list_researchers():
    rows = Database.fetch_all("""
        SELECT r.id, r.first_name, r.last_name, r.position, r.affiliation,
               COUNT(DISTINCT a.publication_id) AS pub_count
        FROM researchers r
        LEFT JOIN authorship a ON a.researcher_id = r.id
        GROUP BY r.id
    """)
    researcher_ids = [r[0] for r in rows]
    # Batch-fetch URLs
    url_rows = Database.fetch_all(
        f"SELECT researcher_id, id, page_type, url FROM researcher_urls "
        f"WHERE researcher_id IN ({','.join(['%s']*len(researcher_ids))})",
        tuple(researcher_ids)
    )
    # ... group url_rows by researcher_id ...
```

**Expected improvement:** Reduces publications endpoint from 22 queries to 3. Reduces researchers endpoint from 1+2N to 2-3. At 50 researchers, that is 101 queries down to 3.

---

### DB-3: Missing UNIQUE Constraint on Authorship

**Severity: High**
**File:** `database.py:142-153`, `publication.py:74-85`

The `authorship` table has no UNIQUE constraint on `(researcher_id, publication_id)`. The insert in `publication.py:80-85` does not check for duplicates. If a scrape re-processes a page and re-extracts the same publications (e.g., the dedup check in `save_publications` fails due to URL normalization differences), duplicate authorship rows accumulate. This causes:
- Inflated `publication_count` values returned by `_get_pub_count_for_researcher()`
- Duplicate author names in publication listings

**Recommendation:**
```sql
ALTER TABLE authorship
ADD UNIQUE KEY uq_researcher_pub (researcher_id, publication_id);
```
And change the INSERT to `INSERT IGNORE` or `ON DUPLICATE KEY UPDATE`.

---

### DB-4: Full Table Scan Deduplication

**Severity: High**
**File:** `publication.py:52-55`

```python
existing = Database.fetch_one(
    "SELECT id FROM publications WHERE LOWER(TRIM(title)) = %s AND url = %s",
    (normalized_title, url)
)
```

The `LOWER(TRIM(title))` expression prevents MySQL from using the `uq_title_url` index. Every dedup check is a full table scan on `publications`. As the publications table grows, this query becomes progressively slower.

**Recommendation:** Normalize titles before insertion and store them normalized, so the unique index can be used directly. Alternatively, add a `normalized_title` computed/generated column with its own index:

```sql
ALTER TABLE publications
ADD COLUMN title_normalized VARCHAR(200) GENERATED ALWAYS
  AS (LOWER(TRIM(title))) STORED,
ADD INDEX idx_title_norm_url (title_normalized, url(200));
```

Then query against `title_normalized = %s AND url = %s`, which hits the index.

---

### DB-5: `create_tables` Opens 6 Separate Connections

**Severity: Low**
**File:** `database.py:168-170`

`create_tables()` calls `execute_query()` once per table definition (6 tables), opening and closing 6 connections sequentially at startup. This is wasteful but only happens once.

**Recommendation:** Execute all DDL statements within a single connection.

---

## 2. Memory Management

### MEM-1: Unbounded Invalid JSON File Dumps

**Severity: Medium**
**File:** `publication.py:168-180`

When the OpenAI response is not valid JSON, the raw response is dumped to a new file in `invalid_json_dumps/` with no rotation, size limit, or cleanup. If the LLM model consistently returns non-JSON (e.g., due to prompt issues or model changes), this directory grows without bound.

**Quantified risk:** Each OpenAI response is typically 1-10 KB. With ~100 URLs scraped daily and even a 10% failure rate, that is ~10 files/day or ~3,600 files/year. Not catastrophic, but the directory is never cleaned and could exhaust inodes on constrained container filesystems.

**Recommendation:** Log the invalid response content at ERROR level instead of writing files, or implement a rotating dump with a maximum of N files:

```python
@staticmethod
def dump_invalid_json(response):
    MAX_DUMPS = 50
    dump_dir = "invalid_json_dumps"
    os.makedirs(dump_dir, exist_ok=True)
    # Rotate: delete oldest if at limit
    existing = sorted(os.listdir(dump_dir))
    while len(existing) >= MAX_DUMPS:
        os.remove(os.path.join(dump_dir, existing.pop(0)))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(dump_dir, f"invalid_json_{timestamp}.txt")
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(response)
```

---

### MEM-2: Unbounded Publication Accumulation in State

**Severity: Medium**
**File:** `app/src/app/NewsfeedContent.tsx:45-49`

The "Load more" pagination accumulates all loaded publications into a single `useState` array that never shrinks:

```typescript
setPublications((prev) => {
    const existingIds = new Set(prev.map((p) => p.id));
    const newItems = data.items.filter((p) => !existingIds.has(p.id));
    return [...prev, ...newItems];
});
```

Additionally, the dedup logic creates a new `Set` from all existing IDs on every page load, which is O(n) per load. After loading many pages, this array can grow to thousands of items, all held in memory and all rendered into the DOM.

**Recommendation:** Implement virtual scrolling (e.g., `react-window` or `@tanstack/virtual`) for large lists, or switch to traditional page-based navigation that replaces rather than accumulates.

---

## 3. Caching Opportunities

### CA-1: No Cache-Control Headers on API Responses

**Severity: Medium**
**File:** `api.py` (all endpoints)

No `Cache-Control`, `ETag`, or `Last-Modified` headers are set on any API response. The publications and researchers data changes infrequently (at most once per scrape cycle, typically every 24 hours), yet every browser navigation triggers a full round-trip to the API.

**Recommendation:** Add Cache-Control headers appropriate to each endpoint's volatility:

```python
@app.get("/api/publications")
async def list_publications(...):
    # ... existing logic ...
    response = JSONResponse(content=result)
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return response

@app.get("/api/researchers")
async def list_researchers():
    # ... existing logic ...
    response = JSONResponse(content=result)
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return response
```

This allows browsers and CDNs to serve cached responses for 5 minutes, with stale-while-revalidate providing seamless background updates.

---

### FE-1: SWR Installed But Unused

**Severity: High**
**File:** `app/package.json:16` (dependency), `app/src/lib/api.ts`, all content components

SWR (`swr@^2.4.1`) is listed as a production dependency but is not imported or used anywhere. All data fetching uses raw `useEffect` + `fetch` with manual loading/error state management. This means:

- **No client-side cache:** Navigating away and back triggers a full refetch.
- **No deduplication:** Multiple components requesting the same resource in parallel issue duplicate requests.
- **No stale-while-revalidate:** Users see loading spinners on every navigation.
- **Unnecessary boilerplate:** Each component manually tracks `isLoading`, `error`, and `data` states.

**Recommendation:** Adopt SWR across all data-fetching components:

```typescript
// app/src/lib/api.ts — add SWR-compatible fetcher
import useSWR from "swr";

const fetcher = (url: string) => fetch(url).then((r) => {
  if (!r.ok) throw new Error(`API error: ${r.status}`);
  return r.json();
});

export function usePublications(page = 1, perPage = 20) {
  return useSWR<PaginatedResponse<Publication>>(
    `${API_BASE_URL}/api/publications?page=${page}&per_page=${perPage}`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 }
  );
}

export function useResearchers() {
  return useSWR<{ items: Researcher[] }>(
    `${API_BASE_URL}/api/researchers`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 }
  );
}

export function useResearcher(id: number) {
  return useSWR<ResearcherDetail>(
    `${API_BASE_URL}/api/researchers/${id}`,
    fetcher,
    { revalidateOnFocus: false }
  );
}
```

```typescript
// ResearchersContent.tsx — simplified with SWR
"use client";
import { useResearchers } from "@/lib/api";
import ResearcherCard from "@/components/ResearcherCard";

export default function ResearchersContent() {
  const { data, error, isLoading } = useResearchers();
  if (isLoading) return <LoadingSkeleton />;
  if (error) return <ErrorMessage message="Failed to load researchers." />;
  return (
    <div className="space-y-3">
      {data?.items.map((r) => <ResearcherCard key={r.id} researcher={r} />)}
    </div>
  );
}
```

**Expected improvement:** Instant navigation for cached data, ~60% reduction in API calls during typical user sessions.

---

## 4. I/O Bottlenecks

### IO-1: Unpaginated Researchers Endpoint

**Severity: High**
**Files:** `api.py:273-291`

`GET /api/researchers` fetches ALL researchers in a single query with no `LIMIT` or `OFFSET`. Combined with the N+1 pattern (DB-2), response time grows linearly with researcher count. At 500 researchers, this endpoint issues ~1001 queries and returns a payload that could exceed 500 KB.

**Recommendation:** Add pagination matching the publications endpoint pattern:

```python
@app.get("/api/researchers")
async def list_researchers(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    count_row = Database.fetch_one("SELECT COUNT(*) FROM researchers")
    total = count_row[0] if count_row else 0
    pages = math.ceil(total / per_page) if total else 0
    offset = (page - 1) * per_page

    rows = Database.fetch_all(
        """SELECT r.id, r.first_name, r.last_name, r.position, r.affiliation,
                  COUNT(DISTINCT a.publication_id) AS pub_count
           FROM researchers r
           LEFT JOIN authorship a ON a.researcher_id = r.id
           GROUP BY r.id
           ORDER BY r.last_name, r.first_name
           LIMIT %s OFFSET %s""",
        (per_page, offset),
    )
    # Batch-fetch URLs for just this page of researchers...
    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": pages}
```

---

### IO-2: Synchronous Blocking I/O in Scraper

**Severity: High**
**Files:** `html_fetcher.py:100-131`, `scheduler.py:60-79`

The scraper uses `requests.Session` (synchronous) for HTTP calls. The `run_scrape_job()` function processes URLs sequentially, with each URL incurring:
- `time.sleep()` for rate limiting (2s default)
- DNS resolution for SSRF validation
- `robots.txt` fetch (another HTTP request)
- The actual page fetch (with up to 3 retries and exponential backoff)

While this runs in a background thread (not blocking the event loop directly), it holds a single thread for what could be parallelized across different domains.

**Quantified impact:** With 100 URLs across 20 domains and a 2-second rate limit, the minimum scrape time is ~200 seconds. With parallelism across domains, this could be reduced to ~10 seconds (20 domains x 5 URLs/domain x 2s rate limit / 20 parallel workers).

**Recommendation:** Use `asyncio` + `aiohttp` with per-domain semaphores, or use `concurrent.futures.ThreadPoolExecutor` with domain-grouped batches:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

def run_scrape_job():
    urls = Researcher.get_all_researcher_urls()
    # Group by domain for rate-limit-aware parallel execution
    by_domain = defaultdict(list)
    for url_id, researcher_id, url, page_type in urls:
        domain = urlparse(url).hostname
        by_domain[domain].append((url_id, researcher_id, url, page_type))

    # Process domains in parallel, URLs within a domain sequentially
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_scrape_domain_urls, domain_urls): domain
            for domain, domain_urls in by_domain.items()
        }
        for future in as_completed(futures):
            future.result()  # propagate exceptions
```

---

### IO-3: robots.txt Fetched Every Scrape Without Caching

**Severity: Low**
**File:** `html_fetcher.py:32-46`

`is_allowed_by_robots()` creates a new `RobotFileParser`, fetches `robots.txt`, and parses it on every single URL check. For a domain with 10 researcher URLs, `robots.txt` is fetched 10 times per scrape cycle.

**Recommendation:** Cache parsed robots.txt per domain with a TTL:

```python
_robots_cache: dict[str, tuple[RobotFileParser, float]] = {}
ROBOTS_CACHE_TTL = 3600  # 1 hour

@staticmethod
def is_allowed_by_robots(url):
    parsed = urlparse(url)
    domain = parsed.netloc
    now = time.time()
    if domain in HTMLFetcher._robots_cache:
        rp, cached_at = HTMLFetcher._robots_cache[domain]
        if now - cached_at < ROBOTS_CACHE_TTL:
            return rp.can_fetch('HTMLFetcher/1.0', url)
    rp = RobotFileParser()
    rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    rp.read()
    HTMLFetcher._robots_cache[domain] = (rp, now)
    return rp.can_fetch('HTMLFetcher/1.0', url)
```

---

## 5. Concurrency Issues

### CC-1: Race Condition in Scrape Trigger

**Severity: High**
**File:** `api.py:347-353`

```python
if not scheduler._scrape_lock.acquire(blocking=False):
    raise HTTPException(status_code=409, ...)
# Release immediately — run_scrape_job will re-acquire
scheduler._scrape_lock.release()

log_id = create_scrape_log()
# ...
t = threading.Thread(target=run_scrape_job, daemon=True)
t.start()
```

There is a TOCTOU (time-of-check-time-of-use) race between the lock release on line 353 and the lock re-acquire inside `run_scrape_job()`. Two near-simultaneous requests can both pass the check, both release, and both start threads. This results in:
- Two concurrent scrape jobs running simultaneously
- Two `scrape_log` entries created for a single logical scrape
- Potential duplicate publications from parallel extraction

**Recommendation:** Do not release the lock. Instead, pass it to the thread or use a different synchronization mechanism:

```python
@app.post("/api/scrape", status_code=201)
async def trigger_scrape(request: Request):
    # ... auth check ...
    if not scheduler._scrape_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scrape is already running.")
    # DO NOT release — the thread function releases it in its finally block
    log_id = create_scrape_log()
    t = threading.Thread(
        target=_run_scrape_already_locked,
        daemon=True,
    )
    t.start()
    return {"scrape_id": log_id, "status": "running", ...}

def _run_scrape_already_locked():
    """Run scrape assuming lock is already held. Releases on exit."""
    try:
        # ... scrape logic (without re-acquiring) ...
    finally:
        scheduler._scrape_lock.release()
```

---

### CC-2: Class-Level Mutable State on HTMLFetcher

**Severity: Medium**
**Files:** `html_fetcher.py:23-29`

```python
class HTMLFetcher:
    session = requests.Session()       # shared mutable state
    _domain_last_request = {}          # shared mutable dict
```

`session` and `_domain_last_request` are class-level attributes shared across all threads. `requests.Session` is thread-safe for `.get()`, but `_domain_last_request` is a plain dict modified without locking. With the current single-threaded scraper this is safe, but if IO-2's parallelism recommendation is adopted, concurrent writes to `_domain_last_request` would create a race condition.

**Recommendation:** If parallelizing, protect `_domain_last_request` with a `threading.Lock`, or use per-domain locks:

```python
_rate_lock = threading.Lock()

@staticmethod
def _rate_limit(url):
    domain = urlparse(url).hostname
    with HTMLFetcher._rate_lock:
        if domain in HTMLFetcher._domain_last_request:
            elapsed = time.time() - HTMLFetcher._domain_last_request[domain]
            if elapsed < RATE_LIMIT_SECONDS:
                wait = RATE_LIMIT_SECONDS - elapsed
                time.sleep(wait)
        HTMLFetcher._domain_last_request[domain] = time.time()
```

---

## 6. Frontend Performance

### FE-2: `NEXT_PUBLIC_API_URL` SSR/Docker Mismatch

**Severity: Medium**
**Files:** `app/src/lib/api.ts:8-9`, `docker-compose.yml:47`

```typescript
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
```

`NEXT_PUBLIC_*` variables are inlined at **build time** in Next.js. In docker-compose, `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000`, which gets baked into the frontend bundle during `npm run build`. At runtime:

- **Server-side rendering** in the Docker frontend container: `localhost:8000` refers to the frontend container itself (no API there), so SSR fetches fail silently or with errors.
- **Client-side rendering** in the browser: `localhost:8000` correctly reaches the API (if port-mapped), but only on the development machine.

Currently all components use `"use client"` so SSR data fetching is not happening, which masks the issue. But it means the application cannot leverage SSR for initial page loads.

**Recommendation:** Use a runtime configuration approach or a Next.js API route proxy:

```javascript
// next.config.mjs
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_URL || "http://api:8000"}/api/:path*`,
      },
    ];
  },
};
export default nextConfig;
```

Then change the frontend to use `/api/...` as a relative URL, which works for both SSR and client-side rendering.

---

### FE-3: No Next.js Output Optimization

**Severity: Low**
**File:** `app/next.config.mjs`

```javascript
const nextConfig = {};
```

The Next.js config is entirely empty. For Docker deployments, the `standalone` output mode significantly reduces image size by including only the files needed for production:

```javascript
const nextConfig = {
  output: "standalone",
};
```

This reduces the Docker image from ~1 GB (full `node_modules`) to ~100-150 MB (standalone output). It also improves container startup time.

---

## 7. Scalability Concerns

### SC-1: Single-Worker Uvicorn

**Severity: Medium**
**File:** `Dockerfile.api:16`

```dockerfile
CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

Uvicorn runs with a single worker by default. Since all database operations are synchronous (blocking the event loop via `mysql.connector`), a single worker can only handle one request at a time that involves database access. Under concurrent load, requests queue behind each other.

**Recommendation:** Use Gunicorn with Uvicorn workers:

```dockerfile
CMD ["gunicorn", "api:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000"]
```

Add `gunicorn` to `requirements.txt`. With 4 workers and connection pooling (DB-1), the API can handle 4 concurrent requests. Pair this with an async MySQL driver (e.g., `aiomysql`) for true async I/O.

**Note:** The APScheduler `BackgroundScheduler` and the `threading.Lock` in `scheduler.py` are process-local. With multiple Gunicorn workers, each worker spawns its own scheduler. This must be addressed by running the scheduler in a separate process or using a distributed lock (e.g., database-backed).

---

### SC-2: No Database Connection Retry at Startup

**Severity: Low**
**Files:** `api.py:44-48`, `database.py:34-43`

The `lifespan` function calls `Database.create_tables()` immediately at startup. If the database is not yet ready (common in Docker Compose despite `depends_on: condition: service_healthy`), the application crashes without retrying.

**Recommendation:** Add retry logic with backoff:

```python
import time

@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(10):
        try:
            Database.create_tables()
            break
        except Exception:
            if attempt == 9:
                raise
            time.sleep(2 ** attempt)
    start_scheduler()
    yield
    shutdown_scheduler()
```

---

## Prioritized Remediation Plan

### Phase 1 — Immediate (Critical, 1-2 days)

| Priority | Finding | Effort | Impact |
|----------|---------|--------|--------|
| P0 | DB-1: Add connection pooling | 1 hour | Eliminates ~95% of connection overhead |
| P0 | DB-2: Fix N+1 queries with batch loading | 4 hours | Reduces query count from O(N) to O(1) per endpoint |
| P1 | CC-1: Fix race condition in scrape trigger | 30 min | Prevents duplicate scrapes and data corruption |

### Phase 2 — Short-term (High, 1 week)

| Priority | Finding | Effort | Impact |
|----------|---------|--------|--------|
| P1 | IO-1: Add pagination to researchers endpoint | 2 hours | Bounds response time and payload size |
| P1 | FE-1: Adopt SWR for client-side caching | 3 hours | Eliminates redundant fetches, instant navigation |
| P1 | DB-3: Add UNIQUE constraint on authorship | 30 min | Prevents data integrity issues |
| P1 | DB-4: Fix full-table-scan dedup query | 1 hour | Prevents O(N) dedup as publications grow |

### Phase 3 — Medium-term (Medium, 2-4 weeks)

| Priority | Finding | Effort | Impact |
|----------|---------|--------|--------|
| P2 | SC-1: Multi-worker uvicorn + Gunicorn | 2 hours | 4x concurrent request capacity |
| P2 | IO-2: Parallelize scraper across domains | 4 hours | 10-20x faster scrape cycles |
| P2 | CA-1: Add Cache-Control headers | 1 hour | Reduces API load, faster perceived performance |
| P2 | FE-2: Fix NEXT_PUBLIC_API_URL for Docker SSR | 2 hours | Enables SSR, proper Docker deployment |
| P2 | MEM-1: Cap invalid JSON dumps | 30 min | Prevents unbounded disk usage |
| P2 | CC-2: Thread-safe rate limiter | 1 hour | Required if IO-2 is implemented |

### Phase 4 — Low priority

| Priority | Finding | Effort | Impact |
|----------|---------|--------|--------|
| P3 | MEM-2: Virtual scrolling for publication list | 3 hours | Better performance with large datasets |
| P3 | FE-3: Standalone Next.js output | 15 min | Smaller Docker image |
| P3 | IO-3: Cache robots.txt | 30 min | Faster scrape cycles |
| P3 | DB-5: Single-connection table creation | 15 min | Marginal startup improvement |
| P3 | SC-2: Startup retry logic | 30 min | More resilient Docker startup |

---

## Scaling Projections

| Metric | Current (est.) | At 500 researchers / 5000 pubs | After remediation |
|--------|---------------|-------------------------------|-------------------|
| `/api/researchers` latency | ~200 ms | ~10-50 seconds | ~50 ms |
| `/api/researchers` queries | 1 + 2N | 1001 | 2-3 |
| `/api/publications` latency (page=1) | ~100 ms | ~500 ms | ~20 ms |
| `/api/publications` queries | 2 + N | 22-102 | 3 |
| Scrape cycle duration (100 URLs) | ~200s | ~200s | ~10-20s |
| DB connections per request | 2-100+ | 2-1000+ | 2-3 (pooled) |
| Concurrent API capacity | 1 | 1 | 4-16 |
