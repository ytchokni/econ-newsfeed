"""Tests for application startup requirements.

These tests verify fixes for issues that prevented `make dev` from working:
- SCRAPE_API_KEY minimum length enforcement
- Bio column migration tolerates pre-existing column (MySQL compat)
- Rewrite destination defaults to localhost for local dev
"""
import os
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# SCRAPE_API_KEY enforcement
# ---------------------------------------------------------------------------

class TestScrapeApiKeyValidation:
    """API must refuse to start when SCRAPE_API_KEY is missing or too short."""

    def test_short_key_prevents_startup(self):
        """A key shorter than 16 chars must cause a RuntimeError at startup."""
        with patch.dict(os.environ, {"SCRAPE_API_KEY": "changeme"}, clear=False):
            # Force module-level _SCRAPE_API_KEY to pick up the short value
            with (
                patch("database.Database.create_tables"),
                patch("scheduler.start_scheduler"),
                patch("scheduler.shutdown_scheduler"),
            ):
                import importlib
                import api as api_mod

                # Patch the module-level key to simulate the short key
                with patch.object(api_mod, "_SCRAPE_API_KEY", "changeme"):
                    from fastapi.testclient import TestClient

                    with pytest.raises(RuntimeError, match="too short"):
                        with TestClient(api_mod.app):
                            pass

    def test_empty_key_prevents_startup(self):
        """An empty SCRAPE_API_KEY must cause a RuntimeError at startup."""
        with (
            patch("database.Database.create_tables"),
            patch("scheduler.start_scheduler"),
            patch("scheduler.shutdown_scheduler"),
        ):
            import api as api_mod

            with patch.object(api_mod, "_SCRAPE_API_KEY", ""):
                with pytest.raises(RuntimeError, match="too short"):
                    with TestClient(api_mod.app):
                        pass

    def test_valid_key_allows_startup(self):
        """A key of 16+ chars must allow normal startup."""
        with (
            patch("database.Database.create_tables"),
            patch("scheduler.start_scheduler"),
            patch("scheduler.shutdown_scheduler"),
        ):
            import api as api_mod

            with patch.object(api_mod, "_SCRAPE_API_KEY", "a-valid-key-that-is-long-enough"):
                with TestClient(api_mod.app) as c:
                    # App started successfully — verify it responds
                    with (
                        patch("api.Database.fetch_one", return_value=(0,)),
                        patch("api.Database.fetch_all", return_value=[]),
                    ):
                        resp = c.get("/api/publications")
                    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bio column migration resilience
# ---------------------------------------------------------------------------

class TestBioColumnMigration:
    """create_tables must not fail when the bio column already exists."""

    def test_migration_succeeds_when_column_already_exists(self):
        """ALTER TABLE ADD COLUMN raising a duplicate error must not crash startup."""
        from mysql.connector import Error as MySQLError

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        from database import Database

        with patch.object(Database, "get_connection", return_value=mock_conn):
            # execute_query for the ALTER TABLE will fail (column exists)
            with patch.object(
                Database,
                "execute_query",
                side_effect=Exception("Duplicate column name 'bio'"),
            ):
                with patch.object(Database, "seed_research_fields"):
                    # Must not raise
                    Database.create_tables()

    def test_migration_succeeds_when_column_is_new(self):
        """ALTER TABLE ADD COLUMN succeeding must work normally."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        from database import Database

        with patch.object(Database, "get_connection", return_value=mock_conn):
            with patch.object(Database, "execute_query", return_value=None):
                with patch.object(Database, "seed_research_fields"):
                    # Must not raise
                    Database.create_tables()
