# Optimize Memory Usage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce production memory usage by ~400MB through config tuning and code changes, preventing the recurring OOM crashes that make the instance unresponsive.

**Architecture:** Single-box Lightsail instance runs MySQL (1024MB cap) + FastAPI API (384MB cap) + background workers. Config changes reduce baseline usage (pool size, buffer pool, worker count). Code changes eliminate wasteful data loading (speculative raw_html fetch, unbounded caches, missing connection scoping). No architectural changes — same compose files, same deploy process.

**Tech Stack:** Python/FastAPI, MySQL 8.0, Docker Compose, Gunicorn/Uvicorn

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `docker-compose.yml` | Modify | Lower innodb_buffer_pool_size, lower DB_POOL_SIZE default |
| `docker-compose.prod.yml` | Modify | Set WEB_CONCURRENCY=1 |
| `Dockerfile.api` | Modify | Add --max-requests, --max-requests-jitter, --preload to Gunicorn |
| `database/connection.py` | Modify | Lower DB_POOL_SIZE default from 10 to 5 |
| `html_fetcher.py` | Modify | Lazy-load raw_html in get_extraction_payload(); LRU-cap _robots_cache |
| `extraction.py` | Modify | Handle new two-step payload (content-first, raw_html on fallback) |
| `database/researchers.py` | Modify | Add LIMIT to get_urls_needing_extraction() |
| `database/admin.py` | Modify | Add TTL cache to get_admin_dashboard_stats() |
| `api.py` | Modify | Add connection_scope() to researcher detail endpoint |
| `tests/test_html_fetcher.py` | Modify | Update extraction payload tests, add robots LRU tests |
| `tests/test_extraction.py` | Modify | Update payload mock shape (no raw_html in main payload) |
| `tests/test_diff_extraction.py` | Modify | Update payload mock shape |
| `tests/test_admin_dashboard.py` | Modify | Add cache behavior test |

---

### Task 1: Lower DB connection pool size

**Files:**
- Modify: `database/connection.py:17`
- Modify: `docker-compose.yml:36` (api environment)

- [ ] **Step 1: Change the default pool size**

In `database/connection.py`, change line 17:

```python
_DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '5'))
```

- [ ] **Step 2: Run tests to verify nothing breaks**

Run: `poetry run pytest tests/ -x -q`
Expected: All 1119 tests pass (pool size is not exercised in unit tests — they mock DB calls)

- [ ] **Step 3: Commit**

```bash
git add database/connection.py
git commit -m "perf: lower default DB pool size from 10 to 5

Actual concurrency is ~5 (1 extraction + 1 enrichment + 1 scheduler + a few
async API handlers). With 2 Gunicorn workers × 10 connections = 20 pooled
connections, each costing ~10MB server-side on the 1GB-capped MySQL container.
Reducing to 5 saves ~100MB of MySQL memory."
```

---

### Task 2: Lower innodb_buffer_pool_size from 512MB to 384MB

**Files:**
- Modify: `docker-compose.yml:8`

- [ ] **Step 1: Change the buffer pool size**

In `docker-compose.yml` line 8, change `--innodb-buffer-pool-size=512M` to `--innodb-buffer-pool-size=384M`:

```yaml
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci --innodb-buffer-pool-size=384M --log-bin-trust-function-creators=1 --performance-schema=off
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "perf: lower innodb_buffer_pool_size from 512MB to 384MB

Buffer pool at 512MB is 50% of the DB container's 1024MB limit, leaving
minimal headroom for per-connection buffers and temp tables. The working
set easily fits in 384MB; the freed 128MB prevents OOM under connection
spikes."
```

---

### Task 3: Set WEB_CONCURRENCY=1 in production

**Files:**
- Modify: `docker-compose.prod.yml:20`

- [ ] **Step 1: Change the default worker count**

In `docker-compose.prod.yml` line 20, change the default from 2 to 1:

```yaml
      WEB_CONCURRENCY: ${WEB_CONCURRENCY:-1}
```

- [ ] **Step 2: Update the comment at line 7**

Change the comment at line 7 from:
```yaml
# - Sets WEB_CONCURRENCY=2 for Lightsail's 2GB RAM
```
to:
```yaml
# - Sets WEB_CONCURRENCY=1 (single async worker handles the traffic; saves ~150MB)
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.prod.yml
git commit -m "perf: default WEB_CONCURRENCY=1 in production

Only one Gunicorn worker acquires the scheduler advisory lock and runs
background workers. The second worker (~150MB) exists solely for API
requests on a near-zero-traffic site. One async Uvicorn worker handles
both the API and background threads."
```

---

