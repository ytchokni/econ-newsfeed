# Review Scope

## Target

Full econ-newsfeed project — Python backend (FastAPI) + Next.js frontend + MySQL. Monitors economics researchers' personal websites, detects new/changed publications via LLM extraction (OpenAI), and displays them in a chronological newsfeed.

## Files

### Backend (Python — root directory)
- `api.py` — FastAPI REST API (20+ endpoints, CORS, rate limiting)
- `database/` — MySQL schema, connection pooling, migrations
  - `__init__.py`, `connection.py`, `llm.py`, `papers.py`, `researchers.py`, `schema.py`, `snapshots.py`
- `db_config.py` — Env var validation
- `html_fetcher.py` — Web scraper with per-domain rate limiting, robots.txt compliance
- `main.py` — CLI entry points for scraping pipeline
- `publication.py` — OpenAI extraction with Pydantic structured outputs, Batch API
- `researcher.py` — Researcher data access layer
- `scheduler.py` — APScheduler background jobs with advisory locks
- `scripts/check_env.py` — Environment validation script

### Frontend (Next.js — `app/` directory)
- `app/src/app/` — Pages: `/` (newsfeed), `/researchers`, `/researchers/[id]`
  - `page.tsx`, `layout.tsx`, `NewsfeedContent.tsx`
  - `researchers/page.tsx`, `researchers/ResearchersContent.tsx`
  - `researchers/[id]/page.tsx`, `researchers/[id]/ResearcherDetailContent.tsx`
- `app/src/components/` — Shared UI components
  - `Header.tsx`, `PublicationCard.tsx`, `ResearcherCard.tsx`
  - `EmptyState.tsx`, `ErrorMessage.tsx`
  - `PublicationCardSkeleton.tsx`, `ResearcherCardSkeleton.tsx`
  - `SearchableCheckboxDropdown.tsx`
- `app/src/lib/` — API client and types
  - `api.ts`, `types.ts`

### Tests
- **Python** (`tests/`): 19 test files covering API, security, scraping, dedup, scheduler
- **Frontend** (`app/src/`): Jest + React Testing Library tests for components and pages

### Configuration
- `Makefile`, `docker-compose.yml`, `Dockerfile.api`
- `pyproject.toml`, `app/package.json`
- `app/next.config.mjs`, `app/tsconfig.json`, `app/tailwind.config.ts`
- `.env.example`

## Flags

- Security Focus: no
- Performance Critical: no
- Strict Mode: no
- Framework: FastAPI + Next.js (auto-detected)

## Review Phases

1. Code Quality & Architecture
2. Security & Performance
3. Testing & Documentation
4. Best Practices & Standards
5. Consolidated Report
