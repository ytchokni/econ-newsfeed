#!/usr/bin/env python3
"""Validate how completely we observe articles published in the top-5 economics journals.

CLI wrapper around backend/coverage/top5_validation.py (see its docstring for the method,
options and outputs). Everything is read-only: Crossref/OpenAlex responses and DB exports
are cached under --cache-dir, and nothing is written to a database.

    poetry run python scripts/validate_top5_coverage.py                # prod over SSH, 2020..today
    poetry run python scripts/validate_top5_coverage.py --source local # local DB (.env)

The About page shows the weekly result of the same comparison, computed in production
by backend/coverage/top5_job.py (``python -m backend.main top5-coverage``).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:  # Local mode reads DB settings from .env; SSH mode never touches a local DB.
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # pragma: no cover
    pass
# backend.config validates these at import time; the title helpers we reuse need no DB.
for _var in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "GOOGLE_API_KEY"):
    os.environ.setdefault(_var, "unused")

from backend.coverage.top5_validation import main  # noqa: E402

if __name__ == "__main__":
    main()
