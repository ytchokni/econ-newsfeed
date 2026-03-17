# Issues

Tracking known issues and planned improvements for the econ-newsfeed scraping pipeline.

---

## Issue 5: 4000-character content truncation may miss publications

**Status:** Partially resolved
**Severity:** Medium
**File:** `publication.py:120`

The LLM prompt truncates page content to 4000 characters (`text_content[:4000]`). Truncation logging was added in `html_fetcher.py:205-208`, but the smarter truncation strategy (chunking or prioritizing diff content) is not yet implemented.

---

## Issue 6: No connection pooling in database access

**Status:** Open
**Severity:** Critical (revised from Low — see full analysis)
**File:** `database.py:34-43`

`get_connection()` creates a new MySQL connection on every query: full TCP handshake, TLS negotiation, authentication, and DB selection on every `fetch_one`, `fetch_all`, and `execute_query` call. Combined with N+1 patterns (Issue 16), the publications endpoint opens 22+ connections per request, costing 440–1100 ms in connection overhead alone on Docker.

**Proposed fix:** Replace with `MySQLConnectionPool(pool_size=10)` — pool checkout is sub-millisecond vs. 2–50 ms per fresh connection. Full implementation in `PERFORMANCE_ANALYSIS.md` § DB-1.



## Issue 7: Title changes vs new papers

**Status:** Open
**Severity:** Medium
**File:** `publication.py:40-58`

When a paper title changes slightly (e.g. working paper revision), the current exact-match dedup treats it as a new publication. Comparing everything with an LLM could be expensive. A low-cost alternative would be deterministic pattern matching (e.g. normalised token overlap, Jaccard similarity on title words).

---

## Issue 8: Duplicate researchers from abbreviated and misspelled names

**Status:** Open
**Severity:** High
**File:** `database.py:172-194`

### Problem

`get_researcher_id()` matches on exact `(first_name, last_name)`. When the LLM extracts co-authors from publication pages it typically only sees initials, so co-authors are stored with abbreviated first names. This creates duplicate researcher rows for the same person.

**Current researchers table (2026-03-17) — illustrating the problem:**

| id | first_name        | last_name            | notes                                          |
|----|-------------------|----------------------|-------------------------------------------------|
|  1 | Max Friedrich      | Steinhardt           | Seeded from CSV — full name, has position/affiliation |
|  4 | M.                 | Steinhardt           | Created by LLM extraction — same person as id 1 |
| 28 | Max                | Friedrich Steinhardt | LLM split the name wrong — same person as id 1  |
|  2 | M.                 | Berlemann            | Initial only — full first name unknown           |
| 16 | P.                 | Conconi              | Initial only                                     |

29 out of 31 researchers are stored with initials only because the LLM extracts what the paper shows (e.g. "M. Steinhardt, E. Haustein, J. Tutt"). Researcher id 1 (Max Friedrich Steinhardt) has **three** duplicate rows (ids 1, 4, 28).

### Impact

- Co-author pages show initials instead of full names
- The same person appears multiple times in the researcher directory
- Publication counts are split across duplicate entries
- Linking publications to their correct researcher is unreliable

### Proposed fix: Prompt improvement + LLM disambiguation (two-layer approach)

**Layer 1 — Improve extraction prompt (low cost, immediate)**

Update the LLM extraction prompt in `publication.py` to instruct: *"Always use full first names when possible. If only an initial is shown, return the initial."* This won't solve the problem fully (the source text often only has initials) but reduces unnecessary abbreviation.

**Layer 2 — LLM candidate disambiguation (medium cost, high accuracy)**

When `get_researcher_id()` finds no exact match, query all existing researchers with the same last name and ask the LLM to disambiguate:

```
Existing researchers with last name "Steinhardt":
  1. "Max Friedrich Steinhardt" (Professor, Freie Universität Berlin)
  2. "M. Steinhardt" (no affiliation)

New author to match: "M. Steinhardt"

Is the new author the same person as any existing researcher? Return the id or "new".
```

This LLM call only fires when exact match fails, so cost is bounded. The candidate set (same last name) is typically 1-3 entries, keeping the prompt cheap.

**Implementation sketch:**

```python
def get_researcher_id(first_name, last_name, position=None, affiliation=None):
    # 1. Exact match — fast path
    exact = query("... WHERE first_name = %s AND last_name = %s", ...)
    if exact:
        return exact['id']

    # 2. Candidate match — same last name
    candidates = query("... WHERE last_name = %s", last_name)
    if candidates:
        match = llm_disambiguate(first_name, last_name, candidates)
        if match:
            return match['id']

    # 3. No match — create new researcher
    return insert_new(first_name, last_name, position, affiliation)
```

