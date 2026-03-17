# Security Audit Report -- econ-newsfeed

**Audit Date:** 2026-03-17
**Auditor:** Claude Opus 4.6 (Automated Security Audit)
**Scope:** Full-stack application -- Python FastAPI backend, Next.js/TypeScript frontend, Docker infrastructure, CI/CD pipelines
**Classification:** CONFIDENTIAL

---

## Executive Summary

This audit identified **26 security findings** across the econ-newsfeed project, including **2 Critical**, **6 High**, **10 Medium**, and **8 Low** severity issues. The most urgent finding is a live OpenAI API key committed to the `.env` file on disk (which, while not tracked by git, is present in the working directory and could be inadvertently exposed). Additional critical concerns include SQL identifier injection in the database creation layer and timing-attack-vulnerable API key authentication.

The application has a reasonable security baseline -- parameterized SQL queries for data operations, SSRF protections on the scraper, CORS restricted to the frontend origin, security headers middleware, and non-root Docker users. However, several systemic weaknesses require immediate remediation.

### Findings Summary

| Severity | Count | Key Areas |
|----------|-------|-----------|
| Critical | 2 | Exposed API key, SQL identifier injection |
| High | 6 | Timing attack on auth, TOCTOU race, weak defaults, missing `.dockerignore`, scrape lock race, thread-unsafe session |
| Medium | 10 | SSRF DNS rebinding, error information leakage, missing rate limiting, no auth on read endpoints, missing CSP on frontend, stale dependencies, no HTTPS enforcement, logging sensitive data, deprecated datetime API, insufficient security tests |
| Low | 8 | Overly permissive CORS methods, missing Referrer-Policy, hardcoded User-Agent, no request size limits, no pagination on researchers, db_config sys.exit, invalid JSON dump to disk, no SBOM/dependency pinning |

---

## Critical Findings

### C-1: Live OpenAI API Key in `.env` File on Disk

**Severity:** Critical (CVSS 9.1)
**CWE:** CWE-798 (Use of Hard-Coded Credentials), CWE-540 (Inclusion of Sensitive Information in Source Code)
**Location:** `/Users/yogamtchokni/Projects/econ-newsfeed/.env`, line 12

**Description:**
The `.env` file contains what appears to be a live OpenAI API key:

```
OPENAI_API_KEY=sk-proj-[REDACTED]
```

While `.env` is in `.gitignore` and is not tracked by git (confirmed), the key is on disk in plaintext. The `Dockerfile.api` uses `COPY . .` and the root-level `.dockerignore` does list `.env` -- however, any CI/CD build misconfiguration, backup, or filesystem exposure could leak this key.

**Attack Scenario:**
- If the Docker build context ever changes or `.dockerignore` is bypassed, the key gets baked into the Docker image layer
- Any developer with filesystem access can read this key
- Clipboard/screen capture/log exposure during development

**Remediation:**
1. **Immediately rotate this OpenAI API key** at https://platform.openai.com/api-keys -- treat it as compromised since it appears in this audit output
2. Use a secrets manager (e.g., `aws secretsmanager`, `vault`, Docker secrets) rather than `.env` files for production
3. For development, use a dedicated low-privilege API key with spend limits
4. Add a pre-commit hook to detect secrets (e.g., `detect-secrets`, `gitleaks`, `trufflehog`)

---

### C-2: SQL Identifier Injection in `create_database()` and Makefile `reset-db`

**Severity:** Critical (CVSS 9.8)
**CWE:** CWE-89 (SQL Injection)
**Location:** `database.py`, line 24; `Makefile`, line 21

**Description:**
The `create_database()` method constructs DDL using f-string interpolation of the database name from the environment:

```python
# database.py:24
cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_config['database']}")
```

The Makefile `reset-db` target does the same:

```makefile
# Makefile:21
cursor.execute('DROP DATABASE IF EXISTS ' + db_config['database']); \
```

The `db_config['database']` value comes from `DB_NAME` environment variable (`db_config.py:23`). SQL identifiers cannot be parameterized with `%s` in MySQL connector, but they also must not be blindly interpolated.

**Attack Scenario:**
If an attacker can control the `DB_NAME` environment variable (e.g., via container injection, CI/CD environment manipulation, or `.env` file tampering), they can execute arbitrary SQL:

