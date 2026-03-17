# Econ Newsfeed -- MVP Design Document

## 1. Overview

Econ Newsfeed is a system that tracks economists' personal websites, detects new publications, and displays them in a chronological newsfeed. The MVP delivers three capabilities: a web-based newsfeed UI, a REST API serving publication data, and automated periodic scraping of researcher websites.

### 1.1 Problem

Economists publish new working papers and journal articles on their personal websites, but there is no unified feed to track these updates across researchers. Manually checking dozens of websites is impractical.

### 1.2 Solution

A pipeline that periodically scrapes researcher websites, uses LLM-based extraction to identify publications, and surfaces new entries through a web newsfeed.

### 1.3 Scope (MVP)

**In scope:**
- Newsfeed website showing publications sorted by discovery date
- REST API serving publication and researcher data
- Automated scraping on a configurable schedule
- Researcher directory page
- Manual scrape trigger via API
- Local development setup
- Docker Compose deployment

**Out of scope (post-MVP):**
- User accounts and authentication
- Email/RSS notifications
- Full-text search
- Publication deduplication across researchers
- Admin dashboard for managing researchers
- Custom scraping rules per website

---

## 2. Architecture

```
                    +------------------+
                    |   Next.js App    |
                    |   (Frontend)     |
                    |   Port 3000      |
                    +--------+---------+
                             |
                         HTTP/JSON
                             |
                    +--------v---------+
                    |   FastAPI        |
                    |   (Backend API)  |
                    |   Port 8000      |
                    +--------+---------+
                             |
              +--------------+--------------+
              |                             |
     +--------v---------+        +---------v----------+
     |   MySQL / RDS    |        |   Scheduler        |
     |   (Data Store)   |        |   (APScheduler)    |
     +------------------+        |   Background task  |
                                 +--------------------+
                                          |
                                 +--------v---------+
                                 |   Scraping        |
                                 |   Pipeline        |
                                 |   (HTMLFetcher +  |
                                 |    OpenAI LLM)    |
                                 +------------------+
```

### 2.1 Components

| Component | Technology | Role |
|-----------|-----------|------|
| Frontend | Next.js 14 (latest patch), React 18, TypeScript, Tailwind CSS, SWR | Renders the newsfeed and researcher pages with client-side caching |
| Backend API | FastAPI, Uvicorn, Pydantic | Serves data to the frontend, manages scrape triggers, validates LLM output |
| Scheduler | APScheduler (BackgroundScheduler) | Runs the scraping pipeline on a cron-like interval |
| Scraping pipeline | requests, BeautifulSoup, OpenAI API, difflib | Fetches HTML, detects changes, diffs content, extracts publications |
| Database | MySQL 8 (or AWS RDS) | Stores researchers, URLs, publications, HTML snapshots |

### 2.2 Data Flow

```
1. Scheduler triggers scraping job (every N hours)
2. For each researcher URL:
   a. HTMLFetcher downloads page content
   b. Text is extracted and SHA-256 hashed
   c. Hash compared against last stored hash
   d. If changed: store new content, send text to OpenAI for extraction
   e. Extracted publications saved to database with authorship links
3. Frontend fetches /api/publications (paginated, newest first)
4. User sees new publications appear in the feed
```

---

## 3. Database Schema

The existing schema is updated with constraint fixes, indexes, and a new `scrape_log` table.

### 3.1 Tables

