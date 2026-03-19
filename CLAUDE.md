# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Econ Newsfeed is a full-stack application that monitors economics researchers' websites, detects new publications via LLM extraction, and displays them in a chronological newsfeed. Backend is Python/FastAPI, frontend is Next.js/TypeScript, database is MySQL 8.

## Commands

### Setup & Development
```bash
make setup          # Create .venv, install Python + npm deps
make dev            # Run API (port 8001) + Next.js (port 3000) concurrently
make seed           # Create database schema
make reset-db       # Drop and recreate database
```

### Scraping Pipeline
```bash
make scrape         # Run full scrape job (fetch + parse)
make fetch          # Download HTML from all researcher URLs only
make parse          # Extract publications sequentially
make parse-fast     # Extract publications with 8 concurrent workers
make batch-submit   # Submit OpenAI Batch API job
make batch-check    # Check Batch API job status
```

### Pre-flight Check
```bash
make check             # Run env validation, pytest, tsc, jest (use before make dev)
```

### Testing
```bash
# Python tests (from project root)
.venv/bin/pytest                    # Run all tests
.venv/bin/pytest tests/test_api.py  # Run single test file
.venv/bin/pytest -k "test_name"     # Run specific test by name

# Frontend tests
cd app && npm test                  # Run all Jest tests
cd app && npx jest --testPathPattern="ComponentName"  # Single test file
```

### Docker
```bash
docker compose up -d    # Start MySQL, API (port 8000), frontend (port 3000)
docker compose down     # Stop all services
```

## Architecture

### Backend (Python, project root)

| File | Purpose |
|------|---------|
| `api.py` | FastAPI app with REST endpoints, CORS, rate limiting, security headers |
| `database.py` | MySQL connection pooling, schema creation, all SQL queries |
| `scheduler.py` | APScheduler background jobs, scrape coordination with MySQL advisory locks |
| `html_fetcher.py` | Web scraping with per-domain rate limiting, robots.txt compliance, change detection via content hashing |
| `publication.py` | OpenAI-based publication extraction with Pydantic validation, deduplication via title_hash |
| `db_config.py` | Validates required env vars on import (exits if missing) |
| `main.py` | CLI interface for manual scrape operations |

### Frontend (`app/`)

Next.js 14 App Router with SWR for data fetching. Tailwind CSS for styling.

- `src/app/` — pages (newsfeed, researcher list, researcher detail)
- `src/components/` — reusable React components with co-located tests
- `src/lib/` — API client utilities

### Key Design Patterns

- **Publication deduplication**: SHA-256 `title_hash` column with UNIQUE constraint across all researchers
- **Scrape concurrency control**: MySQL `GET_LOCK`/`RELEASE_LOCK` advisory locks prevent parallel scrapes
- **Change detection**: Content hashed on fetch; only changed pages trigger LLM extraction
- **Token cost tracking**: `llm_usage` table logs per-call token counts and costs

### API Endpoints

All under `/api/`: `GET /publications`, `GET /publications/{id}`, `GET /researchers`, `GET /researchers/{id}`, `GET /fields`, `POST /scrape` (HMAC-SHA256 auth via `X-API-Key`), `GET /scrape/status`.

## Environment

Copy `.env.example` to `.env`. Required variables: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `OPENAI_API_KEY`, `SCRAPE_API_KEY` (min 16 chars).

## Testing Details

- Python tests are in `tests/` using pytest + httpx. `conftest.py` sets env vars before app imports to avoid `db_config.py` exit. Tests mock the database and scheduler.
- Frontend tests use Jest with React Testing Library (jsdom environment). Config in `app/jest.config.ts`.
- Test database name: `test_econ_newsfeed`

## Code Style

- **Python**: Python 3.12+, PEP 8, type hints on all function signatures, 88-char line length, imports ordered stdlib → third-party → local
- **TypeScript**: Strict mode, `interface` for object shapes, `type` for unions. No `any` — use `unknown` with type guards
- **Components**: Functional components with hooks. Server Components by default; `"use client"` only for interactive elements
- **Both**: TDD workflow — write tests before implementation
