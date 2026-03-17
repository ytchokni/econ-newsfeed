# Econ Newsfeed — Implementation Tracks

Four tracks, three of which run in parallel. Each track lists its tasks in implementation order, the files it touches, and its dependencies.

```
Timeline:

Track 1 (Backend Data Layer)  ████████████████████████
Track 2 (Frontend)            ████████████████████████
Track 3 (Deployment)          ████████████████
                                     ↓
Track 4 (API Layer)                  ████████████████
                                               ↓
                              Integration test  ████
```

---

## Track 1: Backend Data Layer

**Scope:** DESIGN.md Phases 1 + 4 + 5 merged — schema fixes, pipeline hardening, scheduler, and scraping safety.

**Files touched:** `database.py`, `db_config.py`, `html_fetcher.py`, `publication.py`, `researcher.py`, `scheduler.py` (new), `requirements.txt`, `.env.example` (new)

**Can start:** Immediately

**Depends on:** Nothing

### Phase A: Schema & Data Layer Fixes

1. **Widen URL columns** — Change `VARCHAR(255)` → `VARCHAR(2048)` on `researcher_urls.url`, `publications.url`, and `html_content.url` in `database.py` `create_tables()`.

2. **Remove redundant `html_content.url` column** — Drop the `url` column from `html_content` table definition. The URL is derivable via `url_id → researcher_urls.url`.

3. **Add `UNIQUE(url_id)` on `html_content`** — Enforces one-row-per-URL. Migrate `save_text()` to use `INSERT ... ON DUPLICATE KEY UPDATE` (upsert) instead of always inserting.

4. **Add `UNIQUE(title(200), url(200))` on `publications`** — Prevents duplicate publications on re-extraction.

5. **Add secondary indexes** — `idx_name(last_name, first_name)` on `researchers`, `idx_timestamp(timestamp)` on `publications`, `idx_url_id_ts(url_id, timestamp)` on `html_content`, `idx_researcher(researcher_id)` and `idx_publication(publication_id)` on `authorship`.

6. **Add `scrape_log` table** — New table with columns: `id`, `started_at`, `finished_at`, `status` (enum: running/completed/failed), `urls_checked`, `urls_changed`, `pubs_extracted`, `error_message`.

### Phase B: Pipeline Fixes

7. **Create `.env.example`** — Document all env vars from DESIGN.md Section 8.1 with comments and safe defaults.