```sql
researchers (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    last_name       VARCHAR(255) NOT NULL,
    first_name      VARCHAR(255) NOT NULL,
    position        VARCHAR(255),
    affiliation     VARCHAR(255),
    INDEX idx_name (last_name, first_name)
)

researcher_urls (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    researcher_id   INT NOT NULL REFERENCES researchers(id),
    page_type       VARCHAR(255) NOT NULL,    -- PUB, WP, HOME
    url             VARCHAR(2048) NOT NULL
)

publications (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    url             VARCHAR(2048),            -- source URL
    title           TEXT,
    year            VARCHAR(4),
    venue           TEXT,
    timestamp       DATETIME,                 -- when discovered
    UNIQUE KEY uq_title_url (title(200), url(200)),
    INDEX idx_timestamp (timestamp)
)

html_content (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    url_id          INT NOT NULL REFERENCES researcher_urls(id),
    content         LONGTEXT,                 -- capped at ~1 MB by application layer
    content_hash    VARCHAR(64),              -- SHA-256
    timestamp       DATETIME,
    researcher_id   INT REFERENCES researchers(id),
    UNIQUE KEY uq_url_id (url_id),
    INDEX idx_url_id_ts (url_id, timestamp)
)

authorship (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    researcher_id   INT NOT NULL REFERENCES researchers(id),
    publication_id  INT NOT NULL REFERENCES publications(id),
    author_order    INT,
    INDEX idx_researcher (researcher_id),
    INDEX idx_publication (publication_id)
)

scrape_log (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    started_at      DATETIME NOT NULL,
    finished_at     DATETIME,
    status          ENUM('running', 'completed', 'failed') DEFAULT 'running',
    urls_checked    INT DEFAULT 0,
    urls_changed    INT DEFAULT 0,
    pubs_extracted  INT DEFAULT 0,
    error_message   TEXT
)
```

**Changes from original schema:**
- `url` columns widened from `VARCHAR(255)` to `VARCHAR(2048)` — academic URLs frequently exceed 255 characters.
- Redundant `url` column removed from `html_content` — derivable via `url_id` foreign key.
- `UNIQUE(url_id)` on `html_content` — enforces one-row-per-URL; `save_text()` uses `INSERT ... ON DUPLICATE KEY UPDATE` instead of append-only inserts, preventing unbounded storage growth.
- `UNIQUE(title(200), url(200))` on `publications` — prevents duplicate publications on re-extraction. Titles are normalized (lowercased, stripped) before comparison.
- Secondary indexes added on columns used for lookups, ordering, and joins.
- `LONGTEXT` content capped at ~1 MB by the application layer to prevent storage abuse from malicious pages.

### 3.2 Entity Relationship Diagram

```
researchers 1───* researcher_urls
researchers 1───* authorship
publications 1───* authorship
researcher_urls 1───1 html_content    (upsert: one snapshot per URL)
researchers 1───* html_content
```

---

## 4. API Design

Base URL: `http://localhost:8000`

### 4.1 Publications

#### `GET /api/publications`

Returns a paginated list of publications, newest first.

**Query parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Items per page (max 100) |
| `year` | string | -- | Filter by publication year |
| `researcher_id` | int | -- | Filter by researcher |

**Response:**
```json
{
  "items": [
    {
      "id": 42,
      "title": "Immigration and Wages: Evidence from...",
      "authors": [
        { "id": 1, "first_name": "Max Friedrich", "last_name": "Steinhardt" }
      ],
      "year": "2024",
      "venue": "Journal of Labor Economics",
      "source_url": "https://sites.google.com/...",
      "discovered_at": "2026-03-15T14:30:00Z"
    }
  ],
  "total": 156,
  "page": 1,
  "per_page": 20,
  "pages": 8
}
```

#### `GET /api/publications/{id}`

Returns a single publication with full author details.

### 4.2 Researchers

#### `GET /api/researchers`