**Trade-offs:**
- Extra LLM call per unknown author, but only on exact-match miss (~cheap, small prompt)
- Risk of false positives if two different people share a last name and initial — mitigated by using affiliation as context
- Could add an alias table later to cache resolved matches and skip repeat LLM calls


---

## Issue 9: Batch LLM processing to reduce HTML parsing cost

**Status:** Open
**Severity:** Medium
**File:** `publication.py:109`

`extract_publications()` fires one LLM API call per URL. A scrape run over 100 researchers makes 100+ round-trips at full per-call overhead.

**Proposed fix:** Group pages into batches of N (e.g. 5–10) and ask the LLM to return a JSON object keyed by URL, with each value being the publications array for that page.

```python
# Rough shape of batched prompt output:
{
  "https://example.com/researcher-a": [{"title": "...", ...}, ...],
  "https://example.com/researcher-b": [...]
}
```

**Trade-offs:**
- Fewer API round-trips → lower latency and cost
- Larger prompt → more tokens per call; per-page character budget shrinks (worsens Issue 5)
- A single malformed response now blocks the whole batch — need per-URL error isolation when parsing
- Optimal batch size will need tuning; start with 5 and measure token usage

**Implementation notes:**
- Accumulate (url, text) pairs in `scheduler.py` before dispatching to `extract_publications`
- Add a `batch_size` config env var so it can be tuned without code changes
- Retry failed individual URLs from a batch individually

---

## Issue 10: Rename `publications` to `papers`; add paper `status` column

**Status:** Open
**Severity:** Medium
**Files:** `database.py:116`, `publication.py`, `api.py`

The `publications` table should be renamed to `papers` and gain a `status` column to track a paper's submission/publication lifecycle.

**Schema change:**

```sql
ALTER TABLE publications RENAME TO papers;

ALTER TABLE papers
  ADD COLUMN status ENUM(
    'published',
    'accepted',
    'revise_and_resubmit',
    'reject_and_resubmit'
  ) DEFAULT NULL;
```

**Affected code:**
- `database.py` — `create_tables()` definition and all raw SQL strings referencing `publications`
- `publication.py` — `save_publications()`, `get_all_publications()`
- `api.py` — all queries in `/api/publications` and `/api/publications/{id}` endpoints (route paths can stay the same or be updated to `/api/papers`)
- `authorship` table FK references `publications(id)` — must be updated in the migration

**Status values:**
| Value | Meaning |
|---|---|
| `published` | Appeared in a journal / proceedings |
| `accepted` | Accepted, not yet published |
| `revise_and_resubmit` | R&R received |
| `reject_and_resubmit` | Rejected, resubmitted elsewhere |
| `NULL` | Unknown / working paper |

**Notes:**
- Status is not always extractable from the page. The LLM extraction prompt should attempt to infer it (e.g. "Forthcoming in JEP" → `accepted`), but NULL is a valid default.
- The `status` field should be exposed in the API response and filterable via `?status=published`.

---

## Issue 11: Researcher field taxonomy — onboarding and categorisation

**Status:** Open
**Severity:** Medium
**Files:** `database.py`, `api.py`, frontend homepage

The homepage should capture which economics subfields each researcher works in via a predefined, filterable taxonomy — not free text. This underpins topic filtering (Issue 12).

**Database changes:**

```sql
CREATE TABLE research_fields (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    slug VARCHAR(100) NOT NULL UNIQUE  -- e.g. 'labour-economics'
);

CREATE TABLE researcher_fields (
    researcher_id INT NOT NULL,
    field_id INT NOT NULL,
    PRIMARY KEY (researcher_id, field_id),
    FOREIGN KEY (researcher_id) REFERENCES researchers(id),
    FOREIGN KEY (field_id) REFERENCES research_fields(id)
);
```

**Initial taxonomy (seed data):**
Macroeconomics, Labour Economics, Cultural Economics, Migration, Political Economy, Development Economics, International Trade, Finance, Health Economics, Public Economics, Industrial Organisation, Econometrics/Methods

**API changes:**
- `GET /api/fields` — return full taxonomy list (for populating the UI)
- `GET /api/researchers` — include `fields: [{id, name, slug}]` in each researcher object
- `PATCH /api/researchers/{id}` — allow updating field assignments (admin or self-service)

