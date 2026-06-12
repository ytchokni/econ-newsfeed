# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Econ Newsfeed monitors economics researchers' personal websites, detects new/changed publications via LLM extraction (Google AI Studio/Gemini), and displays them in a chronological newsfeed. Full-stack monorepo: Python backend (FastAPI) + Next.js frontend + MySQL.

## Commands

```bash
# Setup
make setup                # poetry install + npm install (in app/)

# Development
make dev                  # API on :8001 + frontend on :3000 (parallel)
make kill                 # Kill processes on ports 8000/8001/3000/3001

# Database
make seed                 # Create tables + run migrations (idempotent)
make reset-db             # Drop and recreate database from scratch
make sync-prod            # DESTRUCTIVE: replace local DB with latest prod backup (downloads dump first, then recreates volume)

# Scraping pipeline
make scrape               # Fetch-only pipeline: download HTML + draft URL validation
make fetch                # Stage 1: Download HTML from researcher URLs (hash-based change detection)
make batch-submit         # Stage 2: Submit changed URLs to Gemini Batch API for extraction
make batch-check          # Stage 3: Process completed batch results → save papers, snapshots, links

# Enrichment & classification
make enrich               # Enrich papers via OpenAlex (DOI lookup first, title search fallback)
make classify-jel         # Classify researchers into JEL codes via LLM
make discover-domains     # Scan HTML for untrusted domains with paper-like links

# Testing & validation
make check                # Full suite: env check → pytest → tsc → jest
make check-data           # Data-quality invariants against the real DB (tests_data_quality/) — failures are bad rows, not code bugs
make check-data-live      # check-data + pipeline-liveness checks (scrape freshness, worker activity) — run on the server; meaningless on a synced mirror
poetry run pytest                        # All Python tests
poetry run pytest tests/test_api_publications.py  # Single test file
poetry run pytest -k test_name           # Single test by name
cd app && npx jest                       # All frontend tests
cd app && npx jest --testPathPattern=ComponentName  # Single frontend test

# One-time scripts
poetry run python scripts/backfill_paper_links.py  # Populate paper_links from stored HTML + enrich
poetry run python scripts/cleanup_data_quality.py  # Fix rows flagged by `make check-data` (dry-run; add --apply to write)

# Docker
docker compose up         # Full stack (db + api + frontend)

# Production (Lightsail)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build  # Start prod (no frontend)
./scripts/deploy.sh       # Pull latest + rebuild + health check
./scripts/backup.sh       # Manual DB backup (also runs daily via cron at 3am UTC)
```

### Pipeline Details

**`make scrape`** runs the scheduler job (`scheduler.run_scrape_job()`), which is **fetch-only**:
1. **Fetch phase** (all URLs): Download HTML for all researcher URLs, skip unchanged (content hash).
2. **Draft URL validation**: HEAD-request validation of `draft_url` fields

Extraction is owned by the extraction worker (below) — the scrape job only refreshes stored HTML and the `content_hash ≠ extracted_hash` queue.

**Enrichment worker:** When `ENRICHMENT_WORKER_ENABLED=true`, a background thread in the API process continuously enriches unenriched papers via OpenAlex. Polls every 5 minutes, processes batches of 50, respects the daily budget. Backs off to 10-minute sleep after 5 consecutive failures. In local dev (worker disabled), run `make enrich` manually after scraping.

**Extraction worker:** When `EXTRACTION_WORKER_ENABLED=true`, a background thread in the API process continuously extracts publications from changed pages (`content_hash ≠ extracted_hash`) using synchronous free-tier Gemma calls. Calls are latency-bound (45–190s each, ~35 output tokens/s); `EXTRACTION_DELAY_SECONDS` (default 2) paces the fast tail under the 30 RPM free-tier cap. Polls every 5 minutes when idle, backs off 10 minutes after 10 consecutive failures (quota exhaustion), and skips a URL for the process lifetime after 3 failed attempts (poison-pill guard). In local dev (worker disabled), run `make extract` manually after fetching.