### Task 4: Add Gunicorn --max-requests and --preload

**Files:**
- Modify: `Dockerfile.api:19`

- [ ] **Step 1: Update the CMD**

In `Dockerfile.api` line 19, change:

```dockerfile
CMD ["sh", "-c", "gunicorn api:app --workers ${WEB_CONCURRENCY:-2} --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --log-level info"]
```

to:

```dockerfile
CMD ["sh", "-c", "gunicorn api:app --workers ${WEB_CONCURRENCY:-2} --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --log-level info --max-requests 1000 --max-requests-jitter 100 --preload"]
```

- [ ] **Step 2: Commit**

```bash
git add Dockerfile.api
git commit -m "perf: add Gunicorn --max-requests and --preload

--max-requests 1000: recycle workers after 1000 requests to prevent
memory leak accumulation (Python fragmentation, cached objects).
--preload: load the app before forking so workers share memory via
copy-on-write (saves ~30-50MB with multiple workers)."
```

---

### Task 5: Lazy-load raw_html in get_extraction_payload()

This is the biggest code change. Currently `get_extraction_payload()` always SELECTs `raw_html` (up to 1MB) even though it's only used as a fallback when `content` is NULL (~5% of cases).

**Files:**
- Modify: `html_fetcher.py:738-750`
- Modify: `extraction.py:61-67`
- Modify: `tests/test_html_fetcher.py:456-467`
- Modify: `tests/test_extraction.py:21-38`
- Modify: `tests/test_diff_extraction.py:30-40`

- [ ] **Step 1: Write failing test for the new payload shape**

Add a test in `tests/test_html_fetcher.py` class `TestGetExtractionPayload` that verifies `raw_html` is NOT in the main query:

```python
def test_main_query_excludes_raw_html(self):
    row = {"content": "text", "content_hash": "h1",
           "timestamp": None, "extracted_at": None}
    with patch("html_fetcher.fetch_one", return_value=row) as mock_fetch:
        result = HTMLFetcher.get_extraction_payload(7)
    sql = mock_fetch.call_args[0][0]
    assert "raw_html" not in sql
    assert result == row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_html_fetcher.py::TestGetExtractionPayload::test_main_query_excludes_raw_html -v`
Expected: FAIL (current query includes raw_html)

- [ ] **Step 3: Write failing test for the raw_html fallback method**

Add a new test in `tests/test_html_fetcher.py`:

```python
class TestGetRawHtml:
    def test_returns_raw_html(self):
        with patch("html_fetcher.fetch_one", return_value={"raw_html": "<html>body</html>"}):
            assert HTMLFetcher.get_raw_html(7) == "<html>body</html>"

    def test_returns_none_when_no_row(self):
        with patch("html_fetcher.fetch_one", return_value=None):
            assert HTMLFetcher.get_raw_html(7) is None

    def test_returns_none_when_null_raw_html(self):
        with patch("html_fetcher.fetch_one", return_value={"raw_html": None}):
            assert HTMLFetcher.get_raw_html(7) is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `poetry run pytest tests/test_html_fetcher.py::TestGetRawHtml -v`
Expected: FAIL (get_raw_html does not exist)

- [ ] **Step 5: Implement the changes in html_fetcher.py**

Replace `get_extraction_payload()` (lines 738-750) with:

```python
@staticmethod
def get_extraction_payload(url_id: int) -> dict | None:
    """Read content, content_hash, timestamp, and extracted_at in one query.

    raw_html is excluded — use get_raw_html() as a fallback when content is NULL.
    """
    return fetch_one(
        "SELECT content, content_hash, timestamp, extracted_at"
        " FROM html_content WHERE url_id = %s",
        (url_id,),
    )

@staticmethod
def get_raw_html(url_id: int) -> str | None:
    """Fetch raw_html for a URL. Used as fallback when content is NULL."""
    row = fetch_one(
        "SELECT raw_html FROM html_content WHERE url_id = %s",
        (url_id,),
    )
    return row['raw_html'] if row and row['raw_html'] else None
```

- [ ] **Step 6: Update extraction.py to use the new two-step approach**

In `extraction.py`, replace lines 61-67:

```python
payload = HTMLFetcher.get_extraction_payload(url_id)
if not payload:
    return ExtractionOutcome("no_content")

text = payload['content']
if not text and payload['raw_html']:
    text = HTMLFetcher.extract_text_content(payload['raw_html'])
```

with:

```python
payload = HTMLFetcher.get_extraction_payload(url_id)
if not payload:
    return ExtractionOutcome("no_content")

