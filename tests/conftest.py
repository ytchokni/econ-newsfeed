"""Shared fixtures for API tests.

Environment variables are set BEFORE any application imports to avoid
db_config.py's sys.exit() on missing vars.
"""
import os

# Set test environment variables before any app imports
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")
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


@pytest.fixture(autouse=True)
def _clear_api_caches():
    """Reset TTL caches between tests to prevent cross-test pollution."""
    import api
    api._filter_options_cache.clear()
    api._fields_cache.clear()
    api._jel_codes_cache.clear()
    yield
    api._filter_options_cache.clear()
    api._fields_cache.clear()
    api._jel_codes_cache.clear()


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
