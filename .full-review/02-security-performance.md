# Phase 2: Security & Performance Review

## Security Findings

### Critical (2)

| # | Finding | CVSS | CWE | Location | Description |
|---|---------|------|-----|----------|-------------|
| SEC-01 | SQL injection in database creation | 9.8 | CWE-89 | `database.py:24` | F-string interpolation of `DB_NAME` env var into DDL; attacker controlling env can inject arbitrary SQL |
| SEC-02 | Untrusted LLM output injected into DB | 9.1 | CWE-20, CWE-74 | `publication.py:20-62,75-107` | OpenAI response parsed and inserted without schema validation; poisoned web content can manipulate LLM output (prompt injection via scraped HTML) |

### High (5)

| # | Finding | CVSS | CWE | Location | Description |
|---|---------|------|-----|----------|-------------|
| SEC-03 | No DB config validation — null dereference | 7.5 | CWE-252, CWE-476 | `db_config.py:7-12` | All config values may be `None`; creates DB named "None" or connects to unintended host |
| SEC-04 | OpenAI API key unvalidated — silent failure | 7.4 | CWE-287 | `publication.py:90` | `None` key causes opaque errors; key potentially exposed in error logs |
| SEC-05 | Arbitrary file read via user-controlled path | 7.5 | CWE-22 | `main.py:11-12`, `database.py:190-208` | `input()` path passed directly to `open()` without restriction; could read `/etc/passwd`, `/proc/self/environ` |
| SEC-06 | SSRF via database-stored URLs | 8.1 | CWE-918 | `html_fetcher.py:25` | URLs fetched from DB without scheme/host validation; could target AWS metadata (169.254.169.254), internal services |
| SEC-07 | Broad exception handling masks security errors | 6.5 | CWE-754, CWE-390 | `database.py:207`, `publication.py:57,105` | SQL injection attempts, auth failures, data corruption all logged as generic errors |

### Medium (7)

| # | Finding | CVSS | CWE | Location | Description |
|---|---------|------|-----|----------|-------------|
| SEC-08 | Unpinned Python dependencies — supply chain risk | 6.8 | CWE-829 | `requirements.txt` | Most deps unpinned; duplicate `python-dotenv`; no hash verification |
| SEC-09 | Known CVEs in Next.js 14.2.13 | 6.5-9.1 | CWE-829 | `app/package.json:13` | CVE-2024-46982 (cache poisoning), CVE-2024-51479 (auth bypass), CVE-2025-29927 (middleware bypass) |
| SEC-10 | No HTTPS enforcement for web scraping | 5.9 | CWE-319, CWE-295 | `html_fetcher.py:12-15,25` | HTTP URLs allow MITM content injection; poisoned content then sent to LLM |
| SEC-11 | Sensitive data in log messages | 5.3 | CWE-532 | Multiple files | MySQL errors may contain connection strings; OpenAI errors may include API key |
| SEC-12 | No rate limiting on outbound requests | 5.3 | CWE-799 | `html_fetcher.py:18-35`, `main.py:17-20` | No per-domain throttling; scraper could be abused as DDoS amplifier; no `robots.txt` compliance |
| SEC-13 | LLM response dumped to filesystem unsanitized | 5.0 | CWE-73, CWE-552 | `publication.py:128-139` | No size limit; world-readable permissions; no rotation; disk exhaustion risk |
| SEC-14 | Planned API has no authentication | 6.5 | CWE-306 | `DESIGN.md` 4.3 | `POST /api/scrape` triggers costly operations (OpenAI calls, outbound HTTP) without auth |

### Low (5)

| # | Finding | CVSS | CWE | Location | Description |
|---|---------|------|-----|----------|-------------|
| SEC-15 | Planned API missing security headers & CORS config | 5.3 | CWE-693, CWE-942 | `DESIGN.md` | No CSP, HSTS, X-Frame-Options; CORS config not specified |
| SEC-16 | DB credentials in Docker Compose as root user | 4.0 | CWE-798, CWE-522 | `DESIGN.md` 8.1, 9.1 | App uses MySQL root; all env vars shared to all containers |
| SEC-17 | Connection per query — resource exhaustion | 3.7 | CWE-400 | `database.py:34-43` | Under API load, `max_connections` exhausted → DoS |
| SEC-18 | `@ts-morph/common` unnecessary dependency | 3.1 | CWE-1104 | `app/package.json:12` | Increases attack surface; not needed for Next.js |
| SEC-19 | LONGTEXT stored without size limits | 3.7 | CWE-400, CWE-770 | `html_fetcher.py:55-69`, `database.py:129` | Malicious page could store GBs of content |
| SEC-20 | No TLS for database connections | 4.3 | CWE-319 | `db_config.py:7-12` | Credentials in cleartext on wire; critical for AWS RDS |

---

## Performance Findings

### Critical (3)

| # | Finding | Impact | Location | Description |
|---|---------|--------|----------|-------------|
| PERF-01 | No connection pooling — new TCP per query | 3-15ms overhead per query; ~250 unnecessary handshakes per scrape cycle (50 URLs) | `database.py:34-43` | Every `execute_query/fetch_all/fetch_one` opens and closes a fresh MySQL connection |
| PERF-02 | Mixed connection management in save_publications | 150-300 extra connections per cycle; broken transaction integrity | `publication.py:20-62` | `get_researcher_id()` opens separate connections inside an outer transaction |
| PERF-03 | Fully sequential URL processing | Total scrape time = sum of all fetch times; 25-250s for 50 URLs | `main.py:15-20,22-38` | No concurrency; I/O-bound work done serially |