text = payload['content']
if not text:
    raw_html = HTMLFetcher.get_raw_html(url_id)
    if raw_html:
        text = HTMLFetcher.extract_text_content(raw_html)
```

- [ ] **Step 7: Update existing extraction payload test**

In `tests/test_html_fetcher.py`, update `TestGetExtractionPayload::test_returns_row`:

```python
def test_returns_row(self):
    row = {"content": "text", "content_hash": "h1",
           "timestamp": None, "extracted_at": None}
    with patch("html_fetcher.fetch_one", return_value=row) as mock_fetch:
        result = HTMLFetcher.get_extraction_payload(7)
    assert result == row
    assert mock_fetch.call_args[0][1] == (7,)
```

- [ ] **Step 8: Update extraction test mocks**

In `tests/test_extraction.py`, update the `_patches` function payload (line 24-27):

```python
if payload is None:
    payload = {
        "content": "page text", "content_hash": "h1",
        "timestamp": None, "extracted_at": None if is_seed else "2026-01-01",
    }
```

Add a `get_raw_html` mock to the patches dict (after the `payload` entry):

```python
"get_raw_html": patch("extraction.HTMLFetcher.get_raw_html", return_value=None),
```

In `tests/test_extraction.py`, update `test_fallback_to_raw_html_when_content_is_none` (around line 104) — update the payload to not include `raw_html`:

```python
payload = {"content": None, "content_hash": "h2",
           "timestamp": None, "extracted_at": "2026-01-01"}
```

And patch `get_raw_html` to return the HTML:

```python
patches["get_raw_html"] = patch("extraction.HTMLFetcher.get_raw_html",
                                return_value="<html>x</html>")
```

- [ ] **Step 9: Update diff extraction test mocks**

In `tests/test_diff_extraction.py`, find the payload dict (around line 33) and remove `raw_html`:

```python
"content": content, "content_hash": "h1",
"timestamp": None, "extracted_at": "2026-01-01",
```

Do the same for any other payload dicts in that file that include `raw_html`.

- [ ] **Step 10: Run all tests**

Run: `poetry run pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 11: Commit**

```bash
git add html_fetcher.py extraction.py tests/test_html_fetcher.py tests/test_extraction.py tests/test_diff_extraction.py
git commit -m "perf: lazy-load raw_html in extraction payload

get_extraction_payload() no longer SELECTs raw_html (up to 1MB per row).
New get_raw_html() is called only when content is NULL (~5% of cases).
Saves ~1MB peak memory per extraction cycle for the 95% common path."
```

---

### Task 6: Add connection_scope() to researcher detail endpoint

**Files:**
- Modify: `api.py:847-907`

- [ ] **Step 1: Wrap the queries in connection_scope()**

In `api.py`, replace the researcher detail endpoint body (lines 854-907). Add the import if not already present — check for `from database.connection import connection_scope` at the top of the file. Then wrap:

```python
@app.get("/api/researchers/{researcher_id}")
@limiter.limit("60/minute")
def get_researcher(
    request: Request,
    researcher_id: int,
    include_history: bool = Query(False),
):
    with connection_scope():
        row = get_researcher_detail(researcher_id)
        if not row:
            raise HTTPException(status_code=404, detail="Researcher not found")

        urls_map = get_urls_for_researchers([researcher_id])
        urls = urls_map.get(researcher_id, [])
        pub_counts = get_pub_counts_for_researchers([researcher_id])
        pub_count = pub_counts.get(researcher_id, 0)
        fields_map = get_fields_for_researchers([researcher_id])
        fields = fields_map.get(researcher_id, [])
        jel_codes = get_jel_codes_for_researcher(researcher_id)

        pub_rows = get_researcher_papers(researcher_id)
        pub_ids = [pr['id'] for pr in pub_rows]
        authors_by_pub = get_authors_for_papers(pub_ids)
        coauthors_by_pub = get_coauthors_for_papers(pub_ids)
        links_by_pub = get_links_for_papers(pub_ids)

    publications = [
        _format_publication(pr, authors_by_pub.get(pr['id'], []),
                           coauthors_by_pub.get(pr['id'], []),
                           links_by_pub.get(pr['id'], []))
        for pr in pub_rows
    ]

    result = {
        "id": row['id'],
        "first_name": row['first_name'],
        "last_name": row['last_name'],
        "position": row['position'],
        "affiliation": row['affiliation'],
        "description": row['description'],
        "urls": urls,
        "website_url": _get_website_url(urls),
        "publication_count": pub_count,
        "fields": fields,
        "jel_codes": jel_codes,
        "publications": publications,
    }

    if include_history:
        snapshots = get_researcher_snapshots(researcher_id)
        result["history"] = [
            {
                "position": s['position'],
                "affiliation": s['affiliation'],
                "description": s['description'],
                "scraped_at": _iso_z(s['scraped_at']),
                "source_url": s['source_url'],
            }
            for s in snapshots
        ]

    return result
```