```
DB_NAME="econ_newsfeed; DROP DATABASE production_db; --"
```

This would result in:
```sql
CREATE DATABASE IF NOT EXISTS econ_newsfeed; DROP DATABASE production_db; --
```

**Remediation:**
Validate the database name against a strict allowlist pattern before use:

```python
import re

def _validate_identifier(name: str) -> str:
    """Validate a SQL identifier (database/table name)."""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name

# In create_database():
db_name = _validate_identifier(db_config['database'])
cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
```

Also apply backtick quoting as a defense-in-depth measure. Apply the same fix to the Makefile's `reset-db` target.

---

## High Findings

### H-1: API Key Authentication Vulnerable to Timing Attack

**Severity:** High (CVSS 7.5)
**CWE:** CWE-208 (Observable Timing Discrepancy)
**Location:** `api.py`, line 343

**Description:**
The API key comparison uses Python's standard `!=` operator:

```python
if not api_key or api_key != scrape_api_key:
    raise HTTPException(status_code=401, detail="Missing or invalid API key")
```

Standard string comparison short-circuits on the first differing byte, leaking information about how many characters matched. An attacker can progressively brute-force the API key character-by-character by measuring response times.

**Attack Scenario:**
An attacker sends thousands of requests with incrementally guessed API key prefixes, measuring response latency. Correct prefix characters will take marginally longer to compare. With sufficient statistical samples, each character can be identified in sequence. A 32-character key that would normally require 62^32 guesses can be reduced to 62*32 = 1,984 guesses.

**Remediation:**
Use `hmac.compare_digest()` for constant-time comparison:

```python
import hmac

scrape_api_key = os.environ.get("SCRAPE_API_KEY", "")

if not api_key or not hmac.compare_digest(api_key, scrape_api_key):
    raise HTTPException(status_code=401, detail="Missing or invalid API key")
```

Also ensure `SCRAPE_API_KEY` defaults to an empty string (not `None`) to avoid type errors in `compare_digest`.

---

### H-2: TOCTOU Race Condition on Scrape Lock

**Severity:** High (CVSS 6.5)
**CWE:** CWE-367 (Time-of-Check Time-of-Use)
**Location:** `api.py`, lines 347-353

**Description:**
The `/api/scrape` endpoint checks the lock, immediately releases it, then spawns a background thread that re-acquires it:

```python
if not scheduler._scrape_lock.acquire(blocking=False):
    raise HTTPException(status_code=409, ...)
# Release immediately -- run_scrape_job will re-acquire
scheduler._scrape_lock.release()

# ...later in the thread:
t = threading.Thread(target=run_scrape_job, daemon=True)
t.start()
```

Between the `release()` and the thread's `acquire()` in `run_scrape_job()`, a second request can also pass the check, leading to two concurrent scrape jobs.

**Attack Scenario:**
Two near-simultaneous `POST /api/scrape` requests both pass the lock check, both release, and both spawn threads. This could cause duplicate publications, excessive external requests, and OpenAI API cost overruns.

**Remediation:**
Pass the already-acquired lock to the thread instead of releasing and re-acquiring:

```python
@app.post("/api/scrape", status_code=201)
async def trigger_scrape(request: Request):
    # ... auth check ...

    if not scheduler._scrape_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, ...)

    log_id = create_scrape_log()
    started_at = datetime.now(timezone.utc)

    def _run_with_lock():
        try:
            run_scrape_job_unlocked()  # version that doesn't try to acquire
        finally:
            scheduler._scrape_lock.release()

    t = threading.Thread(target=_run_with_lock, daemon=True)
    t.start()
    # ...
```

---

### H-3: Weak Default Passwords in Docker Compose

**Severity:** High (CVSS 7.3)
**CWE:** CWE-1393 (Use of Default Password)
**Location:** `docker-compose.yml`, lines 5-8, 33

**Description:**
Docker Compose provides weak fallback defaults for all credentials:

```yaml
MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:-rootsecret}
MYSQL_PASSWORD: ${DB_PASSWORD:-secret}
SCRAPE_API_KEY: ${SCRAPE_API_KEY:-changeme}
```

Developers who simply run `docker compose up` without configuring `.env` will get these defaults. In production, if environment variables are not set, these defaults will be active.