Returns all tracked researchers.

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "first_name": "Max Friedrich",
      "last_name": "Steinhardt",
      "position": "Professor",
      "affiliation": "Freie Universität Berlin",
      "urls": [
        { "id": 1, "page_type": "PUB", "url": "https://..." },
        { "id": 2, "page_type": "WP",  "url": "https://..." }
      ],
      "publication_count": 23
    }
  ]
}
```

#### `GET /api/researchers/{id}`

Returns a single researcher with their publications.

### 4.3 Scraping

#### `POST /api/scrape`

Triggers a manual scrape. Returns immediately with a job ID. **Requires authentication** via `X-API-Key` header. Returns `409 Conflict` if a scrape is already running.

**Headers:**
| Header | Required | Description |
|--------|----------|-------------|
| `X-API-Key` | Yes | Must match `SCRAPE_API_KEY` env var |

**Response (201):**
```json
{
  "scrape_id": 15,
  "status": "running",
  "started_at": "2026-03-17T10:00:00Z"
}
```

**Response (409):**
```json
{
  "error": {
    "code": "scrape_in_progress",
    "message": "A scrape is already running (id: 14). Wait for it to complete."
  }
}
```

#### `GET /api/scrape/status`

Returns the status of the most recent scrape and the schedule.

**Response:**
```json
{
  "last_scrape": {
    "id": 14,
    "status": "completed",
    "started_at": "2026-03-16T10:00:00Z",
    "finished_at": "2026-03-16T10:04:32Z",
    "urls_checked": 45,
    "urls_changed": 3,
    "pubs_extracted": 7
  },
  "next_scrape_at": "2026-03-17T10:00:00Z",
  "interval_hours": 24
}
```

### 4.4 Error Responses

All error responses use a standard envelope:

```json
{
  "error": {
    "code": "string",
    "message": "string"
  }
}
```

| HTTP Status | Code | When |
|-------------|------|------|
| 400 | `bad_request` | Invalid query parameters |
| 401 | `unauthorized` | Missing or invalid API key (scrape endpoints) |
| 404 | `not_found` | Resource does not exist |
| 409 | `scrape_in_progress` | Manual scrape requested while one is running |
| 422 | `validation_error` | Request body fails validation |
| 500 | `internal_error` | Unexpected server error |

### 4.5 Security Headers & CORS

**CORS:** Only the frontend origin (`FRONTEND_URL`, default `http://localhost:3000`) is allowed. No wildcard origins.

**Response headers applied via middleware:**

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Content-Security-Policy` | `default-src 'self'` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` (when behind TLS) |

---

## 5. Frontend Design

### 5.1 Pages

#### Newsfeed (`/`)
The main page. Displays publication cards in reverse chronological order (by `discovered_at`).

```
+----------------------------------------------------------+
|  Econ Newsfeed                        [Researchers]       |
+----------------------------------------------------------+
|                                                          |
|  Mar 15, 2026                                            |
|  +------------------------------------------------------+|
|  | Immigration and Wages: Evidence from...              ||
|  | M.F. Steinhardt                                       ||
|  | Journal of Labor Economics, 2024                      ||
|  +------------------------------------------------------+|
|                                                          |
|  Mar 14, 2026                                            |
|  +------------------------------------------------------+|
|  | Trade Shocks and Labor Market Adjustment in...       ||
|  | M.F. Steinhardt, J. Doe                               ||
|  | Working Paper, 2025                                   ||
|  +------------------------------------------------------+|
|                                                          |
|  [Load more]                                             |
+----------------------------------------------------------+
```

**Behavior:**
- Fetches `GET /api/publications?page=1&per_page=20` on load
- Groups publications by discovery date
- "Load more" button appends next page
- Each author name links to the researcher page

#### Researchers (`/researchers`)
A directory of all tracked researchers.

```
+----------------------------------------------------------+
|  Econ Newsfeed                        [Researchers]       |
+----------------------------------------------------------+
|                                                          |
|  Tracked Researchers                                     |
|  +------------------------------------------------------+|
|  | Max Friedrich Steinhardt                              ||
|  | Professor, Freie Universität Berlin                   ||
|  | 23 publications tracked                               ||
|  +------------------------------------------------------+|
|                                                          |
+----------------------------------------------------------+
```

**Behavior:**
- Fetches `GET /api/researchers` on load
- Each card links to `/researchers/[id]` showing that researcher's publications

#### Researcher Detail (`/researchers/[id]`)
Shows a single researcher's profile and their publications.

### 5.2 Component Hierarchy

```
App (layout.tsx)
├── Header
│   ├── Logo / Title
│   └── Navigation (Feed, Researchers)
├── NewsfeedPage
│   ├── PublicationCard[]
│   │   ├── Title
│   │   ├── AuthorList
│   │   ├── VenueAndYear
│   │   └── DiscoveredDate
│   └── LoadMoreButton
├── ResearchersPage
│   └── ResearcherCard[]
│       ├── Name
│       ├── Affiliation
│       └── PublicationCount
└── ResearcherDetailPage
    ├── ResearcherProfile
    └── PublicationCard[]
```

### 5.3 Data Fetching & Caching

The frontend uses React Server Components where possible. Client components are used only for interactive elements (pagination, filtering). Client-side data fetching uses **SWR** for stale-while-revalidate caching, preventing redundant API calls on navigation.

