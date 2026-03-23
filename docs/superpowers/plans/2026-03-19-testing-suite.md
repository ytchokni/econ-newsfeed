# Testing Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `make check` pre-flight command and low-effort high-value tests that catch "make dev breaks" failures before they reach the dev loop.

**Architecture:** New Makefile target runs env validation, pytest, tsc, and jest in sequence. Three new test files cover module imports, db_config validation, and API response shape contracts. One new script validates `.env` contents.

**Tech Stack:** pytest, FastAPI TestClient, importlib, python-dotenv, TypeScript compiler

**Spec:** `docs/superpowers/specs/2026-03-19-testing-suite-design.md`

---

### Task 1: `scripts/check_env.py` — Env validation script

**Files:**
- Create: `scripts/check_env.py`

- [ ] **Step 1: Write the script**

```python
"""Validate .env file has all required variables with correct constraints.

Reads .env via dotenv_values() (no side effects on os.environ).
Exits 0 if valid, 1 with descriptive errors if not.
"""
import re
import sys
from dotenv import dotenv_values

REQUIRED_VARS = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "OPENAI_API_KEY", "SCRAPE_API_KEY"]
DB_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def main() -> int:
    values = dotenv_values(".env")
    if not values:
        print("ERROR: .env file not found or empty. Copy .env.example to .env and fill in values.")
        return 1

    errors = []
    for var in REQUIRED_VARS:
        if not values.get(var):
            errors.append(f"  Missing or empty: {var}")

    db_name = values.get("DB_NAME", "")
    if db_name and not DB_NAME_RE.match(db_name):
        errors.append(f"  Invalid DB_NAME '{db_name}': must match ^[a-zA-Z_][a-zA-Z0-9_]{{0,63}}$")

    scrape_key = values.get("SCRAPE_API_KEY", "")
    if scrape_key and len(scrape_key) < 16:
        errors.append(f"  SCRAPE_API_KEY is too short ({len(scrape_key)} chars, min 16). "
                      "The default 'changeme' from .env.example is not valid.")

    if errors:
        print("ENV VALIDATION FAILED:")
        for e in errors:
            print(e)
        return 1

    print("ENV OK: all required variables present and valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Test the script manually**

Run: `.venv/bin/python scripts/check_env.py`
Expected: `ENV OK: all required variables present and valid.` (if your `.env` is configured) or a descriptive error.

- [ ] **Step 3: Commit**

```bash
git add scripts/check_env.py
git commit -m "feat: add env validation script for make check"
```

---

### Task 2: `tests/test_imports.py` — Module import smoke tests

**Files:**
- Create: `tests/test_imports.py`

- [ ] **Step 1: Write the test file**

```python
"""Smoke tests: verify every backend module imports without error.

Catches syntax errors, missing dependencies, circular imports, and
module-level code that crashes. This is the #1 safety net for
"tests pass but make dev fails" regressions.

Requires conftest.py env vars (set via os.environ.setdefault).
load_dotenv(override=False) won't overwrite them.
"""
import importlib
import sys
from unittest.mock import patch, MagicMock

import pytest

# All .py files in project root, as module names (no .py suffix).
# Excludes: __pycache__, tests/, .venv/, scripts/
ROOT_MODULES = [
    "api",
    "database",
    "db_config",
    "html_fetcher",
    "main",
    "publication",
    "researcher",
    "scheduler",
    "compare_models",
]

# Modules that create or transitively trigger OpenAI() at import time
# (publication.py and compare_models.py create clients at module scope;
#  main.py imports publication at module scope, triggering it transitively)
_OPENAI_CLIENT_MODULES = {"publication", "compare_models", "main"}

# Modules that create MySQL connection pools at module scope
_DB_POOL_MODULES = {"database", "html_fetcher", "researcher", "scheduler", "api", "main", "publication", "compare_models"}


