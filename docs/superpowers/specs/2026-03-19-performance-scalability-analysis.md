# Performance & Scalability Analysis

**Date:** 2026-03-19
**Scope:** Full econ-newsfeed project -- Python/FastAPI backend, Next.js 14 frontend, MySQL 8

---

## Table of Contents

1. [Critical Findings](#1-critical-findings)
2. [High Severity Findings](#2-high-severity-findings)
3. [Medium Severity Findings](#3-medium-severity-findings)
4. [Low Severity Findings](#4-low-severity-findings)
5. [Positive Findings (Preserve)](#5-positive-findings-preserve)
6. [Summary Scorecard](#6-summary-scorecard)

---

## 1. Critical Findings

### 1.1 Gunicorn 4 Workers Each Spawn Independent APScheduler -- Quadruple Scheduler Storm

**Severity:** Critical
**Files:** `Dockerfile.api` (line 19), `scheduler.py` (lines 260-283), `api.py` (lines 56-78)

**Problem:** The Docker CMD starts Gunicorn with `--workers 4`. Each Gunicorn worker forks the ASGI app, which triggers the `lifespan` context manager, which calls `start_scheduler()`. This means 4 independent `BackgroundScheduler` instances all fire `run_scrape_job` at the same interval. While the MySQL advisory lock (`GET_LOCK`) prevents truly parallel scrapes, it causes:

- 4 connections opened per interval just to check/acquire the lock
- 3 workers wasting CPU and memory on scheduler threads that always fail to acquire the lock
- Lock contention spikes every `SCRAPE_INTERVAL_HOURS`
- Each worker's `signal.signal(SIGTERM, ...)` call overwrites the previous handler, causing undefined shutdown behavior

**Performance Impact:** Constant background overhead of 3 idle scheduler threads plus periodic lock contention storms. In production with `SCRAPE_INTERVAL_HOURS=24`, 3 out of 4 workers hit `_acquire_db_lock()` simultaneously, creating a MySQL connection burst (3 extra connections opened, lock-checked, and closed every 24h). More seriously, the signal handler race on SIGTERM can leave orphaned lock connections.

**Recommendation:** Run the scheduler in exactly one process, separate from the API workers.

```python
# Option A: Use Gunicorn's --preload with a worker-0 guard
# In api.py lifespan:
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing table creation ...
    # Only start scheduler in worker 0 (or the master process)
    worker_id = os.environ.get("GUNICORN_WORKER_ID", "0")
    if worker_id == "0":
        start_scheduler()
    yield
    if worker_id == "0":
        shutdown_scheduler()

# Option B (simpler): Run scheduler as a separate container/process
# Dockerfile.scheduler:
# CMD ["python", "-c", "from scheduler import start_scheduler; start_scheduler(); import time; time.sleep(float('inf'))"]
```

---

### 1.2 `_disambiguate_researcher` Creates a New OpenAI Client Per Call

**Severity:** Critical
**File:** `database.py` (lines 443-487)

**Problem:** Every invocation of `_disambiguate_researcher` instantiates a new `OpenAI()` client (line 463). The OpenAI SDK creates an `httpx.Client` per instance with its own connection pool. During a scrape with many new authors, this can be called dozens or hundreds of times, creating and discarding TCP connections.

Meanwhile, `publication.py` (line 15) already creates a module-level `_openai_client = OpenAI(...)` that is properly reused across calls.

**Performance Impact:** Each `OpenAI()` instantiation involves TLS handshake overhead (~100-300ms). For a scrape processing 50 new author names, this adds 5-15 seconds of pure connection setup time. It also fragments the HTTP/2 connection multiplexing that a single client would provide.

**Recommendation:** Reuse the existing module-level client from `publication.py`.

```python
# In database.py, replace lines 462-468:
# Instead of:
#     from openai import OpenAI
#     client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
#     response = client.chat.completions.create(...)

# Use the existing shared client:
from publication import _openai_client
model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
response = _openai_client.chat.completions.create(
    messages=[{"role": "user", "content": prompt}],
    model=model,
)
```

---

### 1.3 Race Condition in Snapshot Append -- Duplicate Writes Under Concurrency

**Severity:** Critical
**File:** `database.py` (lines 569-597, 628-679)

**Problem:** Both `append_researcher_snapshot` and `append_paper_snapshot` follow a check-then-act pattern:

1. Read latest snapshot hash (separate DB connection from pool)
2. Compare with new hash
3. If different, INSERT snapshot + UPDATE denormalized table (in a new connection)

Between steps 1 and 3, another concurrent worker (Gunicorn worker or thread) can perform the same check-then-act, leading to duplicate snapshots and potentially inconsistent feed events.

The `append_paper_snapshot` method is particularly dangerous because it also creates `feed_events` entries. A race between two workers processing the same paper (e.g., found on two different researcher pages in the same scrape) could generate duplicate `status_change` events.

**Performance Impact:** Duplicate snapshot rows waste storage and create incorrect feed entries that confuse users. The duplicate feed events also inflate the `COUNT(*)` query in `list_publications`, causing incorrect pagination.

**Recommendation:** Use a database-level guard to prevent duplicates atomically.

```python
@staticmethod
def append_paper_snapshot(paper_id, status, venue, abstract, draft_url, year, source_url=None):
    content_hash = Database._compute_paper_content_hash(status, venue, abstract, draft_url, year)
    now = datetime.now(timezone.utc)

    with Database.get_connection() as conn:
        with conn.cursor() as cursor:
            # Atomic check: SELECT ... FOR UPDATE prevents concurrent reads
            cursor.execute(
                "SELECT content_hash FROM paper_snapshots "
                "WHERE paper_id = %s ORDER BY scraped_at DESC LIMIT 1 FOR UPDATE",
                (paper_id,),
            )
            prev = cursor.fetchone()
            if prev and prev[0] == content_hash:
                return False

            # ... rest of INSERT + UPDATE in same transaction ...
            conn.commit()
    return True
```

---

## 2. High Severity Findings

### 2.1 `SCRAPE_ON_STARTUP` Blocks API Readiness

**Severity:** High
**File:** `scheduler.py` (lines 281-283)

**Problem:** When `SCRAPE_ON_STARTUP=true`, `start_scheduler()` calls `run_scrape_job()` synchronously (line 283) during the `lifespan` startup. This function processes every researcher URL sequentially, making HTTP requests with 2-second rate limits. For a dataset of 100 URLs, startup is blocked for ~200+ seconds.

In Kubernetes / Cloud Run, the readiness probe will fail, triggering container restarts in a crash loop.

**Performance Impact:** API unavailable for the entire duration of the first scrape (minutes to tens of minutes depending on URL count). Health check failures cascade into restart loops.

**Recommendation:** Run the startup scrape in a background thread.

```python
def start_scheduler():
    global _scheduler
    # ... existing scheduler setup ...
    _scheduler.start()

    if SCRAPE_ON_STARTUP:
        logger.info("SCRAPE_ON_STARTUP is true, triggering immediate scrape in background")
        import threading
        threading.Thread(target=run_scrape_job, daemon=True).start()
```

---

### 2.2 Connection Pool Size Too Small for Multi-Worker Deployment

**Severity:** High
**File:** `database.py` (lines 25-32)

**Problem:** The connection pool is created per-process (module-level `_pool` global) with a default size of 5. With 4 Gunicorn workers, that is 20 total connections. But each worker can handle multiple concurrent requests (uvicorn async), and the scrape job's `run_scrape_job` holds connections for extended periods.

Additionally, `_acquire_db_lock()` in `scheduler.py` (line 27) creates a raw `mysql.connector.connect()` connection *outside* the pool, and holds it for the entire scrape duration. This connection is invisible to the pool and counts against the MySQL `max_connections` limit.

The `list_publications` endpoint makes 2 sequential queries (count + paginated fetch) plus 1 batch author query = 3 pool checkouts per request. Under moderate load (e.g., 10 concurrent users), each worker needs at least 30 connections, far exceeding the pool of 5.

**Performance Impact:** Under load, requests queue waiting for pool connections. With `pool_size=5` and default MySQL `wait_timeout`, connection exhaustion causes `PoolError: Failed getting connection` errors.

**Recommendation:**

```python
# database.py -- scale pool to match expected concurrency
_DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '10'))

# And consolidate the advisory lock connection into the pool:
# scheduler.py -- use pooled connection
def _acquire_db_lock():
    try:
        conn = Database.get_connection()  # from pool
        cursor = conn.cursor()
        cursor.execute("SELECT GET_LOCK(%s, 0)", (_LOCK_NAME,))
        result = cursor.fetchone()
        cursor.close()
        if result and result[0] == 1:
            return conn
        conn.close()
        return None
    except Exception as e:
        logger.error(f"Failed to acquire DB advisory lock: {e}")
        return None
```

---

### 2.3 Sequential Per-Publication DB Round-Trips in `save_publications`

**Severity:** High
**File:** `publication.py` (lines 72-163)

**Problem:** For each publication in a batch, `save_publications` performs:

1. `Database.get_connection()` -- checkout from pool
2. `INSERT IGNORE INTO papers` -- write
3. Conditional: `SELECT id FROM papers WHERE title_hash = %s` -- read
4. `INSERT IGNORE INTO paper_urls` -- write
5. Conditional: `INSERT INTO feed_events` -- write
6. Per-author loop: `Database.get_researcher_id()` (which itself does 1-3 DB queries, potentially including an LLM call) + `INSERT IGNORE INTO authorship`
7. `conn.commit()`

For a page with 30 publications and 3 authors each, that is approximately 30 * (2 + 3*3) = 330 DB round-trips, each checking out and returning a connection from the pool.

**Performance Impact:** At ~1ms per round-trip on a local DB (higher on cloud), this is 330ms minimum per page. With network latency to a remote DB (5-10ms), this grows to 1.6-3.3 seconds per page. The `get_researcher_id` calls compound this with potential LLM disambiguation calls.

**Recommendation:** Batch the operations. Collect all title hashes first, do a single `SELECT ... WHERE title_hash IN (...)`, then batch-insert the new papers.

```python
@staticmethod
def save_publications(url, publications, is_seed=False):
    # Pre-compute all title hashes
    hashes = [Database.compute_title_hash(p['title']) for p in publications]

    # Single query to find existing papers
    if hashes:
        placeholders = ",".join(["%s"] * len(hashes))
        existing = Database.fetch_all(
            f"SELECT title_hash, id FROM papers WHERE title_hash IN ({placeholders})",
            tuple(hashes),
        )
        existing_map = {row[0]: row[1] for row in existing}
    else:
        existing_map = {}

    # Then process with known state, minimizing round-trips
    with Database.get_connection() as conn:
        with conn.cursor() as cursor:
            for pub, title_hash in zip(publications, hashes):
                if title_hash in existing_map:
                    publication_id = existing_map[title_hash]
                    # Only insert paper_url, skip duplicate INSERT IGNORE
                else:
                    # INSERT new paper
                    cursor.execute(...)
                    publication_id = cursor.lastrowid
                    existing_map[title_hash] = publication_id
                # ... authors ...
            conn.commit()
```

---

### 2.4 `robots.txt` Fetched Per-URL Instead of Per-Domain

**Severity:** High
**File:** `html_fetcher.py` (lines 63-82)

**Problem:** `is_allowed_by_robots(url)` fetches and parses the robots.txt file from the target domain every time it is called. If a researcher has 3 URLs on the same domain (homepage, CV, publications), robots.txt is fetched 3 times. Across all researchers, the same domain's robots.txt may be fetched dozens of times per scrape.

There is no caching. The `RobotFileParser` instance is created, used once, and discarded.

**Performance Impact:** For a scrape cycle with 200 URLs across 50 unique domains, this creates 200 HTTP requests to fetch robots.txt instead of 50. At 500ms-2s per request (including DNS, TLS, rate limiting), this adds 75-300 seconds of overhead to each scrape cycle.

**Recommendation:** Cache robots.txt per domain for the duration of a scrape.

```python
class HTMLFetcher:
    _robots_cache: dict[str, RobotFileParser | None] = {}

    @staticmethod
    def is_allowed_by_robots(url):
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        if domain in HTMLFetcher._robots_cache:
            rp = HTMLFetcher._robots_cache[domain]
            return rp.can_fetch('HTMLFetcher/1.0', url) if rp else True

        try:
            robots_url = f"{domain}/robots.txt"
            resp = requests.get(robots_url, timeout=10, headers={
                'User-Agent': SCRAPER_USER_AGENT,
            })
            if resp.status_code != 200:
                HTMLFetcher._robots_cache[domain] = None
                return True
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            HTMLFetcher._robots_cache[domain] = rp
            return rp.can_fetch('HTMLFetcher/1.0', url)
        except Exception:
            HTMLFetcher._robots_cache[domain] = None
            return True
```

---

### 2.5 Researcher List Fetches All Results Without Pagination (`per_page: "100"`)

**Severity:** High
**File:** `app/src/lib/api.ts` (line 87)

**Problem:** `useResearchersFiltered` hardcodes `per_page: "100"`, and the researchers page has no pagination controls. As the dataset grows beyond 100 researchers, results are silently truncated. The `useResearchers` hook (line 64-68) also fetches all researchers with no `per_page` limit at all -- it defaults to the API's 20, which is inconsistent with the filtered endpoint's 100.

**Performance Impact:** Currently benign with a small dataset, but as researchers grow:
- At 100+: users see truncated, misleading results
- At 500+: single API response carrying 500 researchers with their URLs, fields, and publication counts becomes a multi-MB JSON payload
- Backend executes 4 batch queries (researchers + URLs + pub_counts + fields) for all 500 results

**Recommendation:** Add proper pagination to the researchers page, matching the newsfeed pattern.

```typescript
// api.ts
export function useResearchersFiltered(
  page = 1,
  perPage = 20,
  filters?: ResearcherFilters,
) {
  const params = new URLSearchParams({
    page: String(page),
    per_page: String(perPage),
  });
  if (filters?.institution) params.set("institution", filters.institution);
  if (filters?.field) params.set("field", filters.field);
  if (filters?.position) params.set("position", filters.position);
  const url = `/api/researchers?${params.toString()}`;
  return useSWR<PaginatedResponse<Researcher>>(url, fetchJson);
}
```

---

## 3. Medium Severity Findings

### 3.1 `LIKE '%keyword%'` Queries Cannot Use Indexes -- Full Table Scans

**Severity:** Medium
**Files:** `api.py` (lines 366-388, 634-642)

**Problem:** The `institution` and `preset=top20` filters use `LIKE '%keyword%'` patterns (leading wildcard), which cannot use the `idx_affiliation` index on the `researchers` table. Every such query triggers a full table scan.

The `top20` preset is especially expensive: it generates a `WHERE` clause with 22 `LIKE '%keyword%'` terms joined by `OR`, wrapped in a correlated subquery through the `authorship` table.

**Performance Impact:** At current scale (likely <1000 researchers), this is sub-second. At 10,000+ researchers with proportional papers and authorship rows, each `top20` filtered query could take 500ms-2s. With 5-minute Cache-Control headers, this is tolerable but will degrade as the dataset grows.

**Recommendation:** Add a denormalized `is_top20` boolean column to `researchers`, populated during import/scrape, and index it. Or use MySQL fulltext indexes for affiliation search.

```sql
-- Add to researchers table
ALTER TABLE researchers ADD COLUMN is_top20 BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE researchers ADD INDEX idx_top20 (is_top20);

-- Populate during researcher import or via periodic job
UPDATE researchers SET is_top20 = TRUE
WHERE affiliation LIKE '%MIT%' OR affiliation LIKE '%Harvard%' -- etc.
```

---

### 3.2 Missing Composite Index on `feed_events` for Primary Query Pattern

**Severity:** Medium
**File:** `database.py` (lines 271-284), `api.py` (lines 393-417)

**Problem:** The primary newsfeed query joins `feed_events fe JOIN papers p ON p.id = fe.paper_id` with `ORDER BY fe.created_at DESC LIMIT/OFFSET`. The `feed_events` table has separate indexes on `paper_id`, `created_at`, and `event_type`, but no composite index that covers the join + sort pattern.

MySQL will either:
- Use `idx_created_at` for sorting but need a lookup per row for the JOIN
- Use `idx_paper_id` for the JOIN but need a filesort for ordering

**Performance Impact:** For a feed_events table with 10,000+ rows, the `ORDER BY ... LIMIT ... OFFSET` without a covering index means MySQL reads and sorts all matching rows before applying the limit. At 100,000 events, this becomes a noticeable query (~100-500ms).

**Recommendation:** Add a composite index and consider keyset pagination.

```sql
-- Composite index covering the primary query pattern
ALTER TABLE feed_events ADD INDEX idx_created_paper (created_at DESC, paper_id);

-- For even better performance, switch to keyset pagination:
-- WHERE fe.created_at < %s ORDER BY fe.created_at DESC LIMIT %s
-- (instead of OFFSET-based pagination)
```

---

### 3.3 Synchronous HTTP Calls in Async FastAPI Endpoints

**Severity:** Medium
**Files:** `api.py` (all `def` endpoints), `database.py` (all `Database.*` methods)

**Problem:** All API endpoints are defined as synchronous `def` functions (not `async def`). In FastAPI, synchronous route handlers are run in a threadpool (`anyio.to_thread.run_sync`). This means each request occupies a thread from the default thread pool (40 threads).

The database calls use `mysql.connector`, which is a synchronous driver. This is acceptable for now, but it means the entire request pipeline is synchronous, and the threadpool becomes the concurrency bottleneck.

The `POST /api/scrape` endpoint (line 780) is `async def` but spawns a daemon thread -- this is fine. However, the `scheduler.is_scrape_running()` call inside it creates a brand new MySQL connection outside the pool, blocking the async event loop.

**Performance Impact:** Under moderate concurrent load (40+ simultaneous API requests), all threads are occupied, and new requests queue. The default 40-thread pool plus 4 workers = 160 concurrent requests max before queueing.

**Recommendation:** For the immediate term, increase the threadpool size. Longer term, migrate to an async MySQL driver (aiomysql or asyncmy).

```python
# Quick fix: increase threadpool in Dockerfile.api
CMD ["gunicorn", "api:app", "--workers", "4",
     "--worker-class", "uvicorn.workers.UvicornWorker",
     "--bind", "0.0.0.0:8000",
     "--env", "ANYIO_MAX_THREADS=100"]

# In POST /api/scrape, make the lock check non-blocking:
@app.post("/api/scrape", status_code=201)
async def trigger_scrape(request: Request):
    api_key = request.headers.get("X-API-Key", "")
    if not api_key or not hmac.compare_digest(api_key, _SCRAPE_API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")

    # Run the blocking lock check in the threadpool
    import asyncio
    is_running = await asyncio.to_thread(scheduler.is_scrape_running)
    if is_running:
        raise HTTPException(status_code=409, ...)
    # ...
```

---

### 3.4 No Application-Level Caching for Frequently-Read, Rarely-Changed Data

**Severity:** Medium
**Files:** `api.py` (lines 587-615)

**Problem:** The `/api/fields` and `/api/filter-options` endpoints query the database on every request. These datasets (research fields, distinct institutions, distinct positions) change only when researchers are added or updated -- at most once per scrape cycle (every 24h). Yet each page load hits the database for them.

The `Cache-Control: public, max-age=600` header on `/api/filter-options` helps the browser/CDN layer, but the server still executes the query for every cache miss.

**Performance Impact:** At current scale, these are fast queries (<10ms). But they are called on every page load by every user. With 100 concurrent users refreshing the researchers page, that is 100 identical `SELECT DISTINCT affiliation` queries per 10-minute window.

**Recommendation:** Add in-memory caching with a TTL.

```python
import functools
import time

def _ttl_cache(seconds=600):
    """Simple TTL cache decorator for no-arg functions."""
    def decorator(func):
        cache = {"value": None, "expires": 0}
        @functools.wraps(func)
        def wrapper():
            if time.time() < cache["expires"] and cache["value"] is not None:
                return cache["value"]
            result = func()
            cache["value"] = result
            cache["expires"] = time.time() + seconds
            return result
        wrapper.invalidate = lambda: cache.update(value=None, expires=0)
        return wrapper
    return decorator

@_ttl_cache(seconds=600)
def _get_filter_options():
    institutions = Database.fetch_all(
        "SELECT DISTINCT affiliation FROM researchers WHERE affiliation IS NOT NULL ..."
    )
    # ... same logic ...
    return {"institutions": ..., "positions": ..., "fields": ...}
```

---

### 3.5 `HTMLFetcher.session` is a Class-Level `requests.Session` -- Not Thread-Safe

**Severity:** Medium
**File:** `html_fetcher.py` (lines 51-53)

**Problem:** `requests.Session` is [not thread-safe](https://requests.readthedocs.io/en/latest/user/advanced/#session-objects). The `session` attribute is shared across all threads (Gunicorn workers are processes, but `extract_data_from_htmls_concurrent` in `main.py` uses `ThreadPoolExecutor` with 8 workers, all sharing the same `Session`).

Concurrent `.get()` calls on the same Session can corrupt internal state (cookie jar, connection pool tracking), leading to intermittent request failures or data leaks between requests.

**Performance Impact:** Intermittent failures during concurrent extraction (`make parse-fast`). Hard to reproduce and diagnose. Could also cause incorrect cookies being sent to wrong domains.

**Recommendation:** Use a thread-local session or create sessions per-thread.

```python
import threading

class HTMLFetcher:
    _thread_local = threading.local()

    @staticmethod
    def _get_session():
        if not hasattr(HTMLFetcher._thread_local, 'session'):
            session = requests.Session()
            session.headers.update({'User-Agent': SCRAPER_USER_AGENT})
            HTMLFetcher._thread_local.session = session
        return HTMLFetcher._thread_local.session

    @staticmethod
    def fetch_html(url, timeout=10, max_retries=3):
        HTMLFetcher._rate_limit(url)
        session = HTMLFetcher._get_session()
        # ... rest unchanged ...
```

---

### 3.6 `_domain_last_request` Dict Grows Unbounded Across Scrape Cycles

**Severity:** Medium
**File:** `html_fetcher.py` (lines 57-58)

**Problem:** `_domain_last_request` and `_domain_locks` are class-level dicts that are never cleared. Each unique domain adds an entry. Over many scrape cycles with evolving researcher URLs, these dicts accumulate stale entries. The `_domain_locks` dict is worse because each entry holds a `threading.Lock` object.

**Performance Impact:** Memory leak is slow (~100 bytes per domain entry including the Lock object). After 1000 scrape cycles visiting 50 unique domains with some churn, this amounts to ~50-100 KB -- negligible individually but indicative of the pattern. The real risk is if domains rotate frequently (e.g., vanity domains), which could accumulate thousands of stale entries.

**Recommendation:** Clear the rate-limit state at the start of each scrape cycle, or use a TTL-based cache.

```python
@classmethod
def reset_rate_limit_state(cls):
    """Call at the start of each scrape cycle to prevent unbounded growth."""
    with cls._domain_locks_global:
        cls._domain_last_request.clear()
        cls._domain_locks.clear()
```

---

### 3.7 `get_all_publications()` Loads Entire Papers Table Into Memory

**Severity:** Medium
**File:** `publication.py` (lines 276-283)

**Problem:** `Publication.get_all_publications()` runs `SELECT id, url, title, year, venue FROM papers` with no LIMIT or pagination. It materializes every row into a `Publication` object in Python memory. This method is not called by the API endpoints but exists as a utility and could be called by future code or CLI tools.

**Performance Impact:** At 10,000 papers, each `Publication` object is ~500 bytes, so ~5 MB. At 100,000 papers: ~50 MB. With `LONGTEXT` fields in the papers table (abstract), the query itself may transfer significant data over the wire.

**Recommendation:** Add pagination or convert to a generator.

```python
@staticmethod
def get_all_publications(batch_size=500):
    """Retrieve all publications in batches to avoid memory spikes."""
    offset = 0
    while True:
        results = Database.fetch_all(
            "SELECT id, url, title, year, venue FROM papers LIMIT %s OFFSET %s",
            (batch_size, offset),
        )
        if not results:
            break
        for row in results:
            yield Publication(id=row[0], url=row[1], title=row[2],
                              year=row[3], venue=row[4], authors=None)
        offset += batch_size
```

---

## 4. Low Severity Findings

### 4.1 SWR Hooks Use Relative URLs -- No Deduplication Across SSR/CSR

**Severity:** Low
**File:** `app/src/lib/api.ts` (lines 59-73)

**Problem:** The SWR hooks use relative URLs as cache keys (e.g., `/api/publications?page=1&per_page=20`). This works because Next.js rewrites these to the internal API URL. However, the `getFields()` function (line 76) uses the full `API_BASE_URL` prefix while SWR hooks don't. This inconsistency means `getFields()` and a hypothetical `useFields()` hook would have different cache keys, preventing SWR deduplication.

Also, SWR's default configuration is used without customization -- meaning no `dedupingInterval`, no `revalidateOnFocus` override, and no `errorRetryCount`. The default `revalidateOnFocus: true` means every browser tab switch triggers a re-fetch of the current page's data.

**Performance Impact:** Extra API calls when users switch browser tabs. Minor bandwidth and server load.

**Recommendation:**

```typescript
// app/src/lib/api.ts -- add SWR global config
import { SWRConfiguration } from "swr";

export const swrConfig: SWRConfiguration = {
  dedupingInterval: 10_000,      // 10s dedup window
  revalidateOnFocus: false,       // don't re-fetch on tab switch
  errorRetryCount: 2,             // limit retries on error
};

// In layout.tsx or a provider:
// <SWRConfig value={swrConfig}>...</SWRConfig>
```

---

### 4.2 No `key` Stability on Date Group Headers

**Severity:** Low
**File:** `app/src/app/NewsfeedContent.tsx` (lines 294-306)

**Problem:** Date group sections use `key={date}` where `date` is a formatted string like "Mar 19, 2026". If the locale changes or dates shift between fetches, React will unmount and remount entire sections instead of diffing them efficiently. Additionally, the `groupByDate` function creates a new `Map` on every render.

**Performance Impact:** Negligible with the current 20-item page size. Becomes measurable if `per_page` increases.

**Recommendation:** Memoize the grouping computation.

```typescript
import { useMemo } from "react";

// Inside NewsfeedContent:
const groups = useMemo(
  () => (data ? groupByDate(data.items) : new Map()),
  [data]
);
```

---

### 4.3 CSS Stagger Animation Applied to All List Items

**Severity:** Low
**File:** `app/src/app/globals.css` (lines 41-52)

**Problem:** The `.animate-stagger` class applies `opacity: 0` initially and uses CSS animation with delays. Items beyond the 7th have a fixed 360ms delay. For a page with 20 items, all 20 are initially invisible (opacity: 0), and items 7-20 all animate at the same time (360ms). This causes a "flash" effect.

More importantly, if JavaScript execution is delayed (large bundle, slow device), items remain invisible (opacity: 0) until the CSS animation runs. There is no `will-change` property, which means the browser may not promote these elements to their own compositing layer.

**Performance Impact:** On slow devices, a brief flash of invisible content. Minor CLS (Cumulative Layout Shift) risk since items are in the DOM at their final positions but invisible.

**Recommendation:** Add `will-change: opacity, transform` for the animated elements, or use `animation-fill-mode: both` (already implicit with `forwards`). Consider reducing the animation to only the first 3-5 items.

---

### 4.4 `backfill_seed_publications` Runs a Full Table Update Without Batching

**Severity:** Low
**File:** `database.py` (lines 389-408)

**Problem:** `UPDATE papers SET is_seed = TRUE WHERE is_seed = FALSE` updates every non-seed paper in a single transaction. For a large table, this creates a long-running transaction that holds row locks and generates a large redo log.

**Performance Impact:** Only runs once (migration backfill), so this is a one-time cost. But for a table with 100,000+ papers, this could lock the table for several seconds, blocking concurrent reads in InnoDB's default `REPEATABLE READ` isolation level.

**Recommendation:** Batch the update if the table is large.

```python
@staticmethod
def backfill_seed_publications() -> int:
    total = 0
    while True:
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE papers SET is_seed = TRUE WHERE is_seed = FALSE LIMIT 1000"
                )
                conn.commit()
                affected = cursor.rowcount
                total += affected
                if affected < 1000:
                    break
    return total
```

---

### 4.5 `seed_research_fields` Runs 12 Individual INSERT Queries

**Severity:** Low
**File:** `database.py` (lines 366-386)

**Problem:** Each field is inserted with a separate `Database.execute_query()` call, meaning 12 pool checkouts, 12 query executions, and 12 commits. This runs on every app startup (called from `create_tables`).

**Performance Impact:** ~12 * 2ms = ~24ms on startup. Negligible individually but adds to startup time alongside table creation.

**Recommendation:** Use a single `INSERT IGNORE ... VALUES (...), (...), ...` or batch within one connection.

```python
@staticmethod
def seed_research_fields():
    fields = [
        ("Macroeconomics", "macroeconomics"),
        # ... rest ...
    ]
    if not fields:
        return
    placeholders = ", ".join(["(%s, %s)"] * len(fields))
    params = [v for pair in fields for v in pair]
    Database.execute_query(
        f"INSERT IGNORE INTO research_fields (name, slug) VALUES {placeholders}",
        params,
    )
```

---

### 4.6 `html_content.content` is LONGTEXT -- Stored in External Pages

**Severity:** Low
**File:** `database.py` (line 153)

**Problem:** The `html_content` table stores extracted text in a `LONGTEXT` column. In InnoDB, `LONGTEXT` values over 768 bytes are stored in external overflow pages, requiring additional I/O to read. The `content` column is read by `HTMLFetcher.get_latest_text()` and `HTMLFetcher.get_previous_text()` during every scrape iteration, but only the first 4000 characters are used (CONTENT_MAX_CHARS).

**Performance Impact:** Minimal since the content is already truncated to 4000 chars before storage. The `LONGTEXT` type allows up to 4 GB, but actual stored values are small. The overhead is the InnoDB external page pointer per row (~20 bytes).

**Recommendation:** Consider changing to `TEXT` (64 KB limit) or `MEDIUMTEXT` (16 MB limit) since content is already truncated to 4000 characters before storage.

---

## 5. Positive Findings (Preserve)

### 5.1 N+1 Query Avoidance is Well-Implemented

**File:** `api.py` (lines 229-247, 513-559, 528-540)

The batch-fetch helper functions (`_get_authors_for_publications`, `_get_urls_for_researchers`, `_get_pub_counts_for_researchers`, `_get_fields_for_researchers`) use `WHERE ... IN (...)` patterns to fetch related data for all items in a single query. This is the correct approach and avoids the classic N+1 query problem. This pattern should be preserved and extended to new features.

### 5.2 Content Change Detection Via Hashing is Efficient

**File:** `html_fetcher.py` (lines 215-229, 278-283)

The `has_text_changed` / `content_hash` pattern avoids unnecessary LLM calls by only processing pages whose content has actually changed. Combined with the `_was_fetched_recently` 24-hour cooldown, this is an effective cost-saving mechanism for the most expensive operation (OpenAI API calls).

### 5.3 Server-Side Pagination is Properly Implemented

**File:** `api.py` (lines 307-431, 618-708)

Both `list_publications` and `list_researchers` use `LIMIT/OFFSET` pagination with a separate `COUNT(*)` query for total count. The `per_page` is capped at 100 via `Query(20, ge=1, le=100)`, preventing clients from requesting unbounded result sets.

### 5.4 Cache-Control Headers on Read Endpoints

**File:** `api.py` (lines 424, 610, 701)

The `Cache-Control: public, max-age=300, stale-while-revalidate=600` headers on list endpoints allow CDN/browser caching, significantly reducing backend load for read-heavy workloads. The 5-minute max-age with 10-minute stale-while-revalidate is a good balance for a newsfeed that updates at most every 24 hours.

### 5.5 Advisory Locks for Scrape Concurrency Control

**File:** `scheduler.py` (lines 24-65)

Using MySQL `GET_LOCK` / `RELEASE_LOCK` advisory locks is the correct approach for preventing concurrent scrapes across multiple API workers or containers. It avoids filesystem-based locks (which don't work across containers) and is more reliable than application-level state.

### 5.6 Next.js Standalone Output for Minimal Docker Image

**File:** `app/next.config.mjs` (line 2), `app/Dockerfile` (lines 18-19)

The `output: "standalone"` configuration produces a minimal production build that includes only the necessary dependencies. The multi-stage Docker build copies only the standalone output, resulting in a small image (~100-150 MB vs. 500+ MB with full node_modules).

---

## 6. Summary Scorecard

| # | Finding | Severity | Est. Impact | Effort |
|---|---------|----------|-------------|--------|
| 1.1 | Quadruple scheduler storm (4 Gunicorn workers) | Critical | Wasted resources, lock contention, undefined shutdown | Medium |
| 1.2 | New OpenAI client per disambiguation call | Critical | 5-15s wasted per scrape on TLS handshakes | Low |
| 1.3 | Race condition in snapshot append | Critical | Duplicate feed events, data corruption | Medium |
| 2.1 | SCRAPE_ON_STARTUP blocks API readiness | High | Minutes of downtime on deploy, K8s restart loops | Low |
| 2.2 | Connection pool too small for multi-worker | High | Connection exhaustion under load | Low |
| 2.3 | Sequential per-publication DB round-trips | High | 1-3s overhead per page during scrape | High |
| 2.4 | robots.txt fetched per-URL not per-domain | High | 75-300s wasted per scrape cycle | Low |
| 2.5 | Researcher list lacks pagination | High | Silent truncation, payload bloat at scale | Medium |
| 3.1 | LIKE '%keyword%' prevents index usage | Medium | Degrades at 10K+ researchers | Medium |
| 3.2 | Missing composite index on feed_events | Medium | Query degradation at 100K+ events | Low |
| 3.3 | Sync handlers in async framework | Medium | 160 max concurrent requests | Low-Medium |
| 3.4 | No application-level caching for filter options | Medium | Redundant DB queries on every page load | Low |
| 3.5 | requests.Session shared across threads | Medium | Intermittent failures in concurrent extraction | Low |
| 3.6 | Unbounded domain rate-limit dict | Medium | Slow memory leak | Low |
| 3.7 | get_all_publications() loads full table | Medium | Memory spike risk for CLI/future code | Low |
| 4.1 | SWR default revalidateOnFocus | Low | Extra API calls on tab switch | Low |
| 4.2 | groupByDate not memoized | Low | Minor re-render overhead | Low |
| 4.3 | Stagger animation flash on slow devices | Low | Brief invisible content | Low |
| 4.4 | Unbatched seed backfill | Low | One-time table lock | Low |
| 4.5 | 12 individual INSERT queries for field seeding | Low | 24ms startup overhead | Low |
| 4.6 | LONGTEXT for 4KB-capped content | Low | Minor InnoDB overhead | Low |

**Priority order for fixes:** 1.1 > 1.2 > 1.3 > 2.1 > 2.4 > 2.2 > 2.5 > 2.3 > 3.2 > 3.4 > 3.5