```
lib/api.ts
  - getPublications(page, perPage, filters) -> PaginatedResponse<Publication>
  - getResearchers() -> Researcher[]
  - getResearcher(id) -> ResearcherDetail
```

Environment variable: `NEXT_PUBLIC_API_URL=http://localhost:8000`

**Note:** Next.js must be upgraded to the latest 14.x patch to address known CVEs in 14.2.13 (cache poisoning CVE-2024-46982, auth bypass CVE-2024-51479, middleware bypass CVE-2025-29927). The spurious `@ts-morph/common` dependency should be removed.

---

## 6. Scraping Pipeline

### 6.1 Pipeline (updated)

The scraping pipeline in `html_fetcher.py` and `publication.py` is retained with the following improvements:

1. HTTP fetching with retries, timeout, and **exponential backoff** (retries include 5xx errors, not just timeouts)
2. Text extraction via BeautifulSoup (single parse per URL — no duplicate parsing)
3. SHA-256 change detection as a cheap first gate
4. **Diff-based extraction:** when a change is detected, compute `difflib.unified_diff` between old and new content and send only new/changed lines to the LLM, reducing token usage and improving accuracy
5. **Publication deduplication:** before inserting, check for existing publications with matching normalized title + source URL; skip duplicates
6. OpenAI client instantiated **once at module level** (not per call) to reuse HTTP connection pool
7. `fetch_and_save_if_changed()` returns `True` when content changed, `False` otherwise (was previously `None`)
8. Configurable content truncation (`CONTENT_MAX_CHARS`, default 4000) with logging when truncation occurs and how many characters were dropped
9. LLM output validated with **Pydantic models** before database insertion — malformed or missing fields are rejected rather than causing crashes

### 6.2 Scheduler Integration

```python
# scheduler.py

import threading
from apscheduler.schedulers.background import BackgroundScheduler

_scrape_lock = threading.Lock()

def run_scrape_job():
    """Orchestrates a full scraping cycle. Skips if another scrape is running."""
    if not _scrape_lock.acquire(blocking=False):
        logger.warning("Scrape already in progress, skipping")
        return

    try:
        log_id = create_scrape_log()
        urls = Researcher.get_all_researcher_urls()
        urls_checked = 0
        urls_changed = 0
        pubs_extracted = 0

        for url_id, researcher_id, url, page_type in urls:
            urls_checked += 1
            changed = HTMLFetcher.fetch_and_save_if_changed(url_id, url, researcher_id)
            if changed and page_type in ("PUB", "WP"):
                urls_changed += 1
                old_text = HTMLFetcher.get_previous_text(url_id)
                new_text = HTMLFetcher.get_latest_text(url_id)
                diff_text = compute_diff(old_text, new_text) if old_text else new_text
                pubs = Publication.extract_publications(diff_text, url)
                if pubs:
                    Publication.save_publications(url, pubs)
                    pubs_extracted += len(pubs)

        update_scrape_log(log_id, "completed", urls_checked, urls_changed, pubs_extracted)
    except Exception as e:
        update_scrape_log(log_id, "failed", error_message=str(e))
    finally:
        _scrape_lock.release()
```

The `_scrape_lock` prevents overlapping manual (`POST /api/scrape`) and scheduled scrapes from running concurrently, avoiding duplicate publications and excessive resource usage.

### 6.3 Schedule Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `SCRAPE_INTERVAL_HOURS` | 24 | Hours between scraping runs |
| `SCRAPE_ON_STARTUP` | false | Run a scrape immediately on app start |

### 6.4 Scraping Safety

| Control | Description |
|---------|-------------|
| **URL validation** | Reject non-HTTP(S) schemes, internal/private IPs (10.x, 172.16-31.x, 192.168.x, 169.254.x), and AWS metadata endpoints before fetching. Prevents SSRF via database-stored URLs. |
| **HTTPS preference** | Upgrade HTTP URLs to HTTPS where the server supports it. Log warnings for HTTP-only fetches. |
| **robots.txt compliance** | Check `robots.txt` before fetching; skip URLs disallowed for the scraper's User-Agent. |
| **Per-domain rate limiting** | Wait `SCRAPE_RATE_LIMIT_SECONDS` (default 2) between requests to the same domain. |
| **Exponential backoff** | Retry on timeout and 5xx errors with exponential backoff (1s, 2s, 4s). Max 3 retries. |
| **Content size limit** | Reject HTTP responses larger than 1 MB to prevent storage abuse. |