### High (5)

| # | Finding | Impact | Location | Description |
|---|---------|--------|----------|-------------|
| PERF-04 | No secondary database indexes | Full table scans on all lookup queries; O(N) degradation | `database.py:91-147` | Missing indexes on `html_content(url_id,timestamp)`, `researchers(first_name,last_name)`, `publications(timestamp)` |
| PERF-05 | No duplicate publication detection | Linear table growth per scrape cycle | `publication.py:20-62` | Re-extraction inserts duplicates; no unique constraints |
| PERF-06 | 4000-char content truncation | 50%+ of publications silently dropped on long pages | `publication.py:86` | Hard truncation loses data beyond cutoff |
| PERF-07 | Unbounded html_content table growth | Disk and query degradation over months | `html_fetcher.py:55-69` | Full LONGTEXT stored for every content change; ~180MB/year at 50 researchers |
| PERF-08 | Thread-unsafe requests.Session (if concurrency added) | Request corruption under concurrent use | `html_fetcher.py:12-15` | Class-level `Session` shared across threads |

### Medium (10)

| # | Finding | Impact | Location | Description |
|---|---------|--------|----------|-------------|
| PERF-09 | Double BeautifulSoup parse per URL | 2x CPU waste for HTML processing | `html_fetcher.py:72-105` | `has_text_changed()` and `save_text()` each parse the same HTML independently |
| PERF-10 | OpenAI client recreated per call | 50-100ms TLS handshake overhead per extraction | `publication.py:90` | Connection pool discarded each time |
| PERF-11 | No researcher ID caching during scrape | Dozens of redundant DB lookups per cycle | `database.py:154-175` | Same author looked up repeatedly across publications |
| PERF-12 | No HTTP conditional requests (If-Modified-Since/ETag) | Full page download even when unchanged | `html_fetcher.py:18-35` | Wastes bandwidth when servers support caching |
| PERF-13 | No rate limiting on outbound requests | Risk of IP bans; OpenAI 429 errors | `html_fetcher.py`, `publication.py` | No per-domain throttling; no backoff |
| PERF-14 | Only timeout errors retried — 5xx treated as permanent | Transient server errors cause data loss | `html_fetcher.py:29-33` | `HTTPError` for 5xx hits `break` instead of retry |
| PERF-15 | Race condition in researcher upsert | Duplicate rows under concurrent writes | `database.py:154-175` | Check-then-insert without locking; no unique constraint |
| PERF-16 | In-process scheduler shares resources with API | API latency spikes during scrapes | `DESIGN.md` 10.2 | BeautifulSoup parsing and OpenAI deserialization can block GIL |
| PERF-17 | No client-side caching strategy in planned frontend | Redundant API refetches on navigation | `DESIGN.md` 5.3 | No SWR/React Query mentioned |
| PERF-18 | `get_all_publications` fetches entire table unpaginated | Memory proportional to total publications | `publication.py:152-159` | No LIMIT, no pagination, no filtering |

### Low (3)

| # | Finding | Impact | Location | Description |
|---|---------|--------|----------|-------------|
| PERF-19 | Single MySQL instance, no read replicas | API reads and scraper writes contend on same instance | Architecture | Fine for MVP; post-MVP concern |
| PERF-20 | Unbounded invalid_json_dumps directory | Disk accumulation over time | `publication.py:128-139` | No rotation or cleanup mechanism |
| PERF-21 | Spurious `@ts-morph/common` dependency | Minor image size increase | `app/package.json` | Not needed for Next.js frontend |

---

## Critical Issues for Phase 3 Context

The following findings from Phase 2 affect testing and documentation requirements:

### Testing Implications
1. **SQL injection (SEC-01)** — Need input validation tests for database name sanitization
2. **LLM output validation (SEC-02)** — Need schema validation tests for all possible LLM response shapes (malformed JSON, missing keys, wrong types, extra large arrays)
3. **SSRF (SEC-06)** — Need URL validation tests covering internal IPs, metadata endpoints, non-HTTP schemes
4. **Path traversal (SEC-05)** — Need file path validation tests with traversal attempts
5. **Connection pooling (PERF-01)** — Need load tests to verify pool exhaustion behavior
6. **Duplicate detection (PERF-05)** — Need idempotency tests for repeated extraction
7. **Race conditions (PERF-15)** — Need concurrent insertion tests for researcher upsert

### Documentation Implications
1. **Security controls (SEC-14, SEC-15)** — API authentication and CORS configuration must be documented in DESIGN.md
2. **Rate limiting (SEC-12)** — Per-domain limits and `robots.txt` policy need documentation
3. **Environment variables (SEC-03, SEC-04)** — All required env vars with validation rules need a `.env.example`
4. **Dependency management (SEC-08)** — Pinning strategy and update policy need documentation
5. **Data retention (PERF-07)** — `html_content` retention policy needs documentation
6. **Scalability limits (PERF-03, PERF-19)** — Current capacity and scaling path need documentation