**Attack Scenario:**
If the MySQL port (3306) is exposed to the network (it is in `docker-compose.yml` line 9: `ports: - "3306:3306"`), an attacker can connect with `root`/`rootsecret` or `econ_app`/`secret` and gain full database access. The scrape API key `changeme` allows unauthenticated triggering of scrape jobs.

**Remediation:**
1. Remove all default passwords -- use variable substitution without defaults, so Docker Compose fails if not configured:
   ```yaml
   MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}
   ```
2. Do not expose port 3306 externally -- remove the `ports:` mapping for the db service or bind to 127.0.0.1:
   ```yaml
   ports:
     - "127.0.0.1:3306:3306"
   ```
3. Add a minimum complexity requirement or generate random passwords in `.env.example` instructions

---

### H-4: Missing `.dockerignore` for API Dockerfile -- Secrets Leakage Risk

**Severity:** High (CVSS 7.0)
**CWE:** CWE-538 (Insertion of Sensitive Information into Externally-Accessible File or Directory)
**Location:** `Dockerfile.api`, line 11; `.dockerignore`

**Description:**
The root `.dockerignore` does exclude `.env` and `app/`, which is good. However, the `COPY . .` in `Dockerfile.api` (line 11) copies the entire project root into the image. If additional sensitive files are added later (e.g., `credentials.json`, SSH keys, test secrets), they will be included unless `.dockerignore` is updated.

Currently excluded: `.git`, `node_modules`, `.env`, `__pycache__`, `.venv`, `app/`

**Not excluded but present:**
- `invalid_json_dumps/` (could contain scraped content)
- `researchers.md` (untracked file with potential researcher PII)
- `ISSUES.md` (internal documentation)
- `urls.csv` (if present -- referenced in `database.py:233`)
- `*.log` files (not excluded by `.dockerignore`, only by `.gitignore`)

**Remediation:**
Convert the `.dockerignore` to a whitelist approach:

```dockerignore
# Ignore everything
*

# Allow only what's needed
!requirements.txt
!api.py
!database.py
!db_config.py
!html_fetcher.py
!publication.py
!researcher.py
!scheduler.py
```

---

### H-5: Thread-Unsafe `requests.Session` Shared Across Threads

**Severity:** High (CVSS 6.2)
**CWE:** CWE-362 (Concurrent Execution using Shared Resource with Improper Synchronization)
**Location:** `html_fetcher.py`, lines 23-26

**Description:**
`HTMLFetcher.session` is a class-level `requests.Session()` shared across all callers. The scrape job runs in a background thread (spawned from `api.py:359`), while the main asyncio event loop also imports this module. `requests.Session` is not thread-safe.

Similarly, `HTMLFetcher._domain_last_request` (line 29) is a plain dict accessed from multiple threads without locking.

**Attack Scenario:**
Concurrent scrape triggers (especially given the TOCTOU issue in H-2) could corrupt the shared session's connection pool or cookie jar, leading to:
- Requests being sent with wrong cookies or headers
- Connection pool corruption causing crashes
- Race conditions on the rate-limit dict causing incorrect timing

**Remediation:**
Create a new session per scrape job invocation, or protect the shared state with a threading lock:

```python
import threading

class HTMLFetcher:
    _lock = threading.Lock()
    _domain_last_request: dict[str, float] = {}

    @staticmethod
    def _get_session() -> requests.Session:
        """Create a new session for each scrape cycle."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; HTMLFetcher/1.0)'
        })
        return session
```

---

### H-6: SCRAPE_API_KEY Defaults to "changeme" -- Effectively Unauthenticated in Default Deployment

**Severity:** High (CVSS 8.1)
**CWE:** CWE-1393 (Use of Default Password)
**Location:** `docker-compose.yml`, line 33; `.env.example`, line 17

**Description:**
The `SCRAPE_API_KEY` defaults to `changeme` in both `docker-compose.yml` and `.env.example`. This is the authentication token for the `POST /api/scrape` endpoint. Anyone who knows the default (which is committed to the public repo) can trigger scrapes.

A triggered scrape makes external HTTP requests to researcher URLs, calls the OpenAI API (incurring cost), and writes to the database. This is effectively an unauthenticated endpoint in default deployments.

