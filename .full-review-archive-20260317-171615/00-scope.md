# Review Scope

## Target

Full econ-newsfeed project — a web scraping system for economics research papers that fetches HTML from economist personal websites, uses OpenAI to extract publication metadata, and stores results in MySQL. Includes a Next.js frontend (currently default template) and a design document for the planned MVP architecture.

## Files

### Python Backend
- `main.py` — CLI entry point with menu-driven interface
- `database.py` — MySQL database layer (connection, queries, table creation, CSV import)
- `db_config.py` — Database configuration via environment variables
- `html_fetcher.py` — Web scraping with retry logic and SHA-256 change detection
- `publication.py` — OpenAI-based publication extraction and database storage
- `researcher.py` — Researcher data access layer

### Configuration & Data
- `requirements.txt` — Python dependencies
- `urls.csv` — Sample researcher data
- `.env` (expected, not committed) — Environment variables

### Next.js Frontend (`app/`)
- `app/src/app/page.tsx` — Home page (default Next.js template)
- `app/src/app/layout.tsx` — Root layout (default Next.js template)
- `app/src/app/globals.css` — Global styles
- `app/package.json` — Frontend dependencies (Next.js 14, React 18, Tailwind)
- `app/tailwind.config.ts` — Tailwind configuration
- `app/tsconfig.json` — TypeScript configuration

### Documentation
- `README.md` — Project overview and setup instructions
- `DESIGN.md` — MVP design document (API, frontend, scheduler, deployment)

## Flags

- Security Focus: no
- Performance Critical: no
- Strict Mode: no
- Framework: FastAPI + Next.js (auto-detected from DESIGN.md; backend currently CLI-based)

## Review Phases

1. Code Quality & Architecture
2. Security & Performance
3. Testing & Documentation
4. Best Practices & Standards
5. Consolidated Report