8. **Add startup env var validation in `db_config.py`** — Fail fast with a clear error if `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, or `OPENAI_API_KEY` are missing or empty.

9. **Fix `RETURNING` clause in `researcher.py`** — `add_researcher()` and `add_url_to_researcher()` use `RETURNING id` which is PostgreSQL syntax. Replace with standard `INSERT` + `cursor.lastrowid` (MySQL compatible). Also add the missing `page_type` parameter to `add_url_to_researcher()`.

10. **Fix `fetch_and_save_if_changed()` return value** — Currently returns `None`. Change to return `True` when content changed and was saved, `False` otherwise. This is needed for the scheduler to know when to trigger extraction.

11. **Eliminate duplicate HTML parsing in `has_text_changed()`** — Currently `has_text_changed()` parses HTML and extracts text, then `save_text()` does it again. Refactor so text is extracted once and passed through.

12. **Add Pydantic models for LLM output validation** — Define `ExtractedPublication` and `ExtractedPublicationList` Pydantic models. Validate OpenAI response against these before database insertion. Reject malformed or missing fields rather than crashing.

13. **Instantiate OpenAI client once at module level** — Move `OpenAI(api_key=...)` out of `extract_publications()` into a module-level singleton to reuse the HTTP connection pool.

14. **Make OpenAI model configurable** — Read from `OPENAI_MODEL` env var (default `gpt-4o-mini`). Currently hardcoded to `gpt-3.5-turbo`.

15. **Add publication deduplication before insert** — Before inserting a publication, check for an existing row with matching normalized title (lowercased, stripped) + source URL. Skip if duplicate. This works alongside the DB unique constraint as a belt-and-suspenders approach.

16. **Pin all dependencies in `requirements.txt`** — Add version pins. Add `fastapi`, `uvicorn[standard]`, `apscheduler`, `pydantic`. Remove duplicate `python-dotenv` if present.

### Phase C: Scraping & Scheduler

17. **Add URL validation (SSRF protection) to `html_fetcher.py`** — Before fetching, reject non-HTTP(S) schemes, private/internal IPs (10.x, 172.16-31.x, 192.168.x, 169.254.x, 127.x), and AWS metadata endpoints (169.254.169.254).

18. **Add `robots.txt` compliance** — Check `robots.txt` before fetching each URL. Skip URLs disallowed for the scraper's User-Agent. Use `urllib.robotparser`.

19. **Add per-domain rate limiting** — Track last request time per domain. Wait `SCRAPE_RATE_LIMIT_SECONDS` (default 2) between requests to the same domain.

20. **Add exponential backoff for retries** — Currently retries only on timeout. Extend to retry on 5xx errors with exponential backoff (1s, 2s, 4s). Max 3 retries.

21. **Add content size limit** — Reject HTTP responses larger than 1 MB before processing. Log a warning when rejecting.

22. **Add configurable content truncation** — Use `CONTENT_MAX_CHARS` env var (default 4000) instead of hardcoded `[:4000]` slice. Log when truncation occurs and how many characters were dropped.

23. **Add diff-based extraction** — When content changes, compute `difflib.unified_diff` between old and new text. Send only new/changed lines to the LLM instead of the full page. Fall back to full text if no previous content exists.

24. **Add `get_previous_text()` to `HTMLFetcher`** — Query to retrieve the text content that was stored before the latest upsert, needed for diff computation. (With the upsert model, this means reading before writing.)

25. **Create `scheduler.py`** — Implement `run_scrape_job()` with:
    - Threading lock to prevent concurrent scrapes
    - `scrape_log` creation and updates
    - Iteration over all researcher URLs
    - Change detection → diff → extraction → save pipeline
    - Error handling with log update on failure

26. **Integrate scheduler with APScheduler** — Configure `BackgroundScheduler` with `SCRAPE_INTERVAL_HOURS`. Support `SCRAPE_ON_STARTUP` flag.

### Verification

- Run `python database.py` — tables created with new schema, no errors
- Run `python main.py` — CLI pipeline works end-to-end with all fixes applied
- Manually verify: re-running extraction on same page does not create duplicate publications
- Verify `.env.example` contains all documented env vars

---

## Track 2: Frontend

**Scope:** DESIGN.md Phase 3 — Next.js newsfeed UI, researcher pages, components.

**Files touched:** Everything under `app/` — `package.json`, `src/app/page.tsx`, `src/app/layout.tsx`, `src/app/globals.css`, `src/app/researchers/page.tsx`, `src/app/researchers/[id]/page.tsx`, `src/lib/api.ts` (new)

**Can start:** Immediately (API contract is defined in DESIGN.md Section 4)

**Depends on:** Nothing (works against API contract; can use mock data for development)

### Phase A: Setup & Dependencies

1. **Upgrade Next.js to latest 14.x patch** — Addresses CVE-2024-46982 (cache poisoning), CVE-2024-51479 (auth bypass), CVE-2025-29927 (middleware bypass).

2. **Remove `@ts-morph/common`** — Spurious dependency, not used by the application.

3. **Install SWR** — `npm install swr` for client-side data fetching with stale-while-revalidate caching.

4. **Configure Tailwind** — Ensure `tailwind.config.ts` covers all `src/` paths. Set up a minimal design token system (colors, spacing) for consistency.

### Phase B: API Client & Types

5. **Create `src/lib/api.ts`** — API client with typed functions:
   - `getPublications(page, perPage, filters)` → `PaginatedResponse<Publication>`
   - `getResearchers()` → `Researcher[]`
   - `getResearcher(id)` → `ResearcherDetail`
   - Base URL from `NEXT_PUBLIC_API_URL` env var
   - Error handling that returns typed error objects

6. **Define TypeScript interfaces** — `Publication`, `Author`, `Researcher`, `ResearcherDetail`, `PaginatedResponse<T>`, `ApiError`. Match the API response shapes from DESIGN.md Section 4 exactly.

### Phase C: Layout & Navigation

7. **Update `src/app/layout.tsx`** — Root layout with:
   - Header component: logo/title ("Econ Newsfeed"), nav links (Feed, Researchers)
   - Clean, academic-feeling typography (system fonts, readable line height)
   - Responsive layout (single column on mobile, centered max-width on desktop)

8. **Update `src/app/globals.css`** — Minimal Tailwind base styles. No heavy theming — keep it clean and readable.

### Phase D: Newsfeed Page

9. **Build `PublicationCard` component** — Displays: title, author list (linked to researcher pages), venue + year, discovery date. Clean card design with subtle borders.

10. **Replace `src/app/page.tsx` with newsfeed** — Fetches `GET /api/publications?page=1&per_page=20`. Groups publications by discovery date with date headers. Uses SWR for caching.

11. **Add "Load more" pagination** — Button at bottom that appends the next page of results. Tracks current page in state. Disables when no more pages.

### Phase E: Researcher Pages

12. **Create `src/app/researchers/page.tsx`** — Directory of all tracked researchers. Each card shows: name, position, affiliation, publication count. Each card links to `/researchers/[id]`.

13. **Create `src/app/researchers/[id]/page.tsx`** — Researcher detail page. Shows researcher profile (name, position, affiliation, tracked URLs) and their publications using the same `PublicationCard` component.

### Phase F: Polish

14. **Add loading states** — Skeleton loaders or spinners for data fetching states.

15. **Add error states** — User-friendly error messages when API calls fail (both network errors and API error responses).

16. **Add empty states** — "No publications yet" / "No researchers found" messages when data is empty.

### Verification

- `npm run build` succeeds with zero TypeScript errors
- All three pages render correctly with mock/sample data
- Navigation between pages works
- "Load more" pagination works
- Responsive on mobile and desktop
- No console errors or warnings

---

## Track 3: Deployment

**Scope:** DESIGN.md Phase 6 — Docker, Compose, developer tooling.

**Files touched:** `Dockerfile.api` (new), `app/Dockerfile` (new), `docker-compose.yml` (new), `Makefile` (new)

**Can start:** Immediately

**Depends on:** Nothing for scaffolding. Final integration testing depends on Tracks 1 + 2 + 4.

### Phase A: Dockerfiles

1. **Create `Dockerfile.api`** — Multi-stage Python build:
   - Base: `python:3.12-slim`
   - Install dependencies from `requirements.txt`
   - Copy application code
   - Run with `uvicorn api:app --host 0.0.0.0 --port 8000`
   - Non-root user for security

2. **Create `app/Dockerfile`** — Multi-stage Next.js build:
   - Stage 1: `node:18-alpine` — install deps + build
   - Stage 2: `node:18-alpine` — copy standalone output, run with `next start`
   - Non-root user for security

### Phase B: Docker Compose

3. **Create `docker-compose.yml`** — Three services:
   - `db`: MySQL 8 with volume mount, non-root user (`MYSQL_USER`/`MYSQL_PASSWORD`), health check
   - `api`: Builds from `Dockerfile.api`, depends on `db` (with health check wait), environment variables scoped per DESIGN.md Section 10.1
   - `frontend`: Builds from `app/Dockerfile`, depends on `api`
   - Named volume for MySQL data persistence

4. **Add `.dockerignore` files** — For both root and `app/` directories. Exclude `.git`, `node_modules`, `.env`, `__pycache__`, `.venv`, etc.

### Phase C: Developer Tooling

5. **Create `Makefile`** with targets:
   - `make setup` — Copy `.env.example` → `.env` (if not exists), `docker compose up -d db`, wait for MySQL, `python database.py`
   - `make dev` — Start API + frontend in development mode (local, not Docker)
   - `make seed` — Run `python database.py` to create tables and import `urls.csv`
   - `make reset-db` — Drop and recreate database + tables
   - `make docker-up` — `docker compose up --build`
   - `make docker-down` — `docker compose down`

6. **Add seed script logic** — On first run, if the `researchers` table is empty, auto-import from `urls.csv`. This makes `docker compose up` work out of the box.

### Verification

- `docker compose up --build` starts all three services
- MySQL health check passes before API starts
- API responds at `http://localhost:8000/docs`
- Frontend responds at `http://localhost:3000`
- `make setup && make dev` works for local development
- Data persists across `docker compose down` / `up` cycles (volume mount)

