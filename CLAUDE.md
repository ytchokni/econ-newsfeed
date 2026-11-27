# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Econ Newsfeed monitors economics researchers' personal websites, detects new/changed publications via LLM extraction (Google AI Studio/Gemini), and displays them in a chronological newsfeed. Full-stack monorepo: Python backend (FastAPI) + Next.js frontend + MySQL.

## Commands

```bash
# Setup
poetry install && cd app && npm install

# Development
poetry run uvicorn backend.api:app --reload --port 8001  # API on :8001
cd app && API_INTERNAL_URL=http://localhost:8001 npm run dev  # Frontend on :3000

# Database
poetry run python -c "from backend.database import create_database, create_tables; create_database(); create_tables()"  # seed (idempotent)
./scripts/sync_prod_db.sh  # DESTRUCTIVE: replace local DB with latest prod backup

# Scraping pipeline
poetry run python -c "from backend.pipeline.scheduler import run_scrape_job; run_scrape_job()"  # Fetch-only: download HTML + draft URL validation
poetry run python -m backend.main extract          # LLM extraction for URLs with pending changes
poetry run python -m backend.main extract --limit 5  # Extract a limited batch

# Enrichment & classification
poetry run python -m backend.main enrich            # Enrich papers via OpenAlex
poetry run python -m backend.main classify-jel      # One budgeted JEL batch (bio + papers + OpenAlex topics)
poetry run python -m backend.main classify-jel --until-done  # Repeat batches until nothing is due, a limit is hit, or extraction needs the key
poetry run python -m backend.main discover-domains  # Scan HTML for untrusted domains with paper-like links

# Testing & validation
poetry run python scripts/check_env.py && poetry run pytest && cd app && npx tsc --noEmit && npm run lint && npx jest  # Full suite
poetry run pytest tests_data_quality -v             # Data-quality invariants against real DB
DATA_QUALITY_LIVE=1 poetry run pytest tests_data_quality -v  # + pipeline-liveness checks (server only)
poetry run python scripts/data_quality_check.py run --out dq.json  # suite + signals as JSON (read-only session)
python3 scripts/data_quality_check.py report dq.json --offline     # preview the daily issue summary
poetry run pytest                                   # All Python tests
poetry run pytest tests/test_api_publications.py    # Single test file
poetry run pytest -k test_name                      # Single test by name
cd app && npx jest                                  # All frontend tests
cd app && npx jest --testPathPattern=ComponentName  # Single frontend test

# One-time scripts
poetry run python scripts/backfill_paper_links.py   # Populate paper_links from stored HTML + enrich
poetry run python scripts/cleanup_data_quality.py   # Fix rows flagged by data-quality checks (dry-run; add --apply to write)
poetry run python scripts/recompute_has_top5.py     # Recompute researchers.has_top5_pub (dry-run; add --apply to write)
poetry run python scripts/reverify_openalex_ids.py --max-requests 300 --apply  # #266: re-check pre-15-Oct OpenAlex IDs (~900 budgeted requests: pause ENRICHMENT_WORKER_ENABLED or raise OPENALEX_DAILY_BUDGET for ~3 days; DB-cached, resumable; --status, --undo, --purge-cache)
poetry run python scripts/merge_renamed_papers.py   # #265: merge renames sharing an SSRN/NBER/arXiv/DOI link (dry-run; --apply)
poetry run python scripts/repair_researcher_identity.py  # Merge split researchers, route page papers to owners, deactivate unowned pages (dry-run; --apply)

# Docker
docker compose up         # Full stack (db + api + frontend)

# Production (Hetzner)
./scripts/deploy.sh       # Pull latest; rebuild API + miner; verify revision and DB readiness
./scripts/backup.sh       # Manual DB backup (also runs daily via cron at 3am UTC)
```

### Pipeline Details

**Scrape job** (`run_scrape_job()`) is **fetch-only**:
1. **Fetch phase** (all URLs): Download HTML for all researcher URLs, skip unchanged (content hash).
2. **Draft URL validation**: HEAD-request validation of `draft_url` fields