**Frontend:**
- Homepage (or researcher profile edit page) shows a multi-select chip/checkbox UI using the taxonomy from `/api/fields`
- Selections are saved immediately on toggle (no submit button needed)

---

## Issue 12: Feed shows only new discoveries; researcher profile shows bio

**Status:** Open
**Severity:** High
**Files:** `api.py`, `database.py`, frontend feed view

Two related product changes:

### 12a — Feed shows only changes

The feed (`/api/publications`) currently returns all papers ordered by discovery timestamp. It should surface only papers new since the user's last visit, and re-visits to a researcher page should show their full working-paper list.

- Add a `?since=<ISO8601>` query param to `/api/publications` so the frontend can pass the user's last-seen timestamp and receive only new entries
- Frontend should persist `last_seen` in `localStorage` and pass it on load
- Clicking a researcher card navigates to `/researchers/{id}` which shows their full paper list — this already works via `GET /api/researchers/{id}`

### 12b — Researcher bio field

If a researcher's homepage contains a short description or bio, store and display it.

**Database change:**

```sql
ALTER TABLE researchers ADD COLUMN bio TEXT;
```

**Scraping change (`researcher.py` / `html_fetcher.py`):**
- When scraping a researcher's homepage, pass the text to the LLM with an additional extraction target: `"bio"` — a ≤2-sentence description of the researcher if present on the page
- Store the extracted bio in `researchers.bio`
- If no bio is found, leave NULL (do not overwrite an existing bio with NULL on re-scrape)

**API change:**
- Include `bio` in `GET /api/researchers` list items and `GET /api/researchers/{id}`

---

## Issue 13: Filtering by institution, topic, and position; presets

**Status:** Open
**Severity:** Medium
**Depends on:** Issue 11 (field taxonomy)
**File:** `api.py:273`

`GET /api/researchers` needs filter query params and preset shortcuts.

**New query params:**

| Param | Type | Example |
|---|---|---|
| `institution` | string (partial match) | `?institution=Harvard` |
| `field` | slug or id (repeatable) | `?field=labour-economics&field=migration` |
| `position` | string (partial match) | `?position=Professor` |
| `preset` | string | `?preset=top20` |

**Presets:**

- `top20` — hardcoded list of Top-20 economics departments (US News / QS); stored as a config constant, not in DB
- `repec400` — Top 400 by RepEC author ranking; requires either a periodic import from the RepEC public data file (`https://ideas.repec.org/top/top.person.all.html`) or a manual seed CSV

**Implementation notes:**
- Presets translate to `WHERE affiliation IN (...)` on the server — no special DB schema needed for `top20`
- RepEC ranking requires a `repec_rank INT` column on `researchers` and a background refresh job
- All params are combinable; presets act as a shorthand for a common institution filter

---

## Issue 14: Surface personal website link prominently

**Status:** Open
**Severity:** Low
**Files:** `api.py:253`, frontend researcher card

The `researcher_urls` table already stores URLs by `page_type`. The personal website link (typically `page_type = 'homepage'` or `'personal'`) is buried in the generic `urls` array in the API response.

**Changes needed:**
- Agree on and enforce a canonical `page_type` value for personal websites (e.g. `'homepage'`)
- API: pull the homepage URL out of the `urls` array and expose it as a top-level `website_url` field in researcher responses, alongside the raw `urls` array
- Frontend: render a prominent "Personal website →" link on researcher cards and profile pages, with the URL as the anchor (avoids displaying raw URLs)
- If multiple homepage-type URLs exist, prefer the first one and log a warning

---

## Issue 15: Draft availability — track and display draft links

**Status:** Open
**Severity:** Medium
**Files:** `database.py:116`, `publication.py:109`, frontend feed

Each paper should indicate whether a PDF draft is available and link to it directly.

**Database change:**

```sql
ALTER TABLE papers ADD COLUMN draft_url VARCHAR(2048) DEFAULT NULL;
```

A `NULL` value means no draft link was found. A non-null value is the URL of the PDF or SSRN/NBER preprint page.

**LLM extraction change (`publication.py`):**
- Extend the extraction prompt to also extract `draft_url` per publication: *"If a link to a PDF, SSRN, NBER, or preprint is associated with this paper, include it as `draft_url`."*
- Add `draft_url: Optional[str]` to the `PublicationExtraction` Pydantic model
- Store the value in `papers.draft_url`