---

## 7. Project Structure (Target)

```
econ-newsfeed/
├── api.py                  # FastAPI application and route definitions
├── scheduler.py            # APScheduler job and integration
├── database.py             # Database class (existing, extended)
├── db_config.py            # DB connection config (existing)
├── html_fetcher.py         # Web scraping (existing)
├── publication.py          # OpenAI extraction (existing)
├── researcher.py           # Researcher queries (existing)
├── main.py                 # CLI interface (existing, retained)
├── requirements.txt        # Python dependencies (updated)
├── urls.csv                # Sample data (existing)
├── .env                    # Environment variables (not committed)
├── .env.example            # Documented env var template
├── .gitignore
├── docker-compose.yml      # MySQL + API + Frontend
├── Dockerfile.api          # Python API container
├── app/                    # Next.js frontend
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx            # Newsfeed page
│   │   │   ├── layout.tsx          # Root layout with navigation
│   │   │   ├── globals.css         # Global styles
│   │   │   └── researchers/
│   │   │       ├── page.tsx        # Researcher directory
│   │   │       └── [id]/
│   │   │           └── page.tsx    # Researcher detail
│   │   └── lib/
│   │       └── api.ts              # API client functions
│   ├── Dockerfile                  # Frontend container
│   ├── package.json
│   ├── tailwind.config.ts
│   └── tsconfig.json
├── DESIGN.md               # This document
└── README.md
```

---

## 8. Configuration and Environment

### 8.1 Environment Variables

All required variables are validated at startup — the application **fails fast** with a clear error message if any are missing or empty.

```bash
# Database (required)
DB_HOST=localhost
DB_USER=econ_app                    # non-root user (see Section 10)
DB_PASSWORD=secret
DB_NAME=econ_newsfeed

# OpenAI (required)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini           # configurable model (default: gpt-4o-mini)

# Scraping
SCRAPE_INTERVAL_HOURS=24            # hours between scheduled scrapes
SCRAPE_ON_STARTUP=false             # run a scrape immediately on app start
SCRAPE_API_KEY=changeme             # required for POST /api/scrape
SCRAPE_RATE_LIMIT_SECONDS=2         # per-domain delay between requests
CONTENT_MAX_CHARS=4000              # max characters sent to LLM per page

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000  # CORS allowed origin
```

A `.env.example` file with all variables and comments is committed to the repository.

### 8.2 Dependencies (additions to requirements.txt)

All dependencies are **pinned to specific versions** for reproducible builds.

```
fastapi==0.115.*
uvicorn[standard]==0.34.*
apscheduler==3.10.*
pydantic==2.10.*
```

`pydantic` is added for validating LLM output before database insertion.

---

## 9. Local Development

### 9.1 Prerequisites

- Python 3.12+
- Node.js 18+ and npm
- MySQL 8 (installed locally or via Docker one-liner)
- OpenAI API key

### 9.2 Quick Start (CLI Pipeline — works now)

These steps work with the current codebase before any implementation phases:

```bash
# 1. Clone and configure
git clone <repo> && cd econ-newsfeed
cp .env.example .env   # fill in DB creds and OPENAI_API_KEY

# 2. Start MySQL (pick one)
# Option A: Docker (recommended)
docker run -d --name econ-mysql \
  -e MYSQL_ROOT_PASSWORD=secret \
  -e MYSQL_DATABASE=econ_newsfeed \
  -p 3306:3306 mysql:8

# Option B: Local MySQL install — create the database manually

# 3. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. Create tables and seed data
python database.py        # creates DB, tables, imports urls.csv

# 5. Run the CLI pipeline
python main.py            # interactive menu: fetch HTML, extract publications
```

### 9.3 Full Stack (API + Frontend — requires Phases 1-4)

These steps only work after `api.py`, `scheduler.py`, and frontend pages are implemented per Section 14 Phases 1-4.

```bash
# After Phase 2 is complete:
# 6. Start the API (with hot reload)
uvicorn api:app --reload --port 8000

# After Phase 3 is complete:
# 7. Start the frontend (in a separate terminal)
cd app && npm install && npm run dev -- -p 3000

# 8. Open http://localhost:3000
# 9. View API docs at http://localhost:8000/docs
```