- [ ] **Step 2: Check the import exists**

Verify `connection_scope` is imported in `api.py`. Look for the import line. If missing, add:

```python
from database.connection import connection_scope
```

- [ ] **Step 3: Run tests**

Run: `poetry run pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add api.py
git commit -m "perf: wrap researcher detail endpoint in connection_scope()

The /api/researchers/{id} endpoint makes 8-10 queries, each checking out
and returning a separate pool connection. connection_scope() reuses a
single connection for the entire request, reducing pool churn."
```

---

### Task 7: Add LIMIT to get_urls_needing_extraction()

**Files:**
- Modify: `database/researchers.py:406-422`

- [ ] **Step 1: Add LIMIT to the query**

In `database/researchers.py`, update `get_urls_needing_extraction()` — add `LIMIT 200` to the query (line 420, before the closing `"""`):

```python
def get_urls_needing_extraction() -> list[dict]:
    """Active researcher URLs whose stored HTML changed since last extraction.

    A URL needs extraction when its content_hash differs from extracted_hash
    (changed since last LLM run, or never extracted). URLs with no stored
    HTML are excluded (nothing to extract).

    Returns at most 200 rows — the worker processes them one at a time and
    re-polls when the batch is done.
    """
    query = """
        SELECT ru.id, ru.researcher_id, ru.url, ru.page_type
        FROM researcher_urls ru
        JOIN html_content hc ON hc.url_id = ru.id
        WHERE ru.is_active = TRUE
          AND hc.content_hash IS NOT NULL
          AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)
        ORDER BY ru.id
        LIMIT 200
    """
    return fetch_all(query)
```

- [ ] **Step 2: Run tests**

Run: `poetry run pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add database/researchers.py
git commit -m "perf: limit extraction queue query to 200 rows

The worker processes URLs one at a time and re-polls when done.
Loading the entire backlog (potentially thousands of rows) into a
Python list every 5 minutes wastes memory. LIMIT 200 caps the
allocation while keeping the worker fed."
```

---

### Task 8: Cap _robots_cache with LRU eviction

**Files:**
- Modify: `html_fetcher.py:153-178`
- Modify: `tests/test_html_fetcher.py` (update .clear() calls, add LRU test)

- [ ] **Step 1: Write failing test for LRU eviction**

Add a test in `tests/test_html_fetcher.py` class `TestRobotsTxtCaching`:

```python
def test_robots_cache_evicts_oldest_entry(self):
    """Cache should not grow beyond _ROBOTS_CACHE_MAX entries."""
    from html_fetcher import _ROBOTS_CACHE_MAX
    with patch("html_fetcher.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "User-agent: *\nAllow: /"
        mock_get.return_value = mock_resp

        HTMLFetcher._robots_cache.clear()
        for i in range(_ROBOTS_CACHE_MAX + 10):
            HTMLFetcher.is_allowed_by_robots(f"https://domain{i}.com/page")

    assert len(HTMLFetcher._robots_cache) <= _ROBOTS_CACHE_MAX
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_html_fetcher.py::TestRobotsTxtCaching::test_robots_cache_evicts_oldest_entry -v`
Expected: FAIL (_ROBOTS_CACHE_MAX does not exist, and cache grows unbounded)

- [ ] **Step 3: Implement LRU-capped cache**

In `html_fetcher.py`, add a constant near the top (after the existing constants):

```python
_ROBOTS_CACHE_MAX = 500
```

Replace the `_robots_cache` class attribute (line 155) and update `_get_robots_parser` (lines 157-178):

```python
_robots_cache: OrderedDict = OrderedDict()
```

Add `from collections import OrderedDict` to the imports at the top of the file.

Update `_get_robots_parser` to enforce the cap:

```python
@staticmethod
def _get_robots_parser(url: str) -> "RobotFileParser | None":
    """Get or fetch the RobotFileParser for a URL's domain. Cached per origin with LRU eviction."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in HTMLFetcher._robots_cache:
        HTMLFetcher._robots_cache.move_to_end(origin)
        return HTMLFetcher._robots_cache[origin]
    try:
        robots_url = f"{origin}/robots.txt"
        resp = requests.get(robots_url, timeout=10, headers={
            'User-Agent': SCRAPER_USER_AGENT,
        })
        if resp.status_code != 200:
            HTMLFetcher._robots_cache[origin] = None
            if len(HTMLFetcher._robots_cache) > _ROBOTS_CACHE_MAX:
                HTMLFetcher._robots_cache.popitem(last=False)
            return None
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        HTMLFetcher._robots_cache[origin] = rp
        if len(HTMLFetcher._robots_cache) > _ROBOTS_CACHE_MAX:
            HTMLFetcher._robots_cache.popitem(last=False)
        return rp
    except Exception:
        HTMLFetcher._robots_cache[origin] = None
        if len(HTMLFetcher._robots_cache) > _ROBOTS_CACHE_MAX:
            HTMLFetcher._robots_cache.popitem(last=False)
        return None
```

