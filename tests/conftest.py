"""Shared fixtures for API tests.

Environment variables are set BEFORE any application imports to avoid
db_config.py's sys.exit() on missing vars.
"""
import os

# Set test environment variables before any app imports
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@contextmanager
def _noop_connection_scope():
    """No-op replacement for connection_scope in tests."""
    yield None


@pytest.fixture
def client():
    """Test client with mocked DB and scheduler."""
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
        patch("api.connection_scope", _noop_connection_scope),
    ):
        from api import app
        with TestClient(app) as c:
            yield c