**API change:**
- Include `draft_url` and a derived `draft_available: bool` in the publication response shape

**Frontend:**
- Feed items show a "Draft ↗" badge/link when `draft_available` is true
- "No draft" label shown when false (helps researchers know the status at a glance)

---

# Performance Issues

*Full analysis, code examples, and scaling projections in `PERFORMANCE_ANALYSIS.md`.*

---

## Issue 16: N+1 queries on all list endpoints

**Status:** Open
**Severity:** Critical
**Files:** `api.py:222-225`, `api.py:279-291`, `api.py:317-320`
**Ref:** `PERFORMANCE_ANALYSIS.md` § DB-2

Every list endpoint loops over rows and fires 1–2 additional queries per item. The `/api/researchers` endpoint issues `1 + 2N` queries (N = total researcher count, unbounded). At 50 researchers that is 101 queries; at 500 it is 1001.

**Fix:** Replace per-row lookups with batch-IN or JOIN queries. A single batch-fetch function for authors (`_get_authors_for_publications(pub_ids)`) reduces the publications endpoint from 22 queries to 3. The researchers list can be collapsed to 2–3 queries with a `GROUP BY` + separate URL batch-fetch.

---

## Issue 17: Missing UNIQUE constraint on `authorship` table

**Status:** Open
**Severity:** High
**Files:** `database.py:142-153`, `publication.py:74-85`
**Ref:** `PERFORMANCE_ANALYSIS.md` § DB-3

No UNIQUE key on `(researcher_id, publication_id)` means re-scraping a page can insert duplicate authorship rows, inflating publication counts and producing duplicate author names in responses.

**Fix:** `ALTER TABLE authorship ADD UNIQUE KEY uq_researcher_pub (researcher_id, publication_id);` and change the INSERT to `INSERT IGNORE`.

---

## Issue 18: Full-table-scan deduplication query

**Status:** Open
**Severity:** High
**File:** `publication.py:52-55`
**Ref:** `PERFORMANCE_ANALYSIS.md` § DB-4

`WHERE LOWER(TRIM(title)) = %s AND url = %s` prevents MySQL from using the `uq_title_url` index, forcing a full table scan on every publication save. Degrades as the table grows.

**Fix:** Add a `title_normalized` generated column with its own index, or normalize titles before insertion so the existing index is usable.

---

## Issue 19: No pagination on researchers endpoint

**Status:** Open
**Severity:** High
**Files:** `api.py:273-291`
**Ref:** `PERFORMANCE_ANALYSIS.md` § IO-1 | `SECURITY_AUDIT.md` § L-5

`GET /api/researchers` fetches all researchers with no `LIMIT`. Combined with N+1 (Issue 16), at 500 researchers this issues ~1001 queries and returns a 500 KB+ payload.

**Fix:** Add `page`/`per_page` query params matching the publications endpoint pattern. Also resolves the security enumeration concern (L-5).

---

## Issue 20: Synchronous blocking I/O in scraper

**Status:** Open
**Severity:** High
**Files:** `html_fetcher.py:100-131`, `scheduler.py:60-79`
**Ref:** `PERFORMANCE_ANALYSIS.md` § IO-2

Scraper processes URLs sequentially with `requests` (synchronous) + 2 s rate-limit sleep per URL. With 100 URLs across 20 domains the minimum scrape time is ~200 s. Parallelising across domains (one thread per domain, sequential within) could reduce this to ~10–20 s.

**Fix:** Use `concurrent.futures.ThreadPoolExecutor` with domain-grouped batches. Requires fixing CC-2 (Issue 28) first to make `_domain_last_request` thread-safe.

---

## Issue 21: Race condition in manual scrape trigger

**Status:** Open
**Severity:** High
**Files:** `api.py:347-353`
**Ref:** `PERFORMANCE_ANALYSIS.md` § CC-1 | `SECURITY_AUDIT.md` § H-2

The endpoint acquires the lock, immediately releases it, then spawns a thread that re-acquires it. Two near-simultaneous requests can both pass the check and start concurrent scrape jobs — producing duplicate publications, double OpenAI costs, and two `scrape_log` rows.

**Fix:** Do not release the lock in the endpoint. Pass it to the thread and release in a `finally` block.

---

## Issue 22: SWR installed but unused — no client-side caching

**Status:** Open
**Severity:** High
**File:** `app/src/` (all content components)
**Ref:** `PERFORMANCE_ANALYSIS.md` § FE-1