**Attack Scenario:**
An attacker sends `POST /api/scrape` with `X-API-Key: changeme`. This triggers:
1. External HTTP requests to all researcher URLs (bandwidth, rate limit abuse)
2. OpenAI API calls (financial cost to the project owner)
3. Potential insertion of duplicate/malicious publications

**Remediation:**
1. Remove the default value: `SCRAPE_API_KEY: ${SCRAPE_API_KEY:?SCRAPE_API_KEY is required}`
2. Add validation in `api.py` at startup to reject known weak keys:
   ```python
   WEAK_KEYS = {"changeme", "secret", "test", "password", ""}
   scrape_api_key = os.environ.get("SCRAPE_API_KEY", "")
   if scrape_api_key in WEAK_KEYS:
       raise RuntimeError("SCRAPE_API_KEY must be set to a strong, unique value")
   ```
3. Document minimum key length/entropy requirements

---

## Medium Findings

### M-1: SSRF Protection Vulnerable to DNS Rebinding

**Severity:** Medium (CVSS 6.4)
**CWE:** CWE-918 (Server-Side Request Forgery)
**Location:** `html_fetcher.py`, lines 49-85, 100-131

**Description:**
The `validate_url()` method resolves the hostname and checks that the IP is not private/reserved. However, `fetch_html()` then makes a separate HTTP request via `requests.Session.get()`. Between the DNS validation and the actual HTTP request, DNS rebinding can occur -- the hostname initially resolves to a public IP (passing validation) but then resolves to a private IP (e.g., `127.0.0.1`, `169.254.169.254`) when the actual connection is made.

**Attack Scenario:**
An attacker adds a researcher URL pointing to a domain they control. The domain's DNS is configured with a short TTL:
1. First resolution (during `validate_url`): returns `93.184.216.34` (public) -- passes validation
2. Second resolution (during `requests.get`): returns `169.254.169.254` (AWS metadata) -- SSRF succeeds

The response (instance metadata including IAM credentials) gets stored in `html_content` table and could be extracted via the publications extraction pipeline or future API endpoints.

**Remediation:**
Pin the resolved IP address and use it for the actual request:

```python
@staticmethod
def validate_and_resolve(url):
    """Validate URL and return the resolved IP to pin for the request."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    import socket
    try:
        resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
        ip = ipaddress.ip_address(resolved_ip)
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            return None
        return resolved_ip
    except (socket.gaierror, ValueError):
        return None
```

Then force the requests library to connect to the pinned IP using a custom transport adapter or by overriding the hostname resolution.

---

### M-2: Error Exception Handlers Leak Internal Details

**Severity:** Medium (CVSS 5.3)
**CWE:** CWE-209 (Generation of Error Message Containing Sensitive Information)
**Location:** `api.py`, lines 89-136

**Description:**
The 400, 401, 409, and 422 error handlers include `str(exc.detail)` or `str(exc)` directly in the response:

```python
content={"error": {"code": "bad_request", "message": str(exc.detail) if hasattr(exc, "detail") else str(exc)}}
```

For unhandled exceptions caught by these handlers, `str(exc)` could expose internal stack traces, database connection strings, SQL query fragments, or filesystem paths.

The 500 handler at line 139 correctly returns a generic message -- but only if the exception is explicitly raised with status 500. Unhandled exceptions in FastAPI actually result in a traceback in the response body by default unless debug mode is explicitly off.

**Remediation:**
1. Ensure all error handlers only return pre-defined messages for non-HTTPException errors:
   ```python
   @app.exception_handler(400)
   async def bad_request_handler(request: Request, exc):
       message = exc.detail if isinstance(exc, HTTPException) else "Bad request"
       return JSONResponse(
           status_code=400,
           content={"error": {"code": "bad_request", "message": message}},
       )
   ```
2. Add a global catch-all exception handler:
   ```python
   @app.exception_handler(Exception)
   async def unhandled_exception_handler(request: Request, exc: Exception):
       logger.error(f"Unhandled exception: {exc}", exc_info=True)
       return JSONResponse(
           status_code=500,
           content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
       )
   ```

---

### M-3: No Rate Limiting on Public API Endpoints

**Severity:** Medium (CVSS 5.3)
**CWE:** CWE-770 (Allocation of Resources Without Limits or Throttling)
**Location:** `api.py` -- all `@app.get` endpoints

