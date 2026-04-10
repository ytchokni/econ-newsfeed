# Econ Newsfeed

Full-stack application that monitors economics researchers' personal websites, detects new publications via LLM-powered extraction, and displays them in a chronological newsfeed.

**Stack:** FastAPI backend, Next.js 14 frontend, MySQL 8, Parasail (Gemma 4 31B).

## Quick Start (Docker Compose)

```bash
cp .env.example .env
# Edit .env — set PARASAIL_API_KEY, DB_PASSWORD, MYSQL_ROOT_PASSWORD, SCRAPE_API_KEY
docker compose up
```

The frontend is available at `http://localhost:3000` and the API at `http://localhost:8000`.

## Development Setup

### Backend

```bash
poetry install
poetry run uvicorn api:app --reload --port 8000
```

### Frontend

```bash
cd app
npm install
npm run dev
```

### Database

Docker Compose starts MySQL 8 automatically. For local development without Docker, create a MySQL database and configure connection details in `.env`.

## Environment Variables

Copy `.env.example` and fill in the required values. Key variables:

| Variable | Description |
|---|---|
| `PARASAIL_API_KEY` | Parasail API key for LLM inference (required) |
| `LLM_MODEL` | LLM model ID (default: `google/gemma-4-31b-it`) |
| `DB_PASSWORD` | MySQL application user password (required) |
| `MYSQL_ROOT_PASSWORD` | MySQL root password (Docker Compose only) |
| `SCRAPE_API_KEY` | API key for the POST /api/scrape endpoint (must be 16+ characters) |
| `SCRAPE_INTERVAL_HOURS` | Hours between automatic scrape runs (default: 24) |
| `CONTENT_MAX_CHARS` | Max characters to send to the LLM (default: 4000) |

See `.env.example` for the full list.

## Architecture Overview

### Backend

| Module | Purpose |
|---|---|
| `api.py` | FastAPI application with REST endpoints and rate limiting |
| `scheduler.py` | APScheduler-based periodic scraping |
| `database/` | Database package (schema, queries, connection pooling) |
| `html_fetcher.py` | Fetches and hashes HTML content from researcher URLs |
| `publication.py` | LLM-powered publication extraction from HTML |
| `researcher.py` | Researcher CSV import |

### Frontend

Next.js 14 application in `app/` with server-side rendering and API route proxying.

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/health` | No | Health check |
| GET | `/api/metrics` | No | Scraping metrics and statistics |
| GET | `/api/publications` | No | Paginated publication feed |
| GET | `/api/publications/{id}` | No | Single publication details |
| GET | `/api/researchers` | No | Paginated researcher list |
| GET | `/api/researchers/{id}` | No | Single researcher with publications |
| GET | `/api/fields` | No | List of research fields |
| GET | `/api/filter-options` | No | Available filter values |
| POST | `/api/scrape` | X-API-Key | Trigger a scrape run |
| GET | `/api/scrape/status` | No | Current scrape job status |

## Running Tests

```bash
# Backend
poetry run pytest

# Frontend
cd app && npm test
```

## Security Notes

- Never commit `.env` to version control (it is in `.gitignore`).
- `SCRAPE_API_KEY` protects the scrape endpoint; use 16+ characters and rotate periodically.
- In production, use Docker secrets or a cloud secret manager instead of environment variables.
- The API binds to `127.0.0.1` by default in Docker Compose to avoid exposing services to the network.