### 9.4 Running the Scraping Pipeline

- **Now (CLI):** `python main.py` → menu options to fetch HTML and extract publications
- **After Phase 4 (API):** `curl -X POST http://localhost:8000/api/scrape -H "X-API-Key: $SCRAPE_API_KEY"`
- **Check status (API):** `curl http://localhost:8000/api/scrape/status`

> **Note:** The CLI pipeline currently has known issues (see Section 13). `fetch_and_save_if_changed()` returns `None` instead of `bool`, so the scheduler integration won't trigger extraction until Phase 1 fixes are applied.

### 9.5 Useful Development Commands

| Task | Command | Requires |
|------|---------|----------|
| Start MySQL (Docker) | `docker run -d --name econ-mysql -e MYSQL_ROOT_PASSWORD=secret -e MYSQL_DATABASE=econ_newsfeed -p 3306:3306 mysql:8` | Docker |
| Create DB + tables + seed | `python database.py` | Now |
| Run CLI pipeline | `python main.py` | Now |
| Start API (hot reload) | `uvicorn api:app --reload --port 8000` | Phase 2 |
| Start frontend (dev) | `cd app && npm run dev -- -p 3000` | Phase 3 |
| Trigger scrape (API) | `curl -X POST localhost:8000/api/scrape -H "X-API-Key: ..."` | Phase 4 |
| View API docs | `http://localhost:8000/docs` | Phase 2 |
| Reset database | `python database.py` (drops and recreates) | Now |

### 9.6 Environment Variable Reference

See Section 8.1 for the full list. Local-specific defaults:

- `DB_HOST=localhost` (not `db` as in Docker Compose)
- `FRONTEND_URL=http://localhost:3000`
- `NEXT_PUBLIC_API_URL=http://localhost:8000`

> **Note:** `OPENAI_MODEL` env var is not yet respected — model is hardcoded in `publication.py` until Phase 1 fixes.

### 9.7 Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `Can't connect to MySQL server on 'localhost'` | MySQL not running or wrong port | Check `docker ps` or `mysqladmin ping`; verify `DB_HOST`/`DB_PASSWORD` in `.env` |
| Port 3306 already in use | Existing MySQL instance | Stop it (`brew services stop mysql`) or use a different port (`-p 3307:3306`) and update `.env` |
| Port 3000/8000 already in use | Another dev server running | Kill the process (`lsof -ti:3000 \| xargs kill`) or use a different `--port` |
| CORS error in browser console | `FRONTEND_URL` doesn't match actual frontend port | Ensure `FRONTEND_URL` in `.env` matches the port Next.js is running on |
| `ModuleNotFoundError: No module named 'fastapi'` | API deps not installed / Phase 2 not complete | Run `pip install -r requirements.txt` after Phase 2 adds FastAPI deps |
| Default Next.js page instead of newsfeed | Frontend pages not built yet | Expected — custom pages are implemented in Phase 3 |

---

## 10. Deployment

### 10.1 Docker Compose

Three services. The API uses a **non-root MySQL user** (`econ_app`) with access only to the application database. Environment variables are scoped per service — only the DB service sees the root password.

```yaml
services:
  db:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: ${DB_NAME}
      MYSQL_USER: ${DB_USER}
      MYSQL_PASSWORD: ${DB_PASSWORD}
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql

  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    ports:
      - "8000:8000"
    depends_on:
      - db
    environment:
      DB_HOST: db
      DB_USER: ${DB_USER}
      DB_PASSWORD: ${DB_PASSWORD}
      DB_NAME: ${DB_NAME}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      OPENAI_MODEL: ${OPENAI_MODEL:-gpt-4o-mini}
      SCRAPE_API_KEY: ${SCRAPE_API_KEY}
      SCRAPE_INTERVAL_HOURS: ${SCRAPE_INTERVAL_HOURS:-24}
      FRONTEND_URL: http://frontend:3000

  frontend:
    build:
      context: ./app
    ports:
      - "3000:3000"
    depends_on:
      - api
    environment:
      NEXT_PUBLIC_API_URL: http://api:8000
```