**Description:**
The public GET endpoints (`/api/publications`, `/api/researchers`, `/api/researchers/{id}`, `/api/scrape/status`) have no rate limiting. Each request creates a new database connection (per `Database.get_connection()`), executes queries, and returns data. The `list_researchers` endpoint (line 274) additionally makes N+1 queries (two extra queries per researcher for URLs and publication count).

**Attack Scenario:**
An attacker sends thousands of concurrent requests to `/api/researchers`, each of which generates 2N+1 database queries. This exhausts the MySQL connection pool, causing denial of service for legitimate users.

**Remediation:**
Add rate limiting middleware using `slowapi` or a custom middleware:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.get("/api/publications")
@limiter.limit("60/minute")
async def list_publications(request: Request, ...):
    ...
```

---

### M-4: No Authentication on Read Endpoints -- Data Enumeration

**Severity:** Medium (CVSS 4.3)
**CWE:** CWE-284 (Improper Access Control)
**Location:** `api.py`, lines 183-331

**Description:**
All GET endpoints are publicly accessible without any authentication. The researcher endpoints expose personal information (names, positions, affiliations, publication URLs). The sequential integer IDs make enumeration trivial: an attacker can iterate `/api/researchers/1`, `/api/researchers/2`, etc. to extract the full researcher database.

**Risk Assessment:**
For a public-facing research aggregator, this may be intentional by design. However, researcher profile data (position, affiliation, curated URL lists) combined with publication metadata could be harvested for:
- Targeted phishing attacks against researchers
- Competitive intelligence gathering
- Data scraping for commercial purposes

**Remediation:**
If public access is intentional, document this as an accepted risk. Otherwise:
1. Add read authentication (API key or OAuth)
2. Use UUIDs instead of sequential integers for researcher IDs
3. Implement per-IP rate limiting (see M-3)

---

### M-5: Frontend Missing Content-Security-Policy and Security Headers

**Severity:** Medium (CVSS 4.7)
**CWE:** CWE-1021 (Improper Restriction of Rendered UI Layers)
**Location:** `app/next.config.mjs`

**Description:**
The Next.js frontend has an empty configuration (`const nextConfig = {};`). While the API sets security headers (`X-Frame-Options`, CSP, HSTS), the frontend served by Next.js does not set any security headers. The API's CSP (`default-src 'self'`) only applies to API responses, not to the frontend pages.

**Remediation:**
Add security headers to `next.config.mjs`:

```javascript
const nextConfig = {
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
          {
            key: 'Content-Security-Policy',
            value: "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' " + (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'),
          },
          { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains' },
        ],
      },
    ];
  },
};
```

---

### M-6: Dependency Version Ranges Allow Vulnerable Patch Versions

**Severity:** Medium (CVSS 5.0)
**CWE:** CWE-1395 (Dependency on Vulnerable Third-Party Component)
**Location:** `requirements.txt`

**Description:**
Dependencies use wildcard patch versions (e.g., `requests==2.32.*`, `fastapi==0.115.*`). While this allows automatic patch updates, it also means the exact installed version is not deterministic across environments. A compromised or vulnerable patch release could be pulled automatically.

Notable concerns:
- `mysql-connector-python==9.1.*` -- MySQL connector has had past CVEs (e.g., CVE-2024-21272)
- `beautifulsoup4==4.12.*` -- Parses untrusted HTML from external websites
- `requests==2.32.*` -- Core HTTP library
- `openai==1.59.*` -- Handles API key material

On the frontend (`app/package.json`):
- `next: "14.2.35"` -- should be verified against known Next.js CVEs
- Several `^` version ranges in devDependencies

**Remediation:**
1. Pin exact versions in `requirements.txt`:
   ```
   requests==2.32.3
   beautifulsoup4==4.12.3
   ```
2. Use `pip-audit` or `safety` for Python dependency scanning
3. Use `npm audit` for frontend dependency scanning
4. Add dependency scanning to CI/CD pipeline
5. Generate and maintain an SBOM (Software Bill of Materials)

---

### M-7: No TLS/HTTPS Enforcement in Docker Compose

**Severity:** Medium (CVSS 5.9)
**CWE:** CWE-319 (Cleartext Transmission of Sensitive Information)
**Location:** `docker-compose.yml`

**Description:**
The Docker Compose configuration exposes services over plaintext HTTP:
- API on port 8000 (HTTP)
- Frontend on port 3000 (HTTP)
- MySQL on port 3306 (unencrypted)

The `HSTS` header is set in `api.py:80`, but this only takes effect if the initial connection is already over HTTPS. Without TLS termination, all traffic (including the `X-API-Key` header for scrape authentication) is transmitted in plaintext.

**Remediation:**
1. Add a reverse proxy (nginx/Caddy/Traefik) with TLS termination
2. Do not expose MySQL port externally (remove `ports: - "3306:3306"`)
3. For production, use managed TLS certificates (Let's Encrypt via Caddy, or cloud load balancer)

---

### M-8: Logging May Contain Sensitive Information

**Severity:** Medium (CVSS 4.0)
**CWE:** CWE-532 (Insertion of Sensitive Information into Log File)
**Location:** `database.py`, lines 27, 42, 58, 73, 87; `html_fetcher.py`, lines 42, 62, 79, 120; `publication.py`, line 147

**Description:**
Error handlers log exception details that may contain sensitive information:

```python
# database.py:42
logging.error(f"Error connecting to the database: {e}")
# Could log: "Access denied for user 'econ_app'@'localhost' (using password: YES)"

