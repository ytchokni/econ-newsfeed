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
| Frontend | Next.js 14, React 18, TypeScript, Tailwind CSS | Renders the newsfeed and researcher pages |
| Backend API | FastAPI, Uvicorn | Serves data to the frontend, manages scrape triggers |
| Scheduler | APScheduler (BackgroundScheduler) | Runs the scraping pipeline on a cron-like interval |
| Scraping pipeline | requests, BeautifulSoup, OpenAI API | Fetches HTML, detects changes, extracts publications |
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

The existing schema is retained with one addition: a `scrape_log` table for tracking scrape runs.

### 3.1 Existing Tables

```sql
researchers (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    last_name       VARCHAR(255) NOT NULL,
    first_name      VARCHAR(255) NOT NULL,
    position        VARCHAR(255),
    affiliation     VARCHAR(255)
)

researcher_urls (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    researcher_id   INT REFERENCES researchers(id),
    page_type       VARCHAR(255) NOT NULL,    -- PUB, WP, HOME
    url             VARCHAR(255) NOT NULL
)

publications (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    url             VARCHAR(255),             -- source URL
    title           TEXT,
    year            VARCHAR(4),
    venue           TEXT,
    timestamp       DATETIME                  -- when discovered
)

html_content (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    url_id          INT REFERENCES researcher_urls(id),
    url             VARCHAR(255),
    content         LONGTEXT,
    content_hash    VARCHAR(64),              -- SHA-256
    timestamp       DATETIME,
    researcher_id   INT REFERENCES researchers(id)
)

authorship (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    researcher_id   INT REFERENCES researchers(id),
    publication_id  INT REFERENCES publications(id),
    author_order    INT
)
```

### 3.2 New Table

```sql
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

### 3.3 Entity Relationship Diagram

```
researchers 1───* researcher_urls
researchers 1───* authorship
publications 1───* authorship
researcher_urls 1───* html_content
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

Triggers a manual scrape. Returns immediately with a job ID.

**Response:**
```json
{
  "scrape_id": 15,
  "status": "running",
  "started_at": "2026-03-17T10:00:00Z"
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

### 5.3 Data Fetching

The frontend uses React Server Components where possible. Client components are used only for interactive elements (pagination, filtering).

```
lib/api.ts
  - getPublications(page, perPage, filters) -> PaginatedResponse<Publication>
  - getResearchers() -> Researcher[]
  - getResearcher(id) -> ResearcherDetail
```

Environment variable: `NEXT_PUBLIC_API_URL=http://localhost:8000`

---

## 6. Scraping Pipeline

### 6.1 Existing Pipeline (retained)

The current scraping pipeline in `html_fetcher.py` and `publication.py` is preserved as-is. It already handles:

1. HTTP fetching with retries and timeout
2. Text extraction via BeautifulSoup
3. SHA-256 change detection
4. OpenAI-based publication extraction
5. Structured storage with authorship links

### 6.2 Scheduler Integration

```python
# scheduler.py

from apscheduler.schedulers.background import BackgroundScheduler

def run_scrape_job():
    """Orchestrates a full scraping cycle."""
    log_id = create_scrape_log()
    try:
        urls = Researcher.get_all_researcher_urls()
        urls_checked = 0
        urls_changed = 0
        pubs_extracted = 0

        for url_id, researcher_id, url, page_type in urls:
            urls_checked += 1
            changed = HTMLFetcher.fetch_and_save_if_changed(url_id, url, researcher_id)
            if changed and page_type in ("PUB", "WP"):
                urls_changed += 1
                text = HTMLFetcher.get_latest_text(url_id)
                pubs = Publication.extract_publications(text, url)
                if pubs:
                    Publication.save_publications(url, pubs)
                    pubs_extracted += len(pubs)

        update_scrape_log(log_id, "completed", urls_checked, urls_changed, pubs_extracted)
    except Exception as e:
        update_scrape_log(log_id, "failed", error_message=str(e))
```

### 6.3 Schedule Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `SCRAPE_INTERVAL_HOURS` | 24 | Hours between scraping runs |
| `SCRAPE_ON_STARTUP` | false | Run a scrape immediately on app start |

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

