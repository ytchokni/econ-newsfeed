# Phase 2: Security & Performance Review

**Date:** 2026-03-20

---

## Security Findings

### Critical (3)

| ID | Finding | File | CVSS |
|----|---------|------|------|
| SEC-C1 | `CONTENT_MAX_CHARS` crashes on missing env var — `int(None)` TypeError | `html_fetcher.py:21` | 8.6 |
| SEC-C2 | `OPENAI_MODEL` has no default — `None` passed to OpenAI API causes validation error on scrape | `publication.py:14` | 8.6 |
| SEC-C3 | DNS rebinding vulnerability in SSRF protection — `validate_url_with_pin()` resolves IP but caller discards it, makes second DNS lookup | `html_fetcher.py:273` | 8.1 |

**SEC-C3 detail:** `validate_url_with_pin()` correctly resolves hostname and checks against private IP ranges, but the resolved IP is discarded (`_resolved_ip`). The actual `fetch_html()` call resolves DNS again, allowing a DNS rebinding attack where the second lookup returns an internal IP (e.g., `169.254.169.254` cloud metadata endpoint).

### High (5)

| ID | Finding | File | CVSS |
|----|---------|------|------|
| SEC-H1 | Next.js 14.2.35 has 4 known CVEs including HTTP request smuggling in rewrites | `app/package.json:13` | 7.5 |
| SEC-H2 | Unbounded IN clause size — `status` and `institution` params accept unlimited comma-separated values enabling resource exhaustion | `api.py:413-414, 448-462` | 7.5 |
| SEC-H3 | LLM prompt injection — researcher page content inserted directly into LLM prompts without sanitization or delimiters | `publication.py:188-204` | 7.3 |
| SEC-H4 | Hidden LLM calls in data access layer — `get_researcher_id()` calls OpenAI for disambiguation with no rate limiting or budget | `database/researchers.py:55-112` | 6.5 |
| SEC-H5 | `glob` dependency command injection (GHSA-5j98-mcp5-4vw2) via `eslint-config-next` | `app/package.json` (transitive) | 7.5 |

### Medium (8)

| ID | Finding | File | CVSS |
|----|---------|------|------|
| SEC-M1 | Health endpoint ignores DB state — returns 200 OK regardless of database connectivity | `api.py:362-365` | 5.3 |
| SEC-M2 | OpenAI client instantiated at import time — causes side effects during testing and requires env vars at import | `publication.py:15` | 5.3 |
| SEC-M3 | Metrics + scrape status endpoints expose operational data without authentication | `api.py:368-382, 889-922` | 5.3 |
| SEC-M4 | CSP allows `unsafe-inline` for scripts in production frontend | `app/next.config.mjs:32` | 5.4 |
| SEC-M5 | Backend CSP header conflicts with frontend CSP — API JSON responses carry restrictive policy | `api.py:190` | 4.3 |
| SEC-M6 | Missing HSTS header on frontend (backend has it, frontend does not) | `app/next.config.mjs` | 4.3 |
| SEC-M7 | Scrape trigger runs in unbounded background thread with no timeout | `api.py:877-879` | 5.3 |
| SEC-M8 | Gunicorn running without `--timeout` or `--max-requests` flags | `Dockerfile.api:19` | 5.3 |

### Low (6)

| ID | Finding | File | CVSS |
|----|---------|------|------|
| SEC-L1 | `.env.example` contains weak placeholder secrets (`secret`, `changeme`) | `.env.example` | 3.7 |
| SEC-L2 | Database connections default to no SSL — unencrypted over network | `db_config.py:25-37` | 3.7 |
| SEC-L3 | Outdated FastAPI 0.115.14 and Gunicorn 22.0.0 | `pyproject.toml` | 3.1 |
| SEC-L4 | CSV import lacks path validation — no directory restriction | `database/researchers.py:131` | 3.3 |
| SEC-L5 | Inconsistent logging — f-strings risk log injection from attacker-controlled URLs | Multiple files | 2.7 |
| SEC-L6 | Batch job temporary file not securely created — `delete=False` with default permissions | `main.py:164` | 2.4 |

### Positive Security Controls (already implemented)
- Parameterized SQL everywhere (no string concatenation)
- HMAC constant-time API key comparison (`hmac.compare_digest`)
- SSRF validation with private IP checks (DNS rebinding aside)
- Security headers on both backend and frontend
- Non-root Docker containers
- `SCRAPE_API_KEY` minimum length enforcement (16 chars)
- Catch-all exception handler preventing stack trace leakage
- LIKE escaping for user-provided filter values
- CORS restricted to configured frontend origin
- Rate limiting via slowapi (60/min or 30/min)
- Advisory locks preventing concurrent scrapes
- robots.txt compliance
- Response size limits (1MB `CONTENT_MAX_BYTES`)
- No `dangerouslySetInnerHTML` in frontend
- `DB_NAME` regex validation preventing SQL injection in DDL

---

## Performance Findings

### Critical (3)

| ID | Finding | Impact |
|----|---------|--------|
| PERF-C1 | Synchronous blocking I/O in async FastAPI — all endpoints are sync `def`, `mysql-connector-python` is sync, default threadpool of 40 threads limits concurrency | At >40 concurrent users, API response times spike to seconds. DB pool of 10 creates chokepoint before threadpool fills |
| PERF-C2 | Connection pool exhaustion in scrape pipeline — advisory lock connections outside pool + 8 concurrent extraction workers holding pool connections = only 2 left for API | During concurrent extraction, API becomes unresponsive. Possible deadlock |
| PERF-C3 | Unbounded memory growth — `_domain_last_request` and `_domain_locks` dicts never cleared across scrape cycles | Slow memory leak in long-running Gunicorn workers |