# database.py:58
logging.error(f"Database error in execute_query: {e}")
# Could log full SQL queries with parameter values

# publication.py:147
logging.error(f"Error in OpenAI API call: {str(e)}")
# Could log API key in error messages
```

**Remediation:**
1. Sanitize exception messages before logging -- strip credentials and connection strings
2. Use structured logging with explicit fields rather than `str(e)`
3. Configure log levels appropriately for production (WARNING and above)
4. Ensure logs are stored securely and rotated

---

### M-9: Use of Deprecated `datetime.utcnow()`

**Severity:** Medium (CVSS 3.7)
**CWE:** CWE-704 (Incorrect Type Conversion or Cast)
**Location:** `html_fetcher.py`, line 164; `scheduler.py`, lines 29, 42

**Description:**
The codebase uses `datetime.utcnow()` which returns a naive datetime (without timezone info). This was deprecated in Python 3.12. Naive datetimes can cause subtle bugs when compared with timezone-aware datetimes (as used in `api.py:356` with `datetime.now(timezone.utc)`).

**Remediation:**
Replace all instances of `datetime.utcnow()` with `datetime.now(timezone.utc)`:

```python
from datetime import datetime, timezone
# Replace: datetime.utcnow()
# With:    datetime.now(timezone.utc)
```

---

### M-10: Insufficient Security Test Coverage

**Severity:** Medium (CVSS 4.0)
**CWE:** CWE-1164 (Irrelevant Code)
**Location:** `tests/` directory

**Description:**
The test suite covers basic middleware functionality (security headers, CORS, error envelopes, scrape auth). However, there are no tests for:
- SQL injection attempts on query parameters
- SSRF validation bypass attempts
- API key brute-force / timing attack resistance
- Input validation edge cases (e.g., very long strings, special characters, Unicode)
- Scrape lock race conditions
- Rate limiting behavior
- Error information leakage (checking that error responses don't contain stack traces)

**Remediation:**
Add security-focused test cases:

```python
class TestSecurityInputValidation:
    def test_sql_injection_in_year_param(self, client):
        response = client.get("/api/publications?year=2024' OR '1'='1")
        assert response.status_code in (200, 400)
        # Verify no data leakage

    def test_extremely_long_query_param(self, client):
        response = client.get(f"/api/publications?year={'A' * 10000}")
        assert response.status_code == 400

    def test_scrape_timing_attack_resistance(self, client):
        # Statistical test comparing response times for different key prefixes
        ...
```

---

## Low Findings

### L-1: CORS `allow_methods=["*"]` Is Overly Permissive

**Severity:** Low (CVSS 3.1)
**CWE:** CWE-942 (Overly Permissive Cross-domain Whitelist)
**Location:** `api.py`, line 66

**Description:**
The CORS middleware allows all HTTP methods (`allow_methods=["*"]`), but the API only uses GET and POST. Allowing DELETE, PUT, PATCH, etc. via CORS is unnecessary and increases attack surface.

**Remediation:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)
```

---