```bash
# Database
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=secret
DB_NAME=econ_newsfeed

# OpenAI
OPENAI_API_KEY=sk-...

# Scheduler
SCRAPE_INTERVAL_HOURS=24
SCRAPE_ON_STARTUP=false

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 8.2 Dependencies (additions to requirements.txt)

```
fastapi
uvicorn[standard]
apscheduler
```

---

## 9. Deployment

### 9.1 Docker Compose

Three services:

```yaml
services:
  db:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_PASSWORD}
      MYSQL_DATABASE: ${DB_NAME}
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
    env_file: .env

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

### 9.2 Startup Sequence

1. MySQL starts and initializes the database
2. API starts, runs `Database.create_tables()`, mounts the scheduler
3. Frontend starts and connects to the API
4. (Optional) Seed script imports `urls.csv` if the researchers table is empty

---

## 10. Key Design Decisions

### 10.1 FastAPI over Flask
FastAPI provides automatic OpenAPI docs, built-in async support, and native Pydantic validation. It integrates cleanly with APScheduler's background scheduler and requires less boilerplate.

### 10.2 Scheduler in-process vs. separate worker
For the MVP, the scheduler runs inside the FastAPI process using APScheduler's `BackgroundScheduler`. This avoids the operational complexity of a separate worker process or a task queue (Celery, Redis). The trade-off is that the scraping job shares resources with the API server, but at MVP scale (tens of researcher URLs) this is acceptable.

### 10.3 Change detection via content hashing
The existing SHA-256 hashing approach is retained. It avoids unnecessary OpenAI API calls (and their cost) when a page hasn't changed. The hash is computed on the extracted text (not raw HTML), making it resilient to minor HTML changes that don't affect content.

### 10.4 LLM for extraction vs. rule-based parsing
Economist personal websites have wildly inconsistent formats. Rule-based parsers would need per-site maintenance. The OpenAI-based approach handles format variation out of the box. The 4000-character content limit controls API cost while covering the publication list on most pages.

### 10.5 No publication deduplication (MVP)
The same publication may appear on multiple researchers' pages (co-authored work). The MVP does not deduplicate these entries. Post-MVP, deduplication can be added using title similarity matching and DOI lookup.

---

## 11. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| OpenAI API returns malformed JSON | Publications not extracted | Existing regex fallback + `invalid_json_dumps/` logging. Post-MVP: switch to structured output mode. |
| Researcher website blocks scraper | Missing data for that researcher | Custom User-Agent, respect robots.txt, add per-site rate limiting |
| OpenAI API cost at scale | Unexpected bills | SHA-256 change detection prevents redundant calls; 4000-char limit caps per-call cost |
| Duplicate publications in feed | Cluttered newsfeed | Accept for MVP; post-MVP add title-based dedup |
| Database connection exhaustion | API errors under load | Current per-query connection pattern is fine for MVP; post-MVP add connection pooling |
| Scraping job blocks API | Slow API responses during scrape | APScheduler runs in a background thread; API remains responsive |

---

## 12. Implementation Plan

### Phase 1: API Layer (backend)
1. Create `api.py` with FastAPI app
2. Implement `GET /api/publications` with pagination
3. Implement `GET /api/researchers` and `GET /api/researchers/{id}`
4. Add CORS middleware
5. Update `requirements.txt`
6. Test endpoints against existing database

### Phase 2: Newsfeed Frontend
1. Create `app/src/lib/api.ts` fetch client
2. Replace `app/src/app/page.tsx` with newsfeed page
3. Build `PublicationCard` component
4. Add pagination ("Load more")
5. Update `layout.tsx` with navigation header
6. Create `/researchers` page
7. Create `/researchers/[id]` detail page

### Phase 3: Automated Scraping
1. Create `scheduler.py` with scrape job
2. Add `scrape_log` table to `database.py`
3. Modify `html_fetcher.py` to return whether content changed
4. Integrate scheduler into `api.py` on startup
5. Add `POST /api/scrape` and `GET /api/scrape/status`

### Phase 4: Deployment
1. Write `Dockerfile.api` and `app/Dockerfile`
2. Write `docker-compose.yml`
3. Add seed script for first-run data import
4. Add loading/error states to frontend
5. End-to-end testing