**Extraction circuit breaker (CLI):** The `make extract` CLI stops after 10 consecutive failed extractions (e.g. LLM quota exhausted). Fetched HTML is preserved — extraction resumes on the next run for URLs where `content_hash ≠ extracted_hash`. The continuous worker uses backoff + per-URL retry limits instead of stopping.

**Paper merge:** `merge_duplicate_papers()` dedupes papers post-run.

**Zombie scrape_log cleanup:** On scheduler start and hourly thereafter, `scrape_log` entries stuck in `'running'` are marked `'failed'` based on the scrape advisory lock: if no connection holds the lock, all running rows older than 5 minutes are zombies; if the lock is held, all but the newest running row are. Deploys that restart the container mid-scrape are swept on the next boot.

**`make fetch`** runs stage 1 only (download HTML). Extraction is handled by the extraction worker (or `make extract` manually) — both use the shared `extraction.extract_one_url()`, which protects feed event integrity via `_title_in_previous_snapshot()` and the `_url_has_baseline()` check.

**Granular batch pipeline** (run stages independently):
1. `make fetch` — Download HTML, detect changes via content hash. Safe to run anytime.
2. `make batch-submit` — Submit URLs where `content_hash ≠ extracted_hash` to Gemini Batch API. Requires fetch first.
3. `make batch-check` — Poll pending batches, process results (save papers, snapshots, links, feed events). Idempotent for completed batches.

Each stage is independently safe. Feed event integrity is protected by `_title_in_previous_snapshot()` and the `_url_has_baseline()` check regardless of which pipeline path is used.

## Architecture

### Data Flow

```
Researcher URLs (DB) → HTMLFetcher (fetch + hash-based change detection)
  → Publication extractor (Google AI Studio/Gemini structured outputs) → Database (papers, feed_events)
  → Link extractor (trusted-domain links → DOI resolution → paper_links)
  → OpenAlex enrichment (DOI lookup or title search → coauthors, abstracts)
  → FastAPI REST API → Next.js frontend (SWR)
```

### Backend (Python — root directory)

| File | Role |
|------|------|
| `api.py` | FastAPI REST API — 20+ endpoints, CORS, rate limiting (`slowapi`), standardized error envelope |
| `database/` | Package with facade class (`Database`) — submodules: `connection.py` (pool), `schema.py` (DDL/migrations), `researchers.py`, `papers.py`, `snapshots.py`, `llm.py`, `admin.py` |
| `main.py` | CLI entry points for scraping pipeline stages |
| `html_fetcher.py` | Web scraper — per-domain rate limiting, robots.txt compliance, content hashing for change detection |
| `publication.py` | LLM extraction (Google AI Studio/Gemini) — Pydantic structured outputs, title dedup via SHA-256 hash |
| `extraction.py` | Per-URL extraction logic shared by worker and CLI — reads stored HTML, runs LLM, persists papers/links/snapshots, marks extracted |
| `llm_client.py` | Google AI Studio LLM client — OpenAI-compatible SDK, guided JSON via `response_format`, retry with reprompt |
| `link_extractor.py` | Trusted-domain link extraction from HTML, DOI-based and anchor text matching to papers |
| `doi_resolver.py` | DOI resolution from publisher URLs — regex extraction + Crossref PII-to-DOI lookup |
| `openalex.py` | OpenAlex API client — DOI lookup, title search, coauthor/abstract enrichment, researcher ID backfill |
| `scheduler.py` | APScheduler background jobs with MySQL advisory locks to prevent concurrent scraping |
| `researcher.py` | Researcher data access layer |
| `db_config.py` | Env var validation — raises `EnvironmentError` on import if required vars missing |
| `scripts/check_env.py` | Validates required env vars (used by `make check`) |
| `scripts/deploy.sh` | Production deploy: git pull + docker-compose rebuild + health check |
| `scripts/backup.sh` | Daily MySQL backup with gzip + 7-day retention (cron on Lightsail) |
| `Caddyfile` | Reverse proxy config for auto-SSL on Lightsail |
| `docker-compose.prod.yml` | Production override: no frontend, `restart: always`, 2 workers |