Extraction is owned by the extraction worker (below) — the scrape job only refreshes stored HTML and the `content_hash ≠ extracted_hash` queue.

**Enrichment worker:** When `ENRICHMENT_WORKER_ENABLED=true`, a background thread in the API process continuously enriches unenriched papers via OpenAlex. Polls every 5 minutes, processes batches of 50, respects the daily budget. Backs off to 10-minute sleep after 5 consecutive failures. In local dev (worker disabled), run `python -m backend.main enrich` manually after scraping.

**Extraction worker:** When `EXTRACTION_WORKER_ENABLED=true`, a background thread in the API process continuously extracts publications from changed pages (`content_hash ≠ extracted_hash`) using synchronous free-tier Gemma calls. Calls are latency-bound (45–190s each, ~35 output tokens/s); `EXTRACTION_DELAY_SECONDS` (default 2) paces the fast tail under the 30 RPM free-tier cap. Polls every 5 minutes when idle, backs off 10 minutes after 10 consecutive failures (quota exhaustion), and skips a URL for the process lifetime after 3 failed attempts (poison-pill guard). Account errors from the provider (HTTP 401/402/403, e.g. depleted AI Studio billing) pause the whole worker for 10 minutes per attempt instead of counting against individual pages. In local dev (worker disabled), run `python -m backend.main extract` manually after fetching.

**Extraction circuit breaker (CLI):** The `extract` CLI stops after 10 consecutive failed extractions (e.g. LLM quota exhausted). Fetched HTML is preserved — extraction resumes on the next run for URLs where `content_hash ≠ extracted_hash`. The continuous worker uses backoff + per-URL retry limits instead of stopping.