### 10.2 Startup Sequence

1. MySQL starts and initializes the database
2. API starts, runs `Database.create_tables()`, mounts the scheduler
3. Frontend starts and connects to the API
4. (Optional) Seed script imports `urls.csv` if the researchers table is empty

---

## 11. Key Design Decisions

### 11.1 FastAPI over Flask
FastAPI provides automatic OpenAPI docs, built-in async support, and native Pydantic validation. It integrates cleanly with APScheduler's background scheduler and requires less boilerplate.

### 11.2 Scheduler in-process vs. separate worker
For the MVP, the scheduler runs inside the FastAPI process using APScheduler's `BackgroundScheduler`. This avoids the operational complexity of a separate worker process or a task queue (Celery, Redis). The trade-off is that the scraping job shares resources with the API server, but at MVP scale (tens of researcher URLs) this is acceptable.

### 11.3 Change detection via content hashing
The existing SHA-256 hashing approach is retained. It avoids unnecessary OpenAI API calls (and their cost) when a page hasn't changed. The hash is computed on the extracted text (not raw HTML), making it resilient to minor HTML changes that don't affect content.

### 11.4 LLM for extraction vs. rule-based parsing
Economist personal websites have wildly inconsistent formats. Rule-based parsers would need per-site maintenance. The OpenAI-based approach handles format variation out of the box. The 4000-character content limit controls API cost while covering the publication list on most pages.

### 11.5 Same-source publication deduplication (MVP)
When a page changes, re-extraction previously inserted duplicates of every existing publication. The MVP now deduplicates within the same source URL using normalized title matching (`UNIQUE(title(200), url(200))`). Cross-researcher deduplication (co-authored papers appearing on different websites) is deferred to post-MVP, where title similarity matching and DOI lookup can be added.

### 11.6 Diff-based extraction over full-page re-extraction
Sending the full page text to the LLM on every change wastes tokens — most of the content is unchanged. By computing a text diff and sending only new/changed lines, we reduce API cost and improve extraction accuracy (the LLM focuses on genuinely new publications rather than re-parsing known ones).

### 11.7 Upsert for html_content
The original append-only design stored a new full-text row on every content change, causing unbounded table growth. Since only the latest snapshot is ever used, `html_content` now uses an upsert (one row per `url_id` via `INSERT ... ON DUPLICATE KEY UPDATE`). This caps storage at one row per tracked URL.

### 11.8 Pydantic validation at trust boundaries
LLM output is inherently untrusted — malformed JSON, missing keys, or wrong types can crash the pipeline or corrupt the database. All OpenAI extraction results are validated through Pydantic models before insertion. This also defends against indirect prompt injection via scraped HTML content.

---

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| OpenAI API returns malformed JSON | Publications not extracted | Pydantic validation rejects bad output; `invalid_json_dumps/` logging retained. Post-MVP: switch to structured output mode. |
| Researcher website blocks scraper | Missing data for that researcher | Custom User-Agent, respect `robots.txt`, per-domain rate limiting (`SCRAPE_RATE_LIMIT_SECONDS`) |
| OpenAI API cost at scale | Unexpected bills | SHA-256 change detection + diff-based extraction minimize tokens sent; `CONTENT_MAX_CHARS` caps per-call cost |
| Duplicate publications in feed | Cluttered newsfeed | Same-source dedup via `UNIQUE(title, url)` constraint. Cross-researcher dedup deferred to post-MVP. |
| Database connection exhaustion | API errors under load | Per-query connections acceptable for MVP; post-MVP add `MySQLConnectionPool` or SQLAlchemy pool |
| Scraping job blocks API | Slow API responses during scrape | APScheduler runs in a background thread; API remains responsive |
| SSRF via stored URLs | Internal network exposure | URL validation rejects non-HTTP(S) schemes, private IPs, and metadata endpoints before fetching |
| LLM prompt injection via scraped HTML | Corrupted publication data | Pydantic validation of LLM output; content size limit prevents oversized payloads |
| Overlapping scrapes (manual + scheduled) | Duplicate publications, resource contention | Threading lock prevents concurrent scrape runs; API returns 409 if scrape is in progress |
| API abuse on scrape endpoint | Excessive OpenAI costs, outbound traffic | `X-API-Key` authentication required for `POST /api/scrape` |