- [ ] **Step 4: Run all tests**

Run: `poetry run pytest tests/ -x -q`
Expected: All tests pass. Existing tests use `.clear()` which works on OrderedDict.

- [ ] **Step 5: Commit**

```bash
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "perf: cap robots.txt cache at 500 entries with LRU eviction

The _robots_cache dict grew unbounded during continuous extraction worker
runs (never cleared between scrape jobs). Replace with an OrderedDict
that evicts the oldest entry beyond 500. Each RobotFileParser is ~1-5KB,
so this caps the cache at ~2.5MB."
```

---

### Task 9: Cache admin dashboard stats with 5-minute TTL

**Files:**
- Modify: `api.py:474-478`
- Modify: `tests/test_admin_dashboard.py`

- [ ] **Step 1: Write failing test for cache behavior**

Add a test in `tests/test_admin_dashboard.py`:

```python
def test_admin_dashboard_caches_result(client):
    """Repeated dashboard requests should hit cache, not re-query."""
    mock_stats = {
        "health": {}, "content": {}, "quality": {},
        "costs": {}, "scrapes": {}, "activity": {},
        "extraction": {},
    }
    with patch("api.get_admin_dashboard_stats", return_value=mock_stats) as mock_fn:
        headers = {"X-API-Key": os.environ.get("SCRAPE_API_KEY", "test-key")}
        client.get("/api/admin/dashboard", headers=headers)
        client.get("/api/admin/dashboard", headers=headers)
    assert mock_fn.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_admin_dashboard.py::test_admin_dashboard_caches_result -v`
Expected: FAIL (call_count == 2, no caching)

- [ ] **Step 3: Implement the cache**

In `api.py`, add a cache instance near the existing caches (around line 92):

```python
_admin_dashboard_cache = _TTLCache(300)  # 5 minutes
```

Update the admin dashboard endpoint (line 474-478):

```python
@app.get("/api/admin/dashboard")
def admin_dashboard(request: Request):
    """Admin dashboard metrics — all stats in one response."""
    _require_api_key(request)
    return _admin_dashboard_cache.get_or_set(get_admin_dashboard_stats)
```

- [ ] **Step 4: Run all tests**

Run: `poetry run pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_admin_dashboard.py
git commit -m "perf: cache admin dashboard stats for 5 minutes

get_admin_dashboard_stats() fires 12+ queries across multiple tables,
several with GROUP BY full table scans. On a memory-constrained instance,
this burst of concurrent allocations is wasteful for a rarely-viewed
dashboard. 5-minute TTL matches the existing _filter_options_cache pattern."
```

---

### Task 10: Run full test suite and verify

- [ ] **Step 1: Run all Python tests**

Run: `poetry run pytest tests/ -x -q`
Expected: All 1119+ tests pass

- [ ] **Step 2: Run TypeScript checks (if applicable)**

Run: `cd app && npx tsc --noEmit 2>&1 | tail -5`
Expected: No errors (these changes don't touch frontend code, but verify no accidental breakage)

- [ ] **Step 3: Final commit — update CLAUDE.md if needed**

If no CLAUDE.md changes needed, skip. Otherwise update the deployment section to note the new defaults.

---

## Summary of Expected Savings

| Change | Memory Saved | Container |
|--------|-------------|-----------|
| DB_POOL_SIZE 10→5 | ~100MB | MySQL |
| innodb_buffer_pool_size 512→384MB | ~128MB | MySQL |
| WEB_CONCURRENCY 2→1 | ~150MB | API |
| --max-requests (leak prevention) | Variable | API |
| Lazy raw_html loading | ~1MB peak/cycle | API |
| Robots LRU cap | Prevents unbounded growth | API |
| connection_scope on researcher detail | Reduces pool churn | API + MySQL |
| Admin dashboard cache | Eliminates 12-query bursts | API + MySQL |
| Extraction queue LIMIT | ~1-2MB per poll | API |

**Total estimated savings: ~380MB baseline + leak/spike prevention**