**JEL classification:** When `JEL_CLASSIFICATION_ENABLED=true`, a scheduler job
(every `JEL_CLASSIFICATION_INTERVAL_MINUTES`, default 60) classifies tracked
researchers into top-level JEL codes from their bio, up to 20 paper titles (3
abstracts) and OpenAlex topics (stored paper topics plus the author's topics).
Bios containing leaked `<thought>`/reasoning text are ignored. Only researchers
that are new, have changed evidence (`researcher_jel_state.input_hash`), or a
due retry are sent to the LLM; others are skipped without a call. It has its own
budget so extraction keeps priority on the shared free-tier key:
`JEL_CLASSIFICATION_BATCH_CALLS` (40) per run, `JEL_CLASSIFICATION_DAILY_LIMIT`
(600) per UTC day counted from `llm_usage`, `JEL_CLASSIFICATION_DELAY_SECONDS`
(20) between calls, and a run stops at the first quota refusal. It also yields
to extraction: extraction records every provider refusal in `llm_quota_signals`,
and JEL skips a run (or stops before its next call) within
`JEL_YIELD_AFTER_QUOTA_MINUTES` (15) of one. It also skips runs while more than
`JEL_MAX_EXTRACTION_BACKLOG` (1000) pages wait for extraction. The DB signal
works across processes, so the CLI yields too. When the LLM finds nothing, codes
mapped from OpenAlex topics are used. The `classify-jel` CLI runs the same
batches under the same advisory lock, daily budget and yield checks
(`--no-yield` skips the checks). Catch-up after a large backlog, run overnight
once the extraction backlog has cleared:
`nohup docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api python -m backend.main classify-jel --until-done --daily-limit 2000 --delay 30 > /root/jel-backfill.log 2>&1 &`

**Paper merge:** `merge_duplicate_papers()` dedupes papers post-run.

**Zombie scrape_log cleanup:** On scheduler start and hourly thereafter, `scrape_log` entries stuck in `'running'` are marked `'failed'` based on the scrape advisory lock: if no connection holds the lock, all running rows older than 5 minutes are zombies; if the lock is held, all but the newest running row are. Deploys that restart the container mid-scrape are swept on the next boot.

**Researcher identity:** `backend/database/identity.py` decides when two names are one person (`same_person`: initials, nicknames, accents, hyphen/space; `strong=True` adds typos and compound surnames and is only used with a shared OpenAlex ID or page ownership). An OpenAlex author ID stays on one record: `assign_openalex_author_id` merges a compatible holder or refuses. `merge_researcher_into` moves authorship, URLs, follows, JEL codes and the rest; feed events belong to papers and are never touched. When papers are saved from a page, a byline name that matches the page owner resolves to the owner. The owner is attached (author_order 0) only when the page names them and no unrelated researcher tracks it; otherwise extraction deactivates the URL with reason `shared_page` or `owner_not_named` (pages under 1000 characters and profile registries such as ORCID are not judged). Reactivating such a URL re-runs the check on the next content change.

Feed event integrity is protected by `_title_in_previous_snapshot()` and the `_url_has_baseline()` check in `backend.pipeline.extraction.extract_one_url()`.

Deployment waits for `/api/ready`, which checks the database, versioned snapshot
guard, and required schema. `/api/health` is process liveness only. Losing the
scheduler advisory-lock connection terminates that worker so its jobs cannot
continue alongside a new owner. OpenAlex paid-request reservations are stored
per UTC day in `enrichment_daily_budget` and survive restarts.

**Monitoring and alerts:** `.github/workflows/monitor.yml` runs once a day
(07:17 UTC) and sends at most one email per run. It probes
the public API, frontend and TLS certificate from the runner, then streams
`scripts/monitor.py` to the server over SSH (the deploy secrets) to check
containers, disk, backups, scrape recency, extraction throughput against the
backlog, feed freshness, enrichment, and LLM calls rejected for billing or
credentials. A failure opens one GitHub issue labelled `monitor-alert` and asks
Claude to fix it with an `@claude` comment (handled by `claude.yml`). The
repository owner is only mentioned, which is what sends the alert email, when
the checks still fail at the next daily run (the fix did not work or was not
merged); the issue closes itself on recovery. The `@claude` comment is posted
with the `MONITOR_GH_TOKEN` secret (a personal access token with Issues
read/write), because GitHub starts no workflow for comments made with the
workflow's own token; without that secret, or if it is rejected, the monitor
alerts the owner straight away. Every other call uses the workflow token so the
owner mention comes from a bot (a self-mention sends no email). The script is standard
library only because it runs on the server's system Python. Run it by hand with
`python3 scripts/monitor.py --external`, or `--host` on the server.

"Has Top-5" means a published or accepted paper in exactly AER, Econometrica,
JPE, QJE or REStud (sister journals, look-alikes and R&Rs do not count); the
"Top-20" preset matches department names as whole words. Both are regexes in
`backend/database/top_presets.py` that MySQL and Python apply identically.
Both feed and directory presets read `researchers.has_top5_pub`, refreshed for a
page's authors on save and in full after each scrape job's paper merge.

`.github/workflows/data-quality.yml` runs at 07:47 UTC: `tests_data_quality`
plus a few pipeline signals (`scripts/data_quality_check.py`) against prod
through an SSH tunnel as the SELECT-only `dq_readonly` MySQL user
(`DQ_DB_PASSWORD` secret), with the session forced read-only. Failures no open
issue tracks go to Claude (Sonnet, read-only tools), which answers in JSON; the
script then files at most 3 `bug` issues or comments on existing ones, posts one
comment on the rolling "Daily data-quality report" issue (its body holds the
invariant→issue state), and closes issues whose invariants passed on two
consecutive days. A fix that is merged but still waiting on its `--apply`
cleanup is listed as one "cleanup pending" line, not a new bug. Claude is
skipped when nothing new fails.

**Search engines:** the canonical host is `https://www.econnewsfeed.org`
(`SITE_URL` on Vercel overrides it). `next.config.mjs` 308-redirects pages on
`econ-newsfeed.vercel.app` and the apex to it (not `/api/*`, so auth callbacks
and unsubscribe POSTs keep working); preview deployments get `noindex` and a
`Disallow: /` robots.txt. Per-page titles, descriptions, canonicals and JSON-LD
come from `app/src/lib/seo.ts`. `/sitemap.xml` is an index over
`/sitemaps/{pages,researchers-N,papers-N}.xml`, built from `/api/sitemap` and
`/api/sitemap/{kind}?page=N` (10k ids per chunk). Researcher profiles are
indexed only if tracked (have a URL) and have papers; keep
`isIndexableResearcher` and `backend/database/sitemap.py` in sync. Detail
pages 404 only on a backend 404 and error (5xx) on other failures, so crawlers
retry. The sitemap fetches send `INTERNAL_API_KEY` without a visitor, so they
are exempt from rate limits and paging caps (see API scrape protection below).

Researcher filter choices are fetched on demand from `/api/researcher-options`
(at most 50 results), rather than included in `/api/filter-options`. Weekly
digest controls follow `/api/capabilities`; an unavailable delivery provider
cannot be enabled through settings. Digest events use ingestion timestamps and
a per-user delivery ledger so delayed extraction is not skipped.
Each digest carries `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058)
headers and a footer button. `GET /api/users/unsubscribe` only redirects to the
`/unsubscribe` confirmation page (link scanners prefetch GETs); `POST` unsubscribes
with no login. Tokens are HMAC-signed with `NEXTAUTH_SECRET`, never expire, and
only toggle that user's digest, so rotating the secret breaks old links.

**API scrape protection** (`backend/api_protection.py`): the Next.js
`app/src/proxy.ts` (Next 16's name for middleware) adds `X-Internal-Api-Key`
(`INTERNAL_API_KEY`, same value on Vercel and the API) and the visitor's IP to
proxied `/api/*` calls; the SSR detail pages do the same via
`fetchBackend(path, revalidate, { forVisitor: true })` in `app/src/lib/server-api.ts`. The API trusts `X-Visitor-IP` only with a valid
key, otherwise it uses the rightmost `X-Forwarded-For` entry that Caddy writes.
Rate limits key on that IP (or the signed-in user) per tier; publication and
researcher lists/details share one "data" budget. Anonymous callers get
`per_page` clamped to 50 and at most 2000 results deep (400 beyond). Known
scraper/HTTP-library User-Agents and empty UAs get 403 on data endpoints.
`API_DIRECT_ACCESS` (open/limited/deny) sets the policy for calls straight to
the duckdns host. 429s carry `Retry-After` and log `rate_limited ip=... path=...`;
the monitor's `api_traffic` check counts them. Without `INTERNAL_API_KEY`, and
for keyless calls while `API_DIRECT_ACCESS=open`, the API fails open to
per-edge-IP per-minute limits. Roll the key out on Vercel (redeploy) before the
API, and switch `API_DIRECT_ACCESS` away from `open` only once the frontend
sends it: otherwise all site traffic shares a few Vercel IPs and gets 429s. New server-side calls from
the frontend to the backend should go through `fetchBackend` or send
`internalApiHeaders()` from `app/src/lib/internal-api.ts`.

## Architecture

### Data Flow

```
Researcher URLs (DB) → HTMLFetcher (fetch + hash-based change detection)
  → Publication extractor (Google AI Studio/Gemini structured outputs) → Database (papers, feed_events)
  → Link extractor (trusted-domain links → DOI resolution → paper_links)
  → OpenAlex enrichment (DOI lookup or title search → coauthors, abstracts)
  → FastAPI REST API → Next.js frontend (SWR)
```

### Backend (Python — `backend/` package)

| File | Role |
|------|------|
| `backend/api.py` | FastAPI REST API — 20+ endpoints, CORS, rate limiting (`slowapi`), standardized error envelope |
| `backend/database/` | Package with facade class (`Database`) — submodules: `connection.py` (pool), `schema.py` (DDL/migrations), `researchers.py`, `papers.py`, `snapshots.py`, `llm.py`, `admin.py` |
| `backend/main.py` | CLI entry points for scraping pipeline stages |
| `backend/pipeline/html_fetcher.py` | Web scraper — per-domain rate limiting, robots.txt compliance, content hashing for change detection |
| `backend/pipeline/publication.py` | LLM extraction (Google AI Studio/Gemini) — Pydantic structured outputs, title dedup via SHA-256 hash |
| `backend/pipeline/extraction.py` | Per-URL extraction logic shared by worker and CLI — reads stored HTML, runs LLM, persists papers/links/snapshots, marks extracted |
| `backend/pipeline/paper_saver.py` | Persists extracted papers and authorship links to the database |
| `backend/pipeline/feed_events.py` | Emits feed events (new_paper, status_change, etc.) after extraction |
| `backend/pipeline/scheduler.py` | APScheduler background jobs with MySQL advisory locks to prevent concurrent scraping |
| `backend/llm/client.py` | Google AI Studio LLM client — OpenAI-compatible SDK, guided JSON via `response_format`, retry with reprompt |
| `backend/enrichment/link_extractor.py` | Trusted-domain link extraction from HTML, DOI-based and anchor text matching to papers |
| `backend/enrichment/doi_resolver.py` | DOI resolution from publisher URLs — regex extraction + Crossref PII-to-DOI lookup |
| `backend/enrichment/openalex.py` | OpenAlex API client — DOI lookup, title search, coauthor/abstract enrichment, researcher ID backfill |
| `backend/enrichment/jel_classifier.py` | JEL code classification via LLM — bio cleaning, prompt from bio + papers + topics, evidence hash |
| `backend/enrichment/jel_job.py` | Incremental, budgeted JEL classification batch (scheduler job + `classify-jel` CLI) |
| `backend/enrichment/jel_enrichment.py` | Maps OpenAlex topics to JEL codes (`enrich-jel` CLI; topic fallback for the JEL job) |
| `backend/enrichment/paper_merge.py` | Deduplicates papers post-extraction |
| `backend/researcher.py` | Researcher data access layer |
| `backend/config.py` | Env var validation + encoding guard — raises `EnvironmentError` on import if required vars missing |
| `scripts/check_env.py` | Validates required env vars (used by the check suite) |
| `scripts/deploy.sh` | Production deploy: git pull + docker-compose rebuild + health check |
| `scripts/monitor.py` | Production health checks behind the daily Monitor workflow; opens/closes the `monitor-alert` issue |
| `scripts/backup.sh` | Daily MySQL backup with gzip, keeps newest 3 (`KEEP_BACKUPS`); skips with an ERROR in `/backups/backup.log` rather than fill the disk (cron on Hetzner) |
| `Caddyfile` | Reverse proxy config for auto-SSL on Hetzner |
| `docker-compose.prod.yml` | Production override: no frontend, `restart: always`, 2 workers |

No ORM — direct parameterized SQL via `mysql-connector-python`. All code imports `from backend.database import Database` — the facade preserves this API while submodules are organized by domain.

### Frontend (Next.js — `app/` directory)

- **Pages**: `/` (newsfeed with filters), `/researchers` (directory), `/researchers/[id]` (detail), `/admin` (password-protected dashboard)
- **Data fetching**: SWR hooks with type-safe API client (`lib/api.ts`)
- **Styling**: Tailwind CSS + Source Serif 4 / DM Sans fonts
- **Tests**: Jest + React Testing Library (`src/__tests__/`)
- **Path alias**: `@/` maps to `src/`

### Key Database Tables

- `papers` — publications with `title_hash` for cross-researcher dedup, `doi`/`openalex_id` from enrichment
- `paper_links` — extracted links to academic resources matched to papers, with `doi` from resolution
- `html_content` — cached HTML with `content_hash` for change detection; `extracted_hash` tracks what was last sent to LLM
- `feed_events` — event-driven newsfeed (new_paper, status_change, etc.)
- `authorship` — researcher↔paper links with `author_order`
- `researchers` — includes `openalex_author_id` for deterministic disambiguation (skips LLM)
- `openalex_coauthors` — coauthor data from OpenAlex enrichment
- `llm_usage` — LLM cost tracking

All foreign keys use `ON DELETE CASCADE`. Tables use `utf8mb4` charset.

## Deployment (Production)

Live MVP: **Vercel** (free frontend) + **Hetzner** (4GB VPS backend).

| Component | URL / Location |
|-----------|---------------|
| Frontend | `https://econ-newsfeed.vercel.app` (Vercel, auto-deploys from `app/` on push) |
| Backend API | `https://econ-newsfeed.duckdns.org` (Hetzner, manual deploy via SSH) |
| SSH | `ssh -i ~/.ssh/hetzner root@167.233.132.217` |
| App directory | `/opt/econ-newsfeed` on the Hetzner instance |
| Backups | `/backups/` on Hetzner, daily at 3am UTC, newest 3 kept (~2GB each) |

**How it works:**
- Vercel serves Next.js frontend via SSR, rewrites `/api/*` server-side to the Hetzner backend (env var `API_INTERNAL_URL`)
- Caddy runs on Hetzner host (not Docker), auto-provisions Let's Encrypt SSL, reverse-proxies to `localhost:8000`
- Docker Compose on Hetzner runs FastAPI API + MySQL (no frontend container)
- Scheduler runs inside the API process; MySQL advisory lock prevents duplicate scrapes across workers

**Deploy to production:**
```bash
# SSH in and run:
cd /opt/econ-newsfeed && ./scripts/deploy.sh
# Or manually:
git pull origin main && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

**Env vars:** Production `.env` lives on the server at `/opt/econ-newsfeed/.env`. Key differences from dev: `DB_HOST=db`, `FRONTEND_URL=https://econ-newsfeed.vercel.app`, `WEB_CONCURRENCY=3`, `EXTRACTION_WORKER_ENABLED=true` (required in prod — the scrape job is fetch-only, so without the worker extraction stops).

## Configuration

### Cheap website mining

`backend.discovery.mining` discovers researcher websites from changed cached HTML
and eight curated economics department/job-market directories. It makes no search
or LLM calls. A full-name personal page plus a matching publication title is
required before automatically adding an existing researcher's HOME and verified
research/publication/working-paper sections. Unknown people, conflicting owners,
and uncertain pages remain in `site_mining_candidates` for review. Explicit
`evidence.identity_hold` records block unresolved researcher identities, including
later candidates. Do not bypass the transactional miner to bulk-approve holds.

```bash
poetry run python -m backend.main mine-urls --mode cached --limit 10  # read-only dry run; fetches candidates
poetry run python -m backend.main mine-urls --apply                  # bounded persistent run
poetry run python -m backend.main mine-urls --status                 # runs, counts, review evidence
```

Production uses the optional `site-miner` Compose service. Set
`COMPOSE_PROFILES=mining` in the server `.env`, leave `SITE_MINING_ENABLED=false`,
then run `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps site-miner`.
This worker mines hourly without restarting the API; directory seeds refresh
weekly, and profile tasks resume from the durable queue. It has a 512 MB / 0.5 CPU
limit. Defaults per batch: 500 cached pages, 30 candidate verifications, 200 HTTP
requests (including robots and redirects), and 600 seconds. `SITE_MINING_*` env
settings in `.env.example` control those budgets. Raw-HTML hashes avoid repeating
unchanged pages and detect href-only changes. The `econ_site_mining` MySQL lock
also protects simultaneous CLI runs. Each URL bundle and its approval commit
together; existing URLs are never reassigned or reactivated.

Alternatively, `SITE_MINING_ENABLED=true` schedules the same bounded job inside
the API hourly and after a scrape. Use one scheduling mechanism at a time. The
paid Searlo discovery job remains separate and its configuration is unchanged.
To pause the production worker, stop `site-miner`; its persisted queue resumes
when it starts again. Mining adds URLs for the normal crawl/extraction pipeline.

Required env vars: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `GOOGLE_API_KEY`, `SCRAPE_API_KEY`. LLM model selection via `LLM_MODEL` (default: `gemini-2.5-flash`). See `.env.example` for all options with defaults.

`ADMIN_PASSWORD` enables the `/admin` dashboard (must also be set in `app/.env.local` for local dev and as a Vercel env var for production). Auth uses HMAC-signed cookies with 7-day expiry.

**User login** has two NextAuth providers: Google, and passwordless email codes
(`backend/email_login.py`, `/api/email-login/request-code` and `verify-code`;
not under `/api/auth/`, which NextAuth owns). Codes are 6 digits, HMAC-hashed in
`email_login_codes`, valid 10 minutes, single use, 5 wrong tries, 60 s resend
cooldown, 5 per address per hour plus a deployment-wide hourly cap
(`EMAIL_LOGIN_HOURLY_LIMIT`). Per-IP limits use `api_protection`'s client key (strict per
visitor; coarse in the keyless fail-open tier, where visitors share Vercel IPs);
the credentials provider forwards the visitor with `internalApiHeaders`. Email
login is offered only with a Resend key and an explicit `LOGIN_FROM_EMAIL` or
`DIGEST_FROM_EMAIL` (the compose placeholder sender does not count). Email sessions carry `sub = "user:<id>"`; Google
sessions keep the Google subject. Both reach the same `users` row for the same
address (a first Google login adopts an email account only when Google reports
`email_verified`). Codes go out through the digest's Resend sender
(`LOGIN_FROM_EMAIL`, default `DIGEST_FROM_EMAIL`); `/api/capabilities` reports
`email_login_available` and the modal hides the option without a provider. For
local dev without Resend, `EMAIL_LOGIN_DEV_LOG_CODES=true` logs codes to the API
console (ignored unless `FRONTEND_URL` is localhost).

`backend/config.py` validates required vars at import time — tests set defaults in `tests/conftest.py` (must be set before any app imports).

## Testing Patterns

- Python tests use `httpx.AsyncClient` with FastAPI's `TestClient` — no real database needed for API contract tests
- `conftest.py` sets env vars *before* importing app modules to avoid `backend/config.py`'s `sys.exit()`
- Frontend tests mock `next/font/google` via `__mocks__/next-font-google.ts`

## Gotchas

- **`.dockerignore` uses a directory-based whitelist**: The entire `backend/` directory is whitelisted as a unit (`!backend/`), so new modules added under `backend/` are automatically included. However, any new top-level files or directories outside `backend/` must still be explicitly added to `.dockerignore` with a `!` prefix, or they will be silently excluded from the Docker build and cause `ModuleNotFoundError` in production.
- **docker-compose env vars are whitelisted too**: The `environment:` blocks in `docker-compose.yml` / `docker-compose.prod.yml` enumerate which `.env` vars reach the api container. A new env var read by the backend must be added there, or it will silently be unset in production (the `.env` file alone is not enough).
- **DB container has 512MB memory limit**: Large MySQL imports (e.g., restoring a full dump) will OOM. Temporarily increase with `docker update --memory 1500m econ-newsfeed-db-1`, import, then restore.
- **Vercel env var `API_INTERNAL_URL`** is baked in at build time (used by Next.js rewrites). Changing it requires a Vercel redeploy, not just a restart.
- **Rate limits are per gunicorn worker**: slowapi counts in process memory, so the effective ceiling is the configured limit times `WEB_CONCURRENCY`, and counters reset on restart.
- **DuckDNS** points `econ-newsfeed.duckdns.org` to the Hetzner IP. Update via: `curl "https://www.duckdns.org/update?domains=econ-newsfeed&token=<DUCKDNS_TOKEN>&ip=<NEW_IP>"`
