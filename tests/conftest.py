"""Shared fixtures for API tests.

Environment variables are set BEFORE any application imports to avoid
db_config.py's sys.exit() on missing vars.
"""
import os

# Set test environment variables before any app imports
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")