@pytest.mark.parametrize("module_name", ROOT_MODULES)
def test_module_imports_cleanly(module_name):
    """Importing {module_name} must not raise."""
    patches = []

    # Mock OpenAI client for modules that instantiate at import time
    if module_name in _OPENAI_CLIENT_MODULES:
        patches.append(patch("openai.OpenAI", return_value=MagicMock()))

    # Mock MySQL pool for all modules (transitive imports hit database.py)
    patches.append(
        patch("mysql.connector.pooling.MySQLConnectionPool", return_value=MagicMock())
    )
    # Also mock direct mysql.connector.connect used by scheduler.py
    patches.append(
        patch("mysql.connector.connect", return_value=MagicMock())
    )

    for p in patches:
        p.start()
    try:
        # Remove from cache so importlib re-executes the module code,
        # catching errors even if a prior parametrized case imported it transitively
        sys.modules.pop(module_name, None)
        mod = importlib.import_module(module_name)
        assert mod is not None
    finally:
        for p in patches:
            p.stop()
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_imports.py -v`
Expected: 9 tests PASS (one per module)

- [ ] **Step 3: Commit**

```bash
git add tests/test_imports.py
git commit -m "test: add module import smoke tests"
```

---

### Task 3: `tests/test_db_config.py` — Environment variable validation

**Files:**
- Create: `tests/test_db_config.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for db_config.py environment variable validation.

Critical: db_config.py calls load_dotenv() at module scope.
We must patch it as a no-op before importlib.reload() to prevent
.env file values from contaminating test env vars.
"""
import importlib
import os
from unittest.mock import patch

import pytest


def _reload_db_config(env_overrides: dict):
    """Reload db_config with controlled env vars.

    Clears all DB/OPENAI env vars, applies overrides, patches
    load_dotenv as no-op, then reloads the module.
    """
    # Start with a clean slate for the vars db_config checks
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME",
                              "DB_PORT", "DB_SSL_CA", "OPENAI_API_KEY")}
    clean_env.update(env_overrides)

    with patch.dict(os.environ, clean_env, clear=True):
        with patch("dotenv.load_dotenv"):  # no-op: don't read .env from disk
            import db_config
            return importlib.reload(db_config)


# Valid baseline env for tests that need a working config
_VALID_ENV = {
    "DB_HOST": "localhost",
    "DB_USER": "testuser",
    "DB_PASSWORD": "testpass",
    "DB_NAME": "test_db",
    "OPENAI_API_KEY": "sk-test-key",
}


class TestRequiredVars:
    """Missing required env vars must raise EnvironmentError."""

    @pytest.mark.parametrize("missing_var", [
        "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "OPENAI_API_KEY",
    ])
    def test_missing_required_var_raises(self, missing_var):
        env = {k: v for k, v in _VALID_ENV.items() if k != missing_var}
        with pytest.raises(EnvironmentError, match=missing_var):
            _reload_db_config(env)


class TestDbNameValidation:
    """DB_NAME must match ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$."""

    @pytest.mark.parametrize("bad_name", [
        "1starts_with_digit",
        "has-dashes",
        "has spaces",
        "has.dots",
        "",
        "a" * 65,  # too long
    ])
    def test_invalid_db_name_raises(self, bad_name):
        env = {**_VALID_ENV, "DB_NAME": bad_name}
        with pytest.raises(EnvironmentError, match="DB_NAME"):
            _reload_db_config(env)

    def test_valid_db_name_accepted(self):
        mod = _reload_db_config(_VALID_ENV)
        assert mod.db_config["database"] == "test_db"


class TestOptionalVars:
    """Optional vars have correct defaults."""

    def test_port_defaults_to_3306(self):
        mod = _reload_db_config(_VALID_ENV)
        assert mod.db_config["port"] == 3306

    def test_port_override(self):
        mod = _reload_db_config({**_VALID_ENV, "DB_PORT": "3307"})
        assert mod.db_config["port"] == 3307

    def test_ssl_ca_not_in_config_by_default(self):
        mod = _reload_db_config(_VALID_ENV)
        assert "ssl_ca" not in mod.db_config

    def test_ssl_ca_added_when_set(self):
        mod = _reload_db_config({**_VALID_ENV, "DB_SSL_CA": "/path/to/ca.pem"})
        assert mod.db_config["ssl_ca"] == "/path/to/ca.pem"
        assert mod.db_config["ssl_verify_cert"] is True


class TestValidConfig:
    """Valid env produces correct db_config dict."""

    def test_config_shape(self):
        mod = _reload_db_config(_VALID_ENV)
        cfg = mod.db_config
        assert cfg["host"] == "localhost"
        assert cfg["user"] == "testuser"
        assert cfg["password"] == "testpass"
        assert cfg["database"] == "test_db"
        assert cfg["port"] == 3306
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_db_config.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_db_config.py
git commit -m "test: add db_config env var validation tests"
```

---

### Task 4: `tests/test_api_response_shapes.py` — API response contract tests

**Files:**
- Create: `tests/test_api_response_shapes.py`

- [ ] **Step 1: Write the test file**

```python
"""Contract tests: API response shapes must match frontend types.ts.