`swr@^2.4.1` is a production dependency but is never imported. All data fetching uses `useEffect` + `fetch` with manual state, meaning every navigation triggers a full API round-trip, and multiple components requesting the same resource issue duplicate requests.

**Fix:** Replace `useEffect`/`fetch` patterns with SWR hooks (`useSWR`). Add typed wrapper hooks (`usePublications`, `useResearchers`, `useResearcher`) in `api.ts`. Expected: ~60% reduction in API calls, instant navigation for cached data.

---

## Issue 23: `NEXT_PUBLIC_API_URL` baked in at build time — Docker mismatch

**Status:** Open
**Severity:** Medium
**Files:** `app/src/lib/api.ts:8-9`, `docker-compose.yml:47`
**Ref:** `PERFORMANCE_ANALYSIS.md` § FE-2

`NEXT_PUBLIC_*` vars are inlined at build time. The Docker default (`http://localhost:8000`) gets baked into the bundle, so SSR fetches inside the container fail (no API on `localhost` there) and CSR only works on the build machine.

**Fix:** Add a `rewrites()` proxy in `next.config.mjs` mapping `/api/:path*` → `http://api:8000/api/:path*`, then use relative `/api/...` paths. Works for both SSR and CSR.

---

## Issue 24: Unbounded invalid JSON file dumps

**Status:** Open
**Severity:** Medium
**File:** `publication.py:168-180`
**Ref:** `PERFORMANCE_ANALYSIS.md` § MEM-1 | `SECURITY_AUDIT.md` § L-7

On every unparseable OpenAI response a new file is written to `invalid_json_dumps/` with no rotation or cleanup. At 10% failure rate over 100 URLs/day this produces ~3,600 files/year. Files may contain scraped content and are not excluded from `.dockerignore`.

**Fix:** Implement a max-N rotating dump (cap at 50 files, delete oldest on overflow). Also add `invalid_json_dumps/` to `.dockerignore`.

---

## Issue 25: Unbounded publication accumulation in frontend state

**Status:** Open
**Severity:** Medium
**File:** `app/src/app/NewsfeedContent.tsx:45-49`
**Ref:** `PERFORMANCE_ANALYSIS.md` § MEM-2

"Load more" appends all publications into a single `useState` array that never shrinks. A new `Set` is created from all existing IDs on each page load (O(n) per load). After many pages, thousands of DOM nodes accumulate.

**Fix:** Switch to page-based navigation (replaces current page rather than accumulating), or implement virtual scrolling with `react-window` / `@tanstack/virtual`.

---

## Issue 26: Single-worker uvicorn in production

**Status:** Open
**Severity:** Medium
**File:** `Dockerfile.api:16`
**Ref:** `PERFORMANCE_ANALYSIS.md` § SC-1

Default single-worker uvicorn + synchronous MySQL driver means one blocking DB call holds the entire event loop. Under concurrent load, requests queue serially.

**Fix:** Use Gunicorn with uvicorn workers (`--workers 4`). Note: `APScheduler` and `threading.Lock` are process-local, so scheduler must move to a separate process or use a DB-backed distributed lock when running multiple workers.

---

## Issue 27: No `Cache-Control` headers on API responses

**Status:** Open
**Severity:** Medium
**File:** `api.py` (all endpoints)
**Ref:** `PERFORMANCE_ANALYSIS.md` § CA-1

All endpoints return responses without `Cache-Control`, `ETag`, or `Last-Modified` headers. Data changes at most once per scrape cycle (~24 h), yet every navigation hits the API.

**Fix:** Add `Cache-Control: public, max-age=300, stale-while-revalidate=600` to list endpoints. Pair with SWR adoption (Issue 22) for full effect.

---

## Issue 28: Class-level mutable state on `HTMLFetcher`

**Status:** Open
**Severity:** Medium
**Files:** `html_fetcher.py:23-29`
**Ref:** `PERFORMANCE_ANALYSIS.md` § CC-2 | `SECURITY_AUDIT.md` § H-5

`HTMLFetcher.session` and `_domain_last_request` are class-level attributes shared across threads. Currently safe with single-threaded scraping, but if Issue 20's parallelism is adopted, concurrent writes to `_domain_last_request` create a data race. `requests.Session` is also not formally thread-safe for concurrent use.

**Fix:** Protect `_domain_last_request` with `threading.Lock`. Create a new `requests.Session` per scrape job invocation.

---