### L-2: Missing `Referrer-Policy` and `Permissions-Policy` Headers

**Severity:** Low (CVSS 3.1)
**CWE:** CWE-16 (Configuration)
**Location:** `api.py`, lines 74-81

**Description:**
The security headers middleware sets four headers but omits `Referrer-Policy` and `Permissions-Policy`. While not critical for an API, these headers provide defense-in-depth.

**Remediation:**
```python
response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
```

---

### L-3: Hardcoded User-Agent String Enables Fingerprinting

**Severity:** Low (CVSS 2.0)
**CWE:** CWE-200 (Exposure of Sensitive Information to an Unauthorized Actor)
**Location:** `html_fetcher.py`, lines 24-25

**Description:**
The scraper uses a custom User-Agent: `Mozilla/5.0 (compatible; HTMLFetcher/1.0)`. This uniquely identifies the application to target websites, enabling fingerprinting and targeted blocking or exploitation.

**Remediation:**
Use a more standard User-Agent or make it configurable via environment variable. Also consider rotating User-Agent strings.

---

### L-4: No Request Body Size Limits

**Severity:** Low (CVSS 3.7)
**CWE:** CWE-770 (Allocation of Resources Without Limits or Throttling)
**Location:** `api.py`, line 338

**Description:**
The `POST /api/scrape` endpoint does not enforce request body size limits. While the endpoint currently ignores the body, a future modification could introduce a body parser without size limits. Uvicorn has a default limit, but it should be explicitly configured.

**Remediation:**
Set explicit limits in uvicorn configuration or add middleware to reject oversized requests.

---

### L-5: No Pagination on Researchers Endpoint

**Severity:** Low (CVSS 3.7)
**CWE:** CWE-400 (Uncontrolled Resource Consumption)
**Location:** `api.py`, lines 273-291

**Description:**
`GET /api/researchers` returns all researchers without pagination. As the dataset grows, this will cause increasing response sizes and memory consumption. Each researcher also triggers 2 additional queries (N+1 problem).

**Remediation:**
Add pagination similar to the publications endpoint:
```python
@app.get("/api/researchers")
async def list_researchers(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
```

---

### L-6: `db_config.py` Calls `sys.exit(1)` at Import Time

**Severity:** Low (CVSS 2.5)
**CWE:** CWE-705 (Incorrect Control Flow Scoping)
**Location:** `db_config.py`, lines 10-16

**Description:**
If required environment variables are missing, `db_config.py` calls `sys.exit(1)` at module import time. This makes the module impossible to import for testing or tooling without the full environment configured. The test `conftest.py` works around this by setting environment variables before import, but this is fragile.

**Remediation:**
Raise a descriptive exception instead of calling `sys.exit()`:

```python
if _missing:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(_missing)}\n"
        "Copy .env.example to .env and fill in all required values."
    )
```

---

### L-7: Invalid JSON Responses Dumped to Filesystem Without Cleanup

**Severity:** Low (CVSS 2.3)
**CWE:** CWE-459 (Incomplete Cleanup)
**Location:** `publication.py`, lines 169-180

**Description:**
When OpenAI returns invalid JSON, the response is dumped to `invalid_json_dumps/` directory. These files are never cleaned up and could:
- Accumulate indefinitely, consuming disk space
- Contain scraped website content (potentially copyrighted or sensitive)
- Be accessible if the directory is served or included in Docker images

**Remediation:**
1. Log the invalid response content to the structured logging system instead of writing files
2. If file-based debugging is needed, implement automatic rotation/cleanup
3. Ensure `invalid_json_dumps/` is in `.dockerignore` (it is in `.gitignore` but not in `.dockerignore`)

---

### L-8: Frontend Dependency Pinning Inconsistencies

**Severity:** Low (CVSS 2.0)
**CWE:** CWE-1395 (Dependency on Vulnerable Third-Party Component)
**Location:** `app/package.json`

**Description:**
The frontend uses a mix of exact version pins (`next: "14.2.35"`) and caret ranges (`react: "^18"`, `swr: "^2.4.1"`). The `package-lock.json` provides deterministic installs, but caret ranges in `package.json` mean `npm update` could pull in breaking or vulnerable versions.

**Remediation:**
Pin all production dependencies to exact versions. Use Dependabot or Renovate for automated update PRs.

---