### High (6)

| ID | Finding | Impact |
|----|---------|--------|
| PERF-H1 | Missing composite index on `feed_events(created_at DESC, paper_id)` — primary feed query forces filesort | 50-200ms with 10k+ events instead of 5ms |
| PERF-H2 | N+1 paper snapshots in scrape pipeline — individual lookup per publication instead of batch | 6000+ queries for 100 URLs × 20 papers, adding 12 seconds of DB round-trip |
| PERF-H3 | Researcher detail endpoint loads ALL publications unbounded — no LIMIT | 200+ publications = 200KB+ response |
| PERF-H4 | Concurrent extraction exhausts pool + OpenAI rate limits — 8 workers with no semaphore | During extraction, API has 0-2 available connections; possible 429 errors |
| PERF-H5 | `get_all_publications()` loads entire papers table — no LIMIT/WHERE | 10k papers = 5-10MB into memory (dead code but callable) |
| PERF-H6 | Frontend researchers page hardcodes `per_page=100` with no pagination | Silent data truncation at 100+ researchers; 100-200KB payload |

### Medium (10)

| ID | Finding | Impact |
|----|---------|--------|
| PERF-M1 | Duplicate COUNT query on every paginated request — separate COUNT(*) + SELECT for same WHERE | Doubles query cost; 20-50ms for complex filters |
| PERF-M2 | `_TOP20_DEPT_KEYWORDS` generates 22 LIKE conditions with leading wildcards | O(researchers × 22) string comparisons per query; forces full table scan |
| PERF-M3 | `save_publications` commits per publication (not batch) — 30 fsync per scrape URL | 150ms commit overhead per URL on SSD; 10x worse on cloud storage |
| PERF-M4 | `requests.Session` shared across threads without thread safety | Possible connection errors under high concurrency |
| PERF-M5 | `_robots_cache` race condition — TOCTOU in concurrent extraction | Duplicate HTTP requests for robots.txt |
| PERF-M6 | No application-level caching for rarely-changing data (fields, filter-options) | 30+ unnecessary DB queries/min for data changing once/day |
| PERF-M7 | HTML content stored as MEDIUMTEXT without compression — off-page InnoDB reads | ~1ms latency per access; oversized column type |
| PERF-M8 | Missing index on `paper_snapshots.content_hash` for change detection | Grows linearly with snapshot count |
| PERF-M9 | Gunicorn 4-worker config with no `--max-requests` — no worker recycling | Workers grow 50-200MB over weeks without recycling |
| PERF-M10 | `CONTENT_MAX_CHARS` no default in html_fetcher.py (same as SEC-C1) | Import-time crash = unavailability |

### Low (7)

| ID | Finding | Impact |
|----|---------|--------|
| PERF-L1 | `_validate_draft_urls` uses sequential requests with 100ms sleep | 100 URLs = 30 seconds; could be 3-5x faster with batching |
| PERF-L2 | LLM disambiguation on every unknown author name — no caching | 600 LLM calls per large scrape = $0.60 and 5 min added latency |
| PERF-L3 | BeautifulSoup parses HTML twice (fetcher + extractor independently) | 2-10 seconds of redundant CPU for 200 URLs |
| PERF-L4 | SWR uses defaults — refetches on every tab focus | 40-60 unnecessary API calls per session |
| PERF-L5 | No connection keep-alive tuning for Next.js rewrites proxy | 20-60ms TCP overhead per page load |
| PERF-L6 | `seed_research_fields` runs 12 INSERT IGNORE on every startup | 24ms unnecessary per startup |
| PERF-L7 | No ETags or conditional requests — full payload every time | Missed 304 opportunities |

### Scalability Bottlenecks

| Barrier | Component | Impact at 10x Scale |
|---------|-----------|---------------------|
| DB pool size (10) vs workers (4 × 10 = 40 connections) | `connection.py` | 8 workers = 80 connections; exceeds MySQL default `max_connections` (151) |
| Per-process caches on HTMLFetcher | `html_fetcher.py` | Wasteful but functional |
| Advisory lock scheduler singleton | `scheduler.py` | Only one worker runs scheduler; correct but fragile |
| top20 LIKE queries (22 conditions) | `api.py` | Untenable at 1000+ researchers |
| Sequential URL processing in scraper | `scheduler.py` | 5-hour scrape at 1000 URLs |

---

## Critical Issues for Phase 3 Context

### Testing requirements driven by security findings
- SEC-C3 (DNS rebinding): Need test verifying pinned IP is actually used for HTTP request
- SEC-H2 (unbounded IN clauses): Need test for parameter length limits
- SEC-H3 (LLM prompt injection): Need test verifying prompt delimiters and system message isolation
- SEC-H4 (hidden LLM in DB layer): Need integration test verifying disambiguation is rate-limited

### Testing requirements driven by performance findings
- PERF-C2 (pool exhaustion): Need load test for concurrent API + scrape
- PERF-H1 (missing index): Need query explain plan test
- PERF-H2 (N+1 snapshots): Need performance benchmark for scrape pipeline
- PERF-M3 (per-publication commit): Need benchmark comparing batch vs per-item commits

### Documentation requirements
- Security posture (what's protected, what's not) should be documented
- Connection pool sizing rationale needs documentation
- Scalability limits and capacity planning estimates should be documented
