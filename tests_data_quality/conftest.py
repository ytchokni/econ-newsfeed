"""Data-quality invariant checks that run against the REAL database.

Unlike tests/ (mocked, fast), this suite connects to the database configured
in .env and fails when stored data violates invariants that past production
incidents established. Run with:

    poetry run pytest tests_data_quality -v

Each test names the incident it guards against (PR/issue number). A failure
is not a code bug — it is a triage list of bad rows in the database.

The suite skips itself when no database is reachable, so it is safe to
invoke anywhere; it is intentionally NOT part of `testpaths` in pyproject.
"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

# db_config.py hard-requires these at import; only the DB_* values are actually
# used here. Placeholders keep collection alive when .env is partial — the
# session fixture below still skips if the DB itself is unreachable.
os.environ.setdefault("GOOGLE_API_KEY", "data-quality-placeholder")
os.environ.setdefault("SCRAPE_API_KEY", "data-quality-placeholder")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

_MISSING_DB_VARS = [v for v in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME") if not os.environ.get(v)]

# Ranks must mirror database/snapshots.py::_STATUS_RANK
STATUS_ORDER = "'working_paper','reject_and_resubmit','revise_and_resubmit','accepted','published'"

# Earliest plausible feed event — the project started scraping in 2025.
PROJECT_EPOCH = "2025-01-01"


@pytest.fixture(scope="session", autouse=True)
def _require_database():
    if _MISSING_DB_VARS:
        pytest.skip(f"No database configured: missing {', '.join(_MISSING_DB_VARS)} in .env")
    try:
        from backend.database.connection import get_connection

        conn = get_connection()
        conn.close()
    except Exception as exc:  # connection refused, bad credentials, missing schema…
        pytest.skip(f"Database not reachable: {exc}")


@pytest.fixture(scope="session")
def db(_require_database):
    """Thin query interface over the real connection pool."""
    from types import SimpleNamespace

    from backend.database.connection import fetch_all, fetch_one

    return SimpleNamespace(fetch_all=fetch_all, fetch_one=fetch_one)


def mojibake_condition(column: str) -> str:
    """SQL condition matching UTF-8-as-latin1 mojibake bigrams in a column.

    A bare 'Ã' is legitimate (Portuguese SÃO, COGNIÇÃO); only 'Ã' followed by
    a latin-1 symbol byte — or the 'â€' smart-punctuation prefix — is mojibake.
    LIKE BINARY is used because MySQL 8.4 rejects REGEXP BINARY on utf8mb4.
    """
    bigrams = ["â€", "Ã©", "Ã¨", "Ã¡", "Ã³", "Ãº", "Ã­", "Ã§", "Ã£", "Ã¶", "Ã¼", "Ã±", "Ã¤", "Ã¸", "Ã¥"]
    return "(" + " OR ".join(f"{column} LIKE BINARY '%{b}%'" for b in bigrams) + ")"


def fmt_violations(rows: list[dict], total: int | None = None, limit: int = 15) -> str:
    """Render offending rows into an actionable failure message."""
    total = total if total is not None else len(rows)
    lines = [f"{total} violating row(s); first {min(len(rows), limit)}:"]
    for row in rows[:limit]:
        lines.append("  " + ", ".join(f"{k}={v!r}" for k, v in row.items()))
    return "\n".join(lines)