## Positive Security Observations

The following security measures are already in place and should be maintained:

1. **Parameterized SQL queries** -- All data-plane queries use `%s` parameterization via MySQL connector, preventing SQL injection on data operations (`api.py`, `database.py`, `html_fetcher.py`, `publication.py`, `researcher.py`)

2. **SSRF validation** -- `html_fetcher.py` validates URLs against private IP ranges, metadata endpoints, and non-HTTP schemes before scraping (`html_fetcher.py:49-85`)

3. **robots.txt compliance** -- The scraper respects `robots.txt` directives (`html_fetcher.py:32-46`)

4. **CORS restriction** -- CORS is limited to a single frontend origin rather than wildcard (`api.py:62-67`)

5. **Security headers** -- The API middleware sets `X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`, and `Strict-Transport-Security` (`api.py:74-81`)

6. **Non-root Docker users** -- Both Dockerfiles create and switch to non-root users (`Dockerfile.api:5-12`, `app/Dockerfile:5-15`)

7. **Response size limiting** -- The scraper limits response size to 1MB and text content to 4000 chars (`html_fetcher.py:19, 205-208`)

8. **Pydantic validation** -- LLM output is validated through Pydantic models before database insertion (`publication.py:16-28, 138-144`)

9. **`.env` excluded from git** -- The `.gitignore` correctly excludes `.env` and it is not tracked

10. **Rate limiting on scraper** -- Per-domain rate limiting prevents aggressive scraping (`html_fetcher.py:87-97`)

11. **React text rendering** -- Frontend uses React's JSX text interpolation (not `dangerouslySetInnerHTML`), providing automatic XSS protection for rendered content

12. **Generic 500 error responses** -- The 500 error handler returns a generic message without leaking internals (`api.py:139-144`)

---

## Remediation Priority Matrix

| Priority | Finding | Effort | Impact |
|----------|---------|--------|--------|
| **Immediate** | C-1: Rotate exposed API key | Low | Prevents financial/access abuse |
| **Immediate** | H-6: Remove default SCRAPE_API_KEY | Low | Prevents unauthenticated scraping |
| **This sprint** | C-2: Fix SQL identifier injection | Low | Prevents SQL injection |
| **This sprint** | H-1: Use `hmac.compare_digest` | Low | Prevents timing attacks |
| **This sprint** | H-2: Fix TOCTOU lock race | Medium | Prevents duplicate scrapes |
| **This sprint** | H-3: Remove weak Docker defaults | Low | Prevents default credential attacks |
| **This sprint** | H-4: Whitelist `.dockerignore` | Low | Prevents secret leakage in images |
| **Next sprint** | H-5: Fix thread-unsafe session | Medium | Prevents concurrency bugs |
| **Next sprint** | M-1: Fix DNS rebinding in SSRF | High | Prevents advanced SSRF |
| **Next sprint** | M-2: Sanitize error responses | Low | Prevents information disclosure |
| **Next sprint** | M-3: Add API rate limiting | Medium | Prevents DoS |
| **Backlog** | M-4 through M-10 | Various | Defense-in-depth |
| **Backlog** | L-1 through L-8 | Various | Hardening |

---

## Appendix: OWASP Top 10 (2021) Mapping

| OWASP Category | Findings | Status |
|---------------|----------|--------|
| A01: Broken Access Control | M-4, L-1 | Partial -- auth only on scrape endpoint |
| A02: Cryptographic Failures | C-1, M-7 | Needs work -- plaintext secrets, no TLS |
| A03: Injection | C-2 | Critical -- SQL identifier injection |
| A04: Insecure Design | H-2, H-5, H-6 | Race conditions, weak defaults |
| A05: Security Misconfiguration | H-3, H-4, M-5, L-2, L-6 | Multiple configuration gaps |
| A06: Vulnerable/Outdated Components | M-6, L-8 | Unpinned dependencies |
| A07: Identification/Authentication Failures | H-1, H-6 | Timing attack, default keys |
| A08: Software/Data Integrity Failures | -- | Pydantic validation helps; no SBOM |
| A09: Security Logging/Monitoring Failures | M-8, M-10 | Logging present but leaky; no monitoring |
| A10: Server-Side Request Forgery | M-1 | SSRF protections exist but bypassable |

---

*End of Security Audit Report*