## Issue 29: No database connection retry at startup

**Status:** Open
**Severity:** Low
**Files:** `api.py:44-48`, `database.py:34-43`
**Ref:** `PERFORMANCE_ANALYSIS.md` § SC-2

`lifespan` calls `Database.create_tables()` immediately. If MySQL is not yet ready (common in Docker Compose despite `depends_on: service_healthy`), the app crashes without retrying.

**Fix:** Wrap `create_tables()` in a retry loop with exponential backoff (max 10 attempts).

---

## Issue 30: `create_tables()` opens 6 separate connections

**Status:** Open
**Severity:** Low
**File:** `database.py:168-170`
**Ref:** `PERFORMANCE_ANALYSIS.md` § DB-5

Each of the 6 table DDL statements goes through `execute_query()`, opening and closing its own connection. Wasteful but only happens once at startup.

**Fix:** Execute all DDL within a single shared connection at startup.

---

## Issue 31: `robots.txt` fetched on every scrape without caching

**Status:** Open
**Severity:** Low
**File:** `html_fetcher.py:32-46`
**Ref:** `PERFORMANCE_ANALYSIS.md` § IO-3

`is_allowed_by_robots()` creates a new `RobotFileParser`, fetches, and parses `robots.txt` on every URL check. For a domain with 10 researcher URLs, `robots.txt` is fetched 10 times per scrape cycle.

**Fix:** Cache parsed `RobotFileParser` instances per domain with a 1-hour TTL in a class-level dict.

---

## Issue 32: No Next.js standalone output configured

**Status:** Open
**Severity:** Low
**File:** `app/next.config.mjs`
**Ref:** `PERFORMANCE_ANALYSIS.md` § FE-3

`next.config.mjs` is empty (`{}`). The `output: "standalone"` option reduces the Docker image from ~1 GB (full `node_modules`) to ~100–150 MB and improves container startup time.

**Fix:** Add `output: "standalone"` to `nextConfig`. Update `app/Dockerfile` to copy from `.next/standalone` as the `standalone` output requires.

---

# Security Issues

*Full findings, attack scenarios, and remediation code in `SECURITY_AUDIT.md`.*

---

## Issue 33: Live OpenAI API key in `.env` file on disk

**Status:** Open — **rotate immediately**
**Severity:** Critical (CVSS 9.1)
**File:** `.env:12`
**Ref:** `SECURITY_AUDIT.md` § C-1

The `.env` file contains a live OpenAI API key in plaintext. While excluded from git, it is present on disk and could be leaked via `COPY . .` in Docker if `.dockerignore` is misconfigured, or by any developer/process with filesystem access.

**Fix:** Rotate the key at https://platform.openai.com/api-keys immediately. Use a secrets manager for production. Add `detect-secrets` / `gitleaks` as a pre-commit hook.

---

## Issue 34: SQL identifier injection in `create_database()` and Makefile

**Status:** Open
**Severity:** Critical (CVSS 9.8)
**Files:** `database.py:24`, `Makefile:21`
**Ref:** `SECURITY_AUDIT.md` § C-2

The database name from `DB_NAME` env var is interpolated directly into DDL without validation: `f"CREATE DATABASE IF NOT EXISTS {db_config['database']}"`. An attacker who controls `DB_NAME` can inject arbitrary SQL.

**Fix:** Validate the name against `^[a-zA-Z_][a-zA-Z0-9_]{0,63}$` before use and wrap in backticks. Apply the same fix to the Makefile `reset-db` target.

---

## Issue 35: API key authentication vulnerable to timing attack

**Status:** Open
**Severity:** High (CVSS 7.5)
**File:** `api.py:343`
**Ref:** `SECURITY_AUDIT.md` § H-1

`api_key != scrape_api_key` short-circuits on the first differing byte, leaking timing information. An attacker can progressively brute-force the key character-by-character via latency measurement, reducing a 32-character key from 62^32 to ~1,984 guesses.

**Fix:** Replace with `hmac.compare_digest(api_key, scrape_api_key)`.

---

## Issue 36: Weak default passwords and credentials in Docker Compose

**Status:** Open
**Severity:** High (CVSS 7.3)
**File:** `docker-compose.yml:5-8,33`
**Ref:** `SECURITY_AUDIT.md` § H-3, H-6

Fallback defaults `rootsecret`, `secret`, and `changeme` are active if environment variables are not set. MySQL port 3306 is exposed externally, allowing direct connection with default credentials. `SCRAPE_API_KEY=changeme` effectively makes the scrape endpoint unauthenticated.