---

## 13. Known Technical Debt

Items accepted for MVP but tracked for post-MVP improvement:

| Item | Severity | Notes |
|------|----------|-------|
| No connection pooling | Low | Per-query connections work at MVP scale (~50 URLs). Post-MVP: switch to `MySQLConnectionPool` or SQLAlchemy. |
| Sequential URL processing | Medium | Scraping is fully serial. Post-MVP: add `asyncio`/`concurrent.futures` for parallel fetching. |
| In-process scheduler | Low | APScheduler shares the FastAPI process. Post-MVP: move to a separate worker (Celery/Redis). |
| No test suite | High | Zero test files exist. Post-MVP: add unit tests (extraction, dedup), integration tests (DB), and API endpoint tests. |
| Static-method-only classes | Medium | All classes use `@staticmethod` — prevents dependency injection and test doubles. Post-MVP: refactor to instance methods with FastAPI `Depends()`. |
| Cross-researcher publication dedup | Low | Same paper by co-authors appears as separate entries. Post-MVP: title similarity + DOI lookup. |
| No `robots.txt` caching | Low | `robots.txt` fetched on every scrape cycle. Post-MVP: cache with TTL. |

---

## 14. Implementation Plan

### Phase 1: Pipeline Fixes (backend)
1. Create `.env.example` with all variables from Section 8.1
2. Fix `RETURNING` clause in `researcher.py` — replace with MySQL-compatible `INSERT` + `LAST_INSERT_ID()`
3. Fix `fetch_and_save_if_changed()` to return `bool`
4. Add publication deduplication (title + URL check before insert)
5. Add Pydantic models for LLM output validation
6. Instantiate OpenAI client once at module level
7. Add startup validation for required env vars (`db_config.py`)
8. Pin all dependencies in `requirements.txt`; remove duplicate `python-dotenv`
9. Add `add_url_to_researcher()` missing `page_type` parameter

### Phase 2: API Layer (backend)
1. Create `api.py` with FastAPI app
2. Implement `GET /api/publications` with pagination
3. Implement `GET /api/researchers` and `GET /api/researchers/{id}`
4. Add CORS middleware (scoped to `FRONTEND_URL`)
5. Add security headers middleware
6. Add standard error response envelope
7. Add `X-API-Key` auth for scrape endpoints
8. Update `requirements.txt` with `fastapi`, `uvicorn`, `pydantic`

### Phase 3: Newsfeed Frontend
1. Upgrade Next.js to latest 14.x patch; remove `@ts-morph/common`
2. Install SWR for client-side caching
3. Create `app/src/lib/api.ts` fetch client
4. Replace `app/src/app/page.tsx` with newsfeed page
5. Build `PublicationCard` component
6. Add pagination ("Load more")
7. Update `layout.tsx` with navigation header
8. Create `/researchers` page
9. Create `/researchers/[id]` detail page

### Phase 4: Automated Scraping
1. Create `scheduler.py` with scrape job and concurrency lock
2. Add `scrape_log` table and indexes to `database.py`
3. Add diff-based extraction (`difflib.unified_diff`)
4. Add URL validation (SSRF protection) and `robots.txt` compliance
5. Add per-domain rate limiting and exponential backoff for retries
6. Add content size limit (1 MB)
7. Add truncation logging
8. Integrate scheduler into `api.py` on startup
9. Add `POST /api/scrape` (with 409 guard) and `GET /api/scrape/status`

### Phase 5: Schema & Data Fixes
1. Widen `url` columns to `VARCHAR(2048)`
2. Remove redundant `url` column from `html_content`
3. Add `UNIQUE(url_id)` on `html_content`; migrate `save_text()` to upsert
4. Add `UNIQUE(title(200), url(200))` on `publications`
5. Add secondary indexes (see Section 3.1)

### Phase 6: Deployment
1. Write `Dockerfile.api` and `app/Dockerfile`
2. Write `docker-compose.yml` with non-root MySQL user
3. Add `Makefile` with targets: `make setup`, `make dev`, `make seed`, `make reset-db`
4. Add seed script for first-run data import
5. Add loading/error states to frontend
6. End-to-end testing
