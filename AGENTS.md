# AGENTS.md

For a full architecture, command, and deployment reference see `CLAUDE.md`. This file only adds notes specific to running the project inside a Cursor Cloud agent VM.

## Cursor Cloud specific instructions

### Services & how to run them
This is a monorepo with three services (details and commands in `CLAUDE.md`):
- **MySQL 8** — local server (installed via apt, not Docker here). Data persists in the VM snapshot.
- **Backend API (FastAPI/uvicorn)** — local dev runs on **:8001** (`make dev`), not :8000.
- **Frontend (Next.js)** — runs on **:3000** (`make dev`), proxies `/api/*` to `API_INTERNAL_URL`.

Standard commands live in the `Makefile` (`make dev`, `make seed`, `make check`, etc.) and `app/package.json`. Don't duplicate them; use them.

### Startup caveats (non-obvious)
- **MySQL is not auto-started on boot.** Run `sudo service mysql start` at the beginning of a session before seeding or starting the API (Docker is not used in this environment).
- **Injected secrets override `.env`.** The VM injects `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `GOOGLE_API_KEY`, and `SCRAPE_API_KEY` as environment variables. `backend/config.py` calls `load_dotenv()` which does **not** override existing env vars, so the injected values win over anything in `.env`. The local MySQL has been provisioned with a user/database matching the injected `DB_USER`/`DB_PASSWORD`/`DB_NAME` so the app connects locally. `DB_HOST` is read from `.env` (`127.0.0.1`). If injected DB credentials ever change and auth fails, re-provision the local user:
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
  Then `make seed` (idempotent) to create tables.
- **tmux sessions must inherit the injected secrets.** A pre-existing tmux server may have been started before secrets were injected; new sessions then inherit that stale env and the API fails with `Access denied for user ...`. If that happens, run `tmux kill-server` and recreate the session from a fresh shell (which has the injected secrets).
- **The injected `GOOGLE_API_KEY` is not guaranteed to be a valid Google AI Studio key.** The API/frontend serve existing DB data without the LLM, but the live extraction pipeline (`make extract` / `make scrape`) needs a valid key. Without one, `make fetch` still works (downloads HTML); only LLM extraction fails.

### Testing caveat (non-obvious)
- `tests/conftest.py` sets its own test env via `os.environ.setdefault(...)`, so the **injected secrets override the test defaults** and break auth-sensitive tests (e.g. `test_admin_dashboard`). Run the Python suite with the injected vars unset:
  ```bash
  env -u SCRAPE_API_KEY -u GOOGLE_API_KEY -u DB_USER -u DB_PASSWORD -u DB_NAME poetry run pytest
  ```
  The API contract tests mock the DB, so unsetting these is safe. Frontend checks (`cd app && npx tsc --noEmit`, `npx next lint`, `npx jest`) need no special handling.