**Fix:** Remove all defaults (`${VAR:?VAR is required}`). Do not expose port 3306 externally. Add startup validation rejecting known weak keys.

---

## Issue 37: Overly inclusive `.dockerignore` — secrets leakage risk

**Status:** Open
**Severity:** High (CVSS 7.0)
**File:** `Dockerfile.api:11`, `.dockerignore`
**Ref:** `SECURITY_AUDIT.md` § H-4

`COPY . .` copies the entire project root. Current exclusions cover `.env` and `.git` but not `invalid_json_dumps/`, `researchers.md`, `urls.csv`, or `*.log` files.

**Fix:** Convert `.dockerignore` to a whitelist approach — ignore `*`, then explicitly allow only the Python source files needed.

---

## Issue 38: SSRF DNS rebinding bypass

**Status:** Open
**Severity:** Medium (CVSS 6.4)
**File:** `html_fetcher.py:49-85`
**Ref:** `SECURITY_AUDIT.md` § M-1

`validate_url()` resolves the hostname and checks for private IPs, but the actual `requests.get()` call re-resolves DNS independently. An attacker controlling a domain can serve a public IP on first resolve (passes validation) and a private IP (e.g., `169.254.169.254`) on second resolve.

**Fix:** Resolve the hostname once, validate the IP, then pin the resolved IP for the actual HTTP request using a custom transport adapter.

---

## Issue 39: Error handlers leak internal exception details

**Status:** Open
**Severity:** Medium (CVSS 5.3)
**File:** `api.py:89-136`
**Ref:** `SECURITY_AUDIT.md` § M-2

The 400/401/409/422 handlers include `str(exc.detail) if hasattr(exc, 'detail') else str(exc)` in responses. For non-`HTTPException` errors, `str(exc)` can expose SQL fragments, DB connection strings, or filesystem paths.

**Fix:** Only forward `.detail` for `HTTPException` instances; return a generic message for all others. Add a global catch-all `Exception` handler.

---

## Issue 40: No rate limiting on public GET endpoints

**Status:** Open
**Severity:** Medium (CVSS 5.3)
**File:** `api.py` (all `@app.get` routes)
**Ref:** `SECURITY_AUDIT.md` § M-3

All read endpoints are unauthenticated and unthrottled. Combined with N+1 queries (Issue 16), a flood of requests to `/api/researchers` exhausts the MySQL connection pool, causing DoS.

**Fix:** Add `slowapi` rate limiting middleware (e.g. `60/minute` per IP on list endpoints).

---

## Issue 41: No authentication on read endpoints — enumeration risk

**Status:** Open — accepted risk if intentionally public
**Severity:** Medium (CVSS 4.3)
**File:** `api.py:183-331`
**Ref:** `SECURITY_AUDIT.md` § M-4

All GET endpoints are public. Sequential integer IDs make full researcher/publication enumeration trivial. If this is intentional (public aggregator), document as accepted risk; otherwise add read auth or switch to UUIDs.

---

## Issue 42: Frontend missing Content-Security-Policy and security headers

**Status:** Open
**Severity:** Medium (CVSS 4.7)
**File:** `app/next.config.mjs`
**Ref:** `SECURITY_AUDIT.md` § M-5

`next.config.mjs` is empty. The API's security headers only apply to API responses, not to frontend pages served by Next.js.

