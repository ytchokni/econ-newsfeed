# Implementation Plan: Pipeline Hardening & Scraping Infrastructure

**Track ID:** pipeline-hardening_20260317
**Spec:** [spec.md](./spec.md)
**Created:** 2026-03-17
**Status:** [ ] Not Started

## Overview

Fix schema, harden the scraping pipeline, and add automated scheduling. Work proceeds in four phases: env/config foundation, schema migration, pipeline bug fixes and safety controls, and scheduler creation.

## Phase 1: Configuration & Environment

Set up environment validation and dependency pinning as the foundation for all other phases.

### Tasks

- [ ] Task 1.1: Create `.env.example` with all variables from DESIGN.md Section 8.1 (DB, OpenAI, scraping, frontend)
- [ ] Task 1.2: Add startup validation to `db_config.py` — fail fast with clear error if `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, or `OPENAI_API_KEY` are missing/empty
- [ ] Task 1.3: Pin all dependencies in `requirements.txt` — add `fastapi`, `uvicorn[standard]`, `apscheduler`, `pydantic`; remove duplicate `python-dotenv`; pin existing unpinned packages

### Verification

- [ ] App raises clear error on missing env vars; `.env.example` documents all variables; `pip install -r requirements.txt` succeeds with pinned versions

## Phase 2: Schema Migration

Update `database.py` table definitions to match DESIGN.md Section 3.1.

### Tasks

- [ ] Task 2.1: Widen `url` columns to `VARCHAR(2048)` in `researcher_urls` and `publications`
- [ ] Task 2.2: Remove redundant `url` column from `html_content` table; add `UNIQUE KEY uq_url_id (url_id)` constraint
- [ ] Task 2.3: Add `UNIQUE KEY uq_title_url (title(200), url(200))` and `INDEX idx_timestamp (timestamp)` to `publications`
- [ ] Task 2.4: Add `INDEX idx_name (last_name, first_name)` to `researchers`; add `INDEX idx_researcher (researcher_id)` and `INDEX idx_publication (publication_id)` to `authorship`; add `INDEX idx_url_id_ts (url_id, timestamp)` to `html_content`
- [ ] Task 2.5: Add `scrape_log` table (id, started_at, finished_at, status ENUM, urls_checked, urls_changed, pubs_extracted, error_message)
- [ ] Task 2.6: Add `NOT NULL` constraints on foreign keys (`researcher_urls.researcher_id`, `html_content.url_id`, `authorship.researcher_id`, `authorship.publication_id`)

### Verification

- [ ] `python database.py` creates all tables with correct column widths, constraints, indexes, and the new `scrape_log` table

## Phase 3: Pipeline Fixes & Safety

Fix bugs in existing pipeline code and add security controls.

### Tasks

- [ ] Task 3.1: Fix `researcher.py` — remove `RETURNING` clause from `add_researcher()` and `add_url_to_researcher()`; add missing `page_type` parameter to `add_url_to_researcher()`
- [ ] Task 3.2: Fix `html_fetcher.py` — make `fetch_and_save_if_changed()` return `bool` (True if changed, False otherwise); eliminate double HTML parsing (parse once, pass text to both `has_text_changed` and `save_text`)
- [ ] Task 3.3: Update `save_text()` to upsert pattern (`INSERT ... ON DUPLICATE KEY UPDATE`); remove `url` parameter since column was dropped; update callers
- [ ] Task 3.4: Add SSRF protection — validate URLs before fetching (reject non-HTTP(S) schemes, private/reserved IPs, AWS metadata endpoints)
- [ ] Task 3.5: Add per-domain rate limiting (`SCRAPE_RATE_LIMIT_SECONDS`, default 2s) and exponential backoff on retries (1s, 2s, 4s for timeout + 5xx errors, max 3 retries)
- [ ] Task 3.6: Add content size limit (reject responses > 1 MB); add configurable truncation (`CONTENT_MAX_CHARS`, default 4000) with logging when truncation occurs
- [ ] Task 3.7: Add `robots.txt` compliance using `urllib.robotparser` — check before fetching each URL
- [ ] Task 3.8: Fix `publication.py` — instantiate OpenAI client once at module level; read model from `OPENAI_MODEL` env var (default `gpt-4o-mini`) instead of hardcoded `gpt-3.5-turbo`
- [ ] Task 3.9: Add Pydantic models for LLM output validation (`PublicationExtraction` with title, authors, year, venue fields); validate parsed JSON through Pydantic before database insertion
- [ ] Task 3.10: Add publication deduplication — normalize title (lowercase, strip) and check against existing records before insert; rely on DB `UNIQUE(title(200), url(200))` as safety net
- [ ] Task 3.11: Add diff-based extraction — `get_previous_text()` method on `HTMLFetcher`; `compute_diff()` using `difflib.unified_diff`; send only new/changed lines to LLM when old content exists

### Verification

- [ ] `fetch_and_save_if_changed()` returns bool; HTML parsed once; SSRF rejects private IPs; rate limiter delays between same-domain requests; Pydantic rejects malformed LLM output; duplicates are skipped; diff is computed when old content exists

## Phase 4: Scheduler

Create `scheduler.py` and wire it up for standalone use and future API integration.

### Tasks

- [ ] Task 4.1: Create `scheduler.py` with `run_scrape_job()` orchestration function — threading.Lock guard, scrape_log creation/updates, iterate all researcher URLs, call fetch/extract pipeline
- [ ] Task 4.2: Add `create_scrape_log()` and `update_scrape_log()` helper functions for the `scrape_log` table
- [ ] Task 4.3: Integrate diff-based extraction into the scrape job — get old text, get new text, compute diff, send diff (or full text if first scrape) to LLM extraction
- [ ] Task 4.4: Add APScheduler `BackgroundScheduler` configuration — `SCRAPE_INTERVAL_HOURS` interval, optional `SCRAPE_ON_STARTUP` trigger
- [ ] Task 4.5: Add `start_scheduler()` and `shutdown_scheduler()` entry points for future API integration

### Verification

- [ ] Scheduler runs scrape job on configured interval; threading lock prevents concurrent runs; scrape_log records are created with correct stats; diff-based extraction is used when old content exists

## Final Verification

- [ ] All acceptance criteria met
- [ ] `python database.py` creates correct schema
- [ ] Pipeline handles edge cases (SSRF, oversized content, malformed LLM output, duplicate publications)
- [ ] Scheduler can be started standalone and prevents concurrent runs
- [ ] Ready for review

---

_Generated by Conductor. Tasks will be marked [~] in progress and [x] complete._