---

## Track 4: API Layer

**Scope:** DESIGN.md Phase 2 — FastAPI application with all REST endpoints.

**Files touched:** `api.py` (new)

**Can start:** After Track 1 Phase B is complete (pipeline fixes that stabilize the data layer)

**Depends on:** Track 1 (schema fixes, data layer functions, scheduler)

### Phase A: App Setup

1. **Create `api.py` with FastAPI app** — Basic app with lifespan handler that:
   - Validates required env vars on startup
   - Runs `Database.create_tables()` on startup
   - Starts the APScheduler background scheduler (from Track 1's `scheduler.py`)
   - Shuts down scheduler on app shutdown

2. **Add CORS middleware** — Allow only `FRONTEND_URL` (default `http://localhost:3000`). No wildcard origins.

3. **Add security headers middleware** — `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'self'`, `Strict-Transport-Security` (when behind TLS).

4. **Add standard error response envelope** — All errors return `{"error": {"code": "...", "message": "..."}}`. Add exception handlers for 400, 401, 404, 409, 422, 500.

### Phase B: Read Endpoints

5. **Implement `GET /api/publications`** — Paginated (page/per_page), sorted by `timestamp` DESC. Optional filters: `year`, `researcher_id`. Response includes authors via JOIN through `authorship`. Matches DESIGN.md Section 4.1 response shape exactly.

6. **Implement `GET /api/publications/{id}`** — Single publication with full author details. Returns 404 if not found.

7. **Implement `GET /api/researchers`** — All researchers with their URLs and publication counts. Publication count via COUNT on `authorship` table.

8. **Implement `GET /api/researchers/{id}`** — Single researcher with profile + their publications. Returns 404 if not found.

### Phase C: Scrape Endpoints

9. **Implement `POST /api/scrape`** — Requires `X-API-Key` header matching `SCRAPE_API_KEY` env var. Returns 401 if missing/invalid. Returns 409 if a scrape is already running (check threading lock from `scheduler.py`). Otherwise, starts scrape in background thread and returns 201 with `scrape_id` and `status: running`.

10. **Implement `GET /api/scrape/status`** — Returns most recent `scrape_log` entry + next scheduled scrape time + interval. No auth required (read-only status info).

### Phase D: Pydantic Response Models

11. **Define response models** — Pydantic models for all response shapes: `PublicationResponse`, `AuthorResponse`, `ResearcherResponse`, `PaginatedResponse`, `ScrapeStatusResponse`, `ErrorResponse`. Use these as FastAPI `response_model` for automatic serialization and OpenAPI docs.

### Verification

- `uvicorn api:app --reload` starts without errors
- `http://localhost:8000/docs` shows all endpoints with correct schemas
- `GET /api/publications` returns paginated results with authors
- `GET /api/researchers` returns researchers with publication counts
- `POST /api/scrape` without API key returns 401
- `POST /api/scrape` with valid key returns 201 and triggers background scrape
- `GET /api/scrape/status` returns last scrape info
- CORS headers present on responses
- Security headers present on all responses

---

## Integration Checklist (after all tracks complete)

- [ ] Frontend fetches real data from API (not mocked)
- [ ] Newsfeed shows publications with correct authors and dates
- [ ] Researcher pages show correct publication counts
- [ ] "Load more" pagination works end-to-end
- [ ] Manual scrape trigger from API creates new publications that appear in feed
- [ ] Scheduled scrape runs automatically after configured interval
- [ ] `docker compose up` brings up the full stack from scratch
- [ ] Duplicate publications are not created on re-scrape
- [ ] Private/internal URLs are rejected by the scraper
- [ ] API returns proper error responses for all error cases