No ORM — direct parameterized SQL via `mysql-connector-python`. All code imports `from database import Database` — the facade preserves this API while submodules are organized by domain.

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
- `batch_jobs` / `llm_usage` — LLM cost tracking

All foreign keys use `ON DELETE CASCADE`. Tables use `utf8mb4` charset.

## Deployment (Production)

Live MVP: **Vercel** (free frontend) + **AWS Lightsail** ($12/mo backend).

| Component | URL / Location |
|-----------|---------------|
| Frontend | `https://econ-newsfeed.vercel.app` (Vercel, auto-deploys from `app/` on push) |
| Backend API | `https://econ-newsfeed.duckdns.org` (Lightsail, manual deploy via SSH) |
| SSH | `ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188` |
| App directory | `/opt/econ-newsfeed` on the Lightsail instance |
| Backups | `/backups/` on Lightsail, daily at 3am UTC, 7-day retention |

**How it works:**
- Vercel serves Next.js frontend via SSR, rewrites `/api/*` server-side to the Lightsail backend (env var `API_INTERNAL_URL`)
- Caddy runs on Lightsail host (not Docker), auto-provisions Let's Encrypt SSL, reverse-proxies to `localhost:8000`
- Docker Compose on Lightsail runs FastAPI API + MySQL (no frontend container)
- Scheduler runs inside the API process; MySQL advisory lock prevents duplicate scrapes across workers

**Deploy to production:**
```bash
# SSH in and run:
cd /opt/econ-newsfeed && ./scripts/deploy.sh
# Or manually:
git pull origin main && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

**Env vars:** Production `.env` lives on the server at `/opt/econ-newsfeed/.env`. Key differences from dev: `DB_HOST=db`, `FRONTEND_URL=https://econ-newsfeed.vercel.app`, `WEB_CONCURRENCY=2`, `EXTRACTION_WORKER_ENABLED=true` (required in prod — the scrape job is fetch-only, so without the worker extraction stops).

## Configuration

Required env vars: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `GOOGLE_API_KEY`, `SCRAPE_API_KEY`. LLM model selection via `LLM_MODEL` (default: `gemini-2.5-flash`). See `.env.example` for all options with defaults.

`ADMIN_PASSWORD` enables the `/admin` dashboard (must also be set in `app/.env.local` for local dev and as a Vercel env var for production). Auth uses HMAC-signed cookies with 7-day expiry.

`db_config.py` validates required vars at import time — tests set defaults in `tests/conftest.py` (must be set before any app imports).

## Testing Patterns

- Python tests use `httpx.AsyncClient` with FastAPI's `TestClient` — no real database needed for API contract tests
- `conftest.py` sets env vars *before* importing app modules to avoid `db_config.py`'s `sys.exit()`
- Frontend tests mock `next/font/google` via `__mocks__/next-font-google.ts`

## Gotchas

- **`.dockerignore` uses a whitelist**: Every new Python module must be explicitly added to `.dockerignore` with a `!` prefix, or it will be silently excluded from the Docker build and cause `ModuleNotFoundError` in production.
- **docker-compose env vars are whitelisted too**: The `environment:` blocks in `docker-compose.yml` / `docker-compose.prod.yml` enumerate which `.env` vars reach the api container. A new env var read by the backend must be added there, or it will silently be unset in production (the `.env` file alone is not enough).
- **DB container has 512MB memory limit**: Large MySQL imports (e.g., restoring a full dump) will OOM. Temporarily increase with `docker update --memory 1500m econ-newsfeed-db-1`, import, then restore.
- **Vercel env var `API_INTERNAL_URL`** is baked in at build time (used by Next.js rewrites). Changing it requires a Vercel redeploy, not just a restart.