**Fix:** Add a `headers()` export to `next.config.mjs` setting `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, and `Content-Security-Policy` for all routes.

---

## Issue 43: Unpinned dependency versions allow unvetted patch installs

**Status:** Open
**Severity:** Medium (CVSS 5.0)
**Files:** `requirements.txt`, `app/package.json`
**Ref:** `SECURITY_AUDIT.md` § M-6

Python deps use `==X.Y.*` wildcards; frontend uses `^` ranges. Exact installed versions are non-deterministic without lock files being enforced in CI. Notable: `mysql-connector-python` and `beautifulsoup4` parse untrusted external content.

**Fix:** Pin to exact versions in `requirements.txt`. Run `pip-audit` / `npm audit` in CI. Consider Dependabot or Renovate for automated update PRs.

---

## Issue 44: No TLS/HTTPS enforcement

**Status:** Open
**Severity:** Medium (CVSS 5.9)
**File:** `docker-compose.yml`
**Ref:** `SECURITY_AUDIT.md` § M-7

API (port 8000) and frontend (port 3000) serve over plain HTTP. The `X-API-Key` header and all data are transmitted unencrypted. The `HSTS` header has no effect without a prior HTTPS connection.

**Fix:** Add a TLS-terminating reverse proxy (Caddy or nginx with Let's Encrypt). Remove the external MySQL port mapping.

---

## Issue 45: Logging may expose credentials and SQL fragments

**Status:** Open
**Severity:** Medium (CVSS 4.0)
**Files:** `database.py:27,42,58,73,87`, `html_fetcher.py`, `publication.py:147`
**Ref:** `SECURITY_AUDIT.md` § M-8

`logging.error(f"...: {e}")` can emit MySQL auth errors (containing usernames), full SQL query strings, or OpenAI API error messages (which may include key fragments).

**Fix:** Sanitize exception messages before logging. Use structured logging with explicit fields. Set production log level to WARNING and above.

---

## Issue 46: `datetime.utcnow()` deprecated — naive/aware datetime mismatch

**Status:** Open
**Severity:** Medium (CVSS 3.7)
**Files:** `html_fetcher.py:164`, `scheduler.py:29,42`
**Ref:** `SECURITY_AUDIT.md` § M-9

`datetime.utcnow()` returns a naive datetime (no timezone). Deprecated in Python 3.12. Mixed with `datetime.now(timezone.utc)` (used in `api.py:356`), comparison operations can raise `TypeError`.

**Fix:** Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)`.

---

## Issue 47: Insufficient security test coverage

**Status:** Open
**Severity:** Medium (CVSS 4.0)
**File:** `tests/`
**Ref:** `SECURITY_AUDIT.md` § M-10

No tests cover: SQL injection in query params, SSRF bypass attempts, timing attack resistance on API key auth, oversized inputs, scrape lock races, or that error responses don't leak internals.

**Fix:** Add a `TestSecurityInputValidation` class covering the above scenarios.

---

## Issue 48: CORS `allow_methods=["*"]` is overly permissive

**Status:** Open
**Severity:** Low (CVSS 3.1)
**File:** `api.py:66`
**Ref:** `SECURITY_AUDIT.md` § L-1

All HTTP methods are permitted via CORS, but the API only uses GET and POST.

**Fix:** Restrict to `["GET", "POST", "OPTIONS"]` and enumerate explicit allowed headers.

---

## Issue 49: Missing `Referrer-Policy` and `Permissions-Policy` headers on API

**Status:** Open
**Severity:** Low (CVSS 3.1)
**File:** `api.py:74-81`
**Ref:** `SECURITY_AUDIT.md` § L-2

The security headers middleware omits `Referrer-Policy` and `Permissions-Policy`.

**Fix:** Add `Referrer-Policy: strict-origin-when-cross-origin` and `Permissions-Policy: camera=(), microphone=(), geolocation=()`.

---

## Issue 50: Hardcoded `User-Agent` enables fingerprinting

**Status:** Open
**Severity:** Low (CVSS 2.0)
**File:** `html_fetcher.py:24-25`
**Ref:** `SECURITY_AUDIT.md` § L-3

`Mozilla/5.0 (compatible; HTMLFetcher/1.0)` uniquely identifies the scraper to target websites, enabling targeted blocking or exploitation.

**Fix:** Make the User-Agent configurable via an env var. Consider rotating from a pool of standard browser strings.

---

## Issue 51: `db_config.py` calls `sys.exit(1)` at import time

**Status:** Open
**Severity:** Low (CVSS 2.5)
**File:** `db_config.py:10-16`
**Ref:** `SECURITY_AUDIT.md` § L-6

Missing env vars cause `sys.exit(1)` at module import, making the module untestable without a full environment. Test `conftest.py` works around this by pre-setting env vars, but it's fragile.

**Fix:** Raise `EnvironmentError` with a descriptive message instead of calling `sys.exit()`.

---

## Issue 52: Frontend production dependency version ranges

**Status:** Open
**Severity:** Low (CVSS 2.0)
**File:** `app/package.json`
**Ref:** `SECURITY_AUDIT.md` § L-8

`react: "^18"`, `swr: "^2.4.1"`, and similar caret ranges in production dependencies mean `npm update` can silently pull in new minor versions.

**Fix:** Pin all production dependencies to exact versions. Use `package-lock.json` enforcement in CI (`npm ci` not `npm install`).




Small change to html without material difference immediately reclassifies  sends everything to openai, which could be expensive.