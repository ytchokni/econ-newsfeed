# AGENTS.md

For full architecture, commands, and deployment details see `CLAUDE.md`. This file covers what cloud agents need to know beyond that.

## Connecting to the production database

Production runs on Hetzner (MySQL inside Docker). Cloud agents connect via SSH tunnel:

```bash
mkdir -p ~/.ssh
echo "$HETZNER_SSH_KEY" > ~/.ssh/hetzner && chmod 600 ~/.ssh/hetzner
ssh -i ~/.ssh/hetzner -o StrictHostKeyChecking=no -L 3306:localhost:3306 root@167.233.132.217 -fN
export DB_HOST=127.0.0.1
```

Verify:
```bash
poetry run python -c "from backend.database.connection import get_connection; c = get_connection(); c.close(); print('OK')"
```

Required secrets (injected as env vars via Cursor secrets vault): `HETZNER_SSH_KEY`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `SCRAPE_API_KEY`, `GOOGLE_API_KEY`.

## Querying the database

No ORM — use the connection module directly:

```python
from backend.database.connection import fetch_all, fetch_one

rows = fetch_all("SELECT id, title, venue, status FROM papers WHERE status = %s LIMIT 10", ("working_paper",))
row = fetch_one("SELECT COUNT(*) AS n FROM feed_events")
```

Key tables: `papers`, `feed_events`, `authorship`, `researchers`, `researcher_urls`, `html_content`, `paper_snapshots`, `scrape_log`, `llm_usage`. See `backend/database/schema.py` for full DDL.

## Running data quality checks

The deterministic test suite checks invariants against whichever database is configured:

```bash
poetry run pytest tests_data_quality/ -v --tb=short
```

Each failure identifies bad rows (not code bugs). The tests cover: feed event integrity, paper field quality, enrichment hygiene, researcher duplicates, and pipeline liveness (when `DATA_QUALITY_LIVE=1`).

## Hitting the production API

The backend API is at `https://econ-newsfeed.duckdns.org`. Admin endpoints require the API key:

```bash
curl -H "X-API-Key: $SCRAPE_API_KEY" https://econ-newsfeed.duckdns.org/api/admin/dashboard
```

The feed is at `/api/publications` (public, no auth needed).

## Domain knowledge for auditing

This project tracks economics researchers' publications. Key concepts:

- **Status progression**: working_paper → revise_and_resubmit → accepted → published. Status only moves forward.
- **Venues** are either journals (peer-reviewed, where R&R/acceptance happens) or working paper series (NBER, CEPR, IZA, RIETI, SSRN, CESifo, IMF, ECB, World Bank — these distribute papers but don't peer-review).
- A status of R&R or accepted **at a working paper series** is an extraction error — you can't R&R at a discussion paper series.
- A status of "working_paper" **at a known journal** is suspicious — the paper is likely published or accepted there.
- `feed_events` drive the newsfeed UI. Bogus events (wrong status, hallucinated papers, junk titles) are user-visible.

## Local development (Cursor Cloud VM)

For agents doing dev work (not prod auditing), a local MySQL is available:

- **Start MySQL**: `sudo service mysql start` (not auto-started on boot; not Docker)
- **Seed schema**: `poetry run python -c "from backend.database import create_database, create_tables; create_database(); create_tables()"`
- **Start dev servers**: Backend on :8001, frontend on :3000
  ```bash
  poetry run uvicorn backend.api:app --reload --port 8001 &
  cd app && API_INTERNAL_URL=http://localhost:8001 npm run dev &
  ```
- **Run tests**:
  ```bash
  # Python (unset injected secrets so test defaults apply)
  env -u SCRAPE_API_KEY -u GOOGLE_API_KEY -u DB_USER -u DB_PASSWORD -u DB_NAME poetry run pytest
  # Frontend
  cd app && npx tsc --noEmit && npx jest
  ```

### Non-obvious caveats

- **Injected secrets override `.env`**: `load_dotenv()` does not override existing env vars. The local MySQL user/database must match the injected `DB_USER`/`DB_PASSWORD`/`DB_NAME`. If auth fails after credential changes, re-provision:
  ```bash
  sudo mysql -e "CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
    CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
    ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
    ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
    GRANT ALL PRIVILEGES ON *.* TO '${DB_USER}'@'127.0.0.1' WITH GRANT OPTION;
    GRANT ALL PRIVILEGES ON *.* TO '${DB_USER}'@'localhost' WITH GRANT OPTION;
    FLUSH PRIVILEGES;"
  ```
- **tmux stale env**: If a tmux server predates secret injection, sessions have stale env. Fix: `tmux kill-server` and recreate from a fresh shell.
- **`GOOGLE_API_KEY`** powers LLM extraction (Gemini). The API/frontend work without it; only extraction needs it.