These tests verify key presence (not values) to catch frontend/backend
drift. Field lists are derived from app/src/lib/types.ts interfaces.
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked database and scheduler."""
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
    ):
        from api import app
        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# Expected key sets (derived from app/src/lib/types.ts)
# ---------------------------------------------------------------------------

PAGINATED_KEYS = {"items", "total", "page", "per_page", "pages"}

PUBLICATION_KEYS = {
    "id", "title", "authors", "year", "venue", "source_url",
    "discovered_at", "status", "abstract", "draft_url",
    "draft_url_status", "draft_available",
}

AUTHOR_KEYS = {"id", "first_name", "last_name"}

RESEARCHER_KEYS = {
    "id", "first_name", "last_name", "position", "affiliation",
    "description", "urls", "website_url", "publication_count", "fields",
}

RESEARCHER_URL_KEYS = {"id", "page_type", "url"}

RESEARCH_FIELD_KEYS = {"id", "name", "slug"}

RESEARCHER_DETAIL_EXTRA_KEYS = {"publications"}

SCRAPE_STATUS_KEYS = {"last_scrape", "next_scrape_at", "interval_hours"}

SCRAPE_LAST_KEYS = {
    "id", "status", "started_at", "finished_at",
    "urls_checked", "urls_changed", "pubs_extracted",
}


# ---------------------------------------------------------------------------
# Sample data (minimal, just enough to produce responses)
# ---------------------------------------------------------------------------

SAMPLE_PUB = (
    1, "Trade and Wages", "2024", "JLE", "https://example.com/p",
    datetime(2026, 3, 15, 14, 30), "published", "https://ssrn.com/1",
    "An abstract.", "valid",
)
SAMPLE_AUTHORS = [(1, 10, "Max", "Steinhardt")]
SAMPLE_RESEARCHER = (
    10, "Max", "Steinhardt", "Professor", "FU Berlin", "Economist."
)
SAMPLE_URLS = [(10, 1, "homepage", "https://example.com")]
SAMPLE_PUB_COUNTS = [(10, 5)]
SAMPLE_FIELDS = [(10, 1, "Labour Economics", "labour-economics")]
SAMPLE_SCRAPE = (
    1, "completed", datetime(2026, 3, 16, 10, 0),
    datetime(2026, 3, 16, 10, 5), 10, 2, 3,
)


# ---------------------------------------------------------------------------
# Publication contract tests
# ---------------------------------------------------------------------------

class TestPublicationShape:
    """GET /api/publications response matches types.ts Publication."""

    def test_paginated_envelope_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
            body = client.get("/api/publications").json()

        assert set(body.keys()) >= PAGINATED_KEYS

    def test_publication_item_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
            body = client.get("/api/publications").json()

        item = body["items"][0]
        assert set(item.keys()) >= PUBLICATION_KEYS, (
            f"Missing keys: {PUBLICATION_KEYS - set(item.keys())}"
        )

    def test_author_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
            body = client.get("/api/publications").json()

        author = body["items"][0]["authors"][0]
        assert set(author.keys()) >= AUTHOR_KEYS, (
            f"Missing author keys: {AUTHOR_KEYS - set(author.keys())}"
        )


# ---------------------------------------------------------------------------
# Researcher contract tests
# ---------------------------------------------------------------------------

class TestResearcherShape:
    """GET /api/researchers response matches types.ts Researcher."""

    def test_researcher_list_item_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [
                [SAMPLE_RESEARCHER], SAMPLE_URLS,
                SAMPLE_PUB_COUNTS, SAMPLE_FIELDS,
            ]
            body = client.get("/api/researchers").json()

        assert set(body.keys()) >= PAGINATED_KEYS
        item = body["items"][0]
        assert set(item.keys()) >= RESEARCHER_KEYS, (
            f"Missing keys: {RESEARCHER_KEYS - set(item.keys())}"
        )

    def test_researcher_url_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [
                [SAMPLE_RESEARCHER], SAMPLE_URLS,
                SAMPLE_PUB_COUNTS, SAMPLE_FIELDS,
            ]
            body = client.get("/api/researchers").json()

        url_obj = body["items"][0]["urls"][0]
        assert set(url_obj.keys()) >= RESEARCHER_URL_KEYS, (
            f"Missing url keys: {RESEARCHER_URL_KEYS - set(url_obj.keys())}"
        )

    def test_research_field_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [
                [SAMPLE_RESEARCHER], SAMPLE_URLS,
                SAMPLE_PUB_COUNTS, SAMPLE_FIELDS,
            ]
            body = client.get("/api/researchers").json()

        field_obj = body["items"][0]["fields"][0]
        assert set(field_obj.keys()) >= RESEARCH_FIELD_KEYS, (
            f"Missing field keys: {RESEARCH_FIELD_KEYS - set(field_obj.keys())}"
        )


# ---------------------------------------------------------------------------
# Researcher detail contract tests
# ---------------------------------------------------------------------------

class TestResearcherDetailShape:
    """GET /api/researchers/{id} matches types.ts ResearcherDetail."""

    def test_detail_has_publications_key(self, client):
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_one.side_effect = [SAMPLE_RESEARCHER, (5,)]
            single_urls = [(1, "homepage", "https://example.com")]
            single_fields = [(1, "Labour Economics", "labour-economics")]
            mock_all.side_effect = [
                single_urls, single_fields,
                [SAMPLE_PUB], SAMPLE_AUTHORS,
            ]
            body = client.get("/api/researchers/10").json()

        all_expected = RESEARCHER_KEYS | RESEARCHER_DETAIL_EXTRA_KEYS
        assert set(body.keys()) >= all_expected, (
            f"Missing keys: {all_expected - set(body.keys())}"
        )
        assert isinstance(body["publications"], list)


# ---------------------------------------------------------------------------
# Scrape status contract tests
# ---------------------------------------------------------------------------

class TestScrapeStatusShape:
    """GET /api/scrape/status matches expected API contract."""

    def test_scrape_status_top_level_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_SCRAPE),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            body = client.get("/api/scrape/status").json()

        assert set(body.keys()) >= SCRAPE_STATUS_KEYS, (
            f"Missing keys: {SCRAPE_STATUS_KEYS - set(body.keys())}"
        )

    def test_scrape_last_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_SCRAPE),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            body = client.get("/api/scrape/status").json()

        last = body["last_scrape"]
        assert last is not None
        assert set(last.keys()) >= SCRAPE_LAST_KEYS, (
            f"Missing last_scrape keys: {SCRAPE_LAST_KEYS - set(last.keys())}"
        )
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_api_response_shapes.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_response_shapes.py
git commit -m "test: add API response shape contract tests"
```

---

### Task 5: Makefile `check` target

**Files:**
- Modify: `Makefile:1-2`

- [ ] **Step 1: Add the `check` target to the Makefile**

Add `check` to the `.PHONY` line and add the target:

```makefile
.PHONY: setup dev seed reset-db scrape fetch parse parse-fast batch-submit batch-check check

# ... (existing targets remain unchanged) ...

check:
	@echo "=== Step 1: Env validation ==="
	.venv/bin/python scripts/check_env.py
	@echo "=== Step 2: Python tests ==="
	.venv/bin/pytest
	@echo "=== Step 3: TypeScript check ==="
	cd app && npx tsc --noEmit
	@echo "=== Step 4: Frontend tests ==="
	cd app && npx jest
	@echo "=== All checks passed ==="
```

- [ ] **Step 2: Run `make check` to verify it works end-to-end**

Run: `make check`
Expected: All 4 steps pass sequentially, printing status headers. If any step fails, the command stops immediately.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat: add make check pre-flight command"
```

---

### Task 6: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `make check` to the Commands section**

In the `### Testing` section, add:

```markdown
### Pre-flight Check
```bash
make check             # Run env validation, pytest, tsc, jest (use before make dev)
```
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add make check to CLAUDE.md commands"
```
