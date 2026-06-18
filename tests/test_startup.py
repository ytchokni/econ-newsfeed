"""Tests for application startup requirements.

These tests verify fixes for issues that prevented `make dev` from working:
- SCRAPE_API_KEY minimum length enforcement
- Bio column migration tolerates pre-existing column (MySQL compat)
- Rewrite destination defaults to localhost for local dev
"""
import os
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from database import create_tables


@contextmanager
def _noop_connection_scope():
    yield None


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
                patch("api.create_tables"),
                patch("api.start_scheduler"),
                patch("api.shutdown_scheduler"),
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
            patch("api.create_tables"),
            patch("api.start_scheduler"),
            patch("api.shutdown_scheduler"),
        ):
            import api as api_mod

            with patch.object(api_mod, "_SCRAPE_API_KEY", ""):
                with pytest.raises(RuntimeError, match="too short"):
                    with TestClient(api_mod.app):
                        pass

    def test_valid_key_allows_startup(self):
        """A key of 16+ chars must allow normal startup."""
        with (
            patch("api.create_tables"),
            patch("api.start_scheduler"),
            patch("api.shutdown_scheduler"),
        ):
            import api as api_mod

            with patch.object(api_mod, "_SCRAPE_API_KEY", "a-valid-key-that-is-long-enough"):
                with TestClient(api_mod.app) as c:
                    # App started successfully -- verify it responds
                    with (
                        patch("api.connection_scope", _noop_connection_scope),
                        patch("api.search_feed_events", return_value=([], 0, 0)),
                        patch("api.get_authors_for_papers", return_value={}),
                        patch("api.get_coauthors_for_papers", return_value={}),
                        patch("api.get_links_for_papers", return_value={}),
                    ):
                        resp = c.get("/api/publications")
                    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bio column migration resilience
# ---------------------------------------------------------------------------

def _make_mock_conn():
    """Create a mock connection whose cursor works as a context manager."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)  # GET_LOCK returns 1 (acquired)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestBioColumnMigration:
    """create_tables must not fail when the bio column already exists."""

    def test_migration_succeeds_when_column_already_exists(self):
        """ALTER TABLE ADD COLUMN raising a duplicate error must not crash startup."""
        mock_conn, _ = _make_mock_conn()

        with patch("database.schema.get_connection", return_value=mock_conn):
            with patch("database.schema.execute_query",
                       side_effect=Exception("Duplicate column name 'bio'")):
                with patch("database.schema.seed_research_fields"), \
                     patch("database.schema.seed_jel_codes"):
                    # Must not raise
                    create_tables()

    def test_migration_succeeds_when_column_is_new(self):
        """ALTER TABLE ADD COLUMN succeeding must work normally."""
        mock_conn, _ = _make_mock_conn()

        with patch("database.schema.get_connection", return_value=mock_conn):
            with patch("database.schema.execute_query", return_value=None):
                with patch("database.schema.seed_research_fields"), \
                     patch("database.schema.seed_jel_codes"):
                    # Must not raise
                    create_tables()


# ---------------------------------------------------------------------------
# Advisory-lock migration must not leave unread results
# ---------------------------------------------------------------------------

class TestMigrationAdvisoryLock:
    """The advisory lock migration must consume all cursor results.

    Regression test: if GET_LOCK / RELEASE_LOCK results are not consumed,
    mysql.connector raises "Unread result found" on the next execute(),
    which crashes the API on startup.
    """

    def test_create_tables_consumes_all_cursor_results(self):
        """Verify GET_LOCK result is fetched and RELEASE_LOCK result is fetched."""
        mock_conn, mock_cursor = _make_mock_conn()

        with patch("database.schema.get_connection", return_value=mock_conn):
            with patch("database.schema.seed_research_fields"), \
                 patch("database.schema.seed_jel_codes"):
                create_tables()

        # The cursor must have called fetchone() at least twice:
        # once for GET_LOCK, once for RELEASE_LOCK
        assert mock_cursor.fetchone.call_count >= 2, (
            "GET_LOCK and RELEASE_LOCK results must both be consumed via fetchone() "
            "to prevent 'Unread result found' errors"
        )

    def test_create_tables_acquires_and_releases_lock(self):
        """Verify GET_LOCK and RELEASE_LOCK are both called."""
        mock_conn, mock_cursor = _make_mock_conn()

        with patch("database.schema.get_connection", return_value=mock_conn):
            with patch("database.schema.seed_research_fields"), \
                 patch("database.schema.seed_jel_codes"):
                create_tables()

        executed_sql = [
            str(call.args[0]) for call in mock_cursor.execute.call_args_list
        ]
        lock_calls = [s for s in executed_sql if "GET_LOCK" in s or "RELEASE_LOCK" in s]
        assert any("GET_LOCK" in s for s in lock_calls), "Must call GET_LOCK"
        assert any("RELEASE_LOCK" in s for s in lock_calls), "Must call RELEASE_LOCK"

    def test_release_lock_called_even_on_migration_error(self):
        """RELEASE_LOCK must be called even if ALTER TABLE raises an unexpected error."""
        mock_conn, mock_cursor = _make_mock_conn()

        # Make ALTER TABLE raise a non-duplicate-column error
        alter_error = Exception("Some unexpected DB error")
        alter_error.errno = 9999

        call_count = [0]
        original_execute = mock_cursor.execute

        def execute_side_effect(sql, *args, **kwargs):
            if "ALTER TABLE" in str(sql):
                call_count[0] += 1
                raise alter_error
            return original_execute(sql, *args, **kwargs)

        mock_cursor.execute = MagicMock(side_effect=execute_side_effect)

        with patch("database.schema.get_connection", return_value=mock_conn):
            with patch("database.schema.seed_research_fields"), \
                 patch("database.schema.seed_jel_codes"):
                create_tables()  # Must not raise

        executed_sql = [
            str(call.args[0]) for call in mock_cursor.execute.call_args_list
        ]
        assert any("RELEASE_LOCK" in s for s in executed_sql), (
            "RELEASE_LOCK must be called even when ALTER TABLE fails"
        )


# ---------------------------------------------------------------------------
# Forward-flapping status_change cleanup migration
# ---------------------------------------------------------------------------

class TestForwardFlappingCleanupMigration:
    """create_tables must delete status_change events that do not advance past
    the highest rank already reached by an earlier event for the same paper."""

    def test_create_tables_runs_forward_flapping_delete(self):
        mock_conn, mock_cursor = _make_mock_conn()

        with patch("database.schema.get_connection", return_value=mock_conn):
            with patch("database.schema.seed_research_fields"), \
                 patch("database.schema.seed_jel_codes"):
                create_tables()

        executed_sql = [str(c) for c in mock_cursor.execute.call_args_list]
        flap_deletes = [s for s in executed_sql if "DELETE b FROM feed_events b" in s]
        assert len(flap_deletes) == 1
        # Self-join orders by (created_at, id) and compares new_status ranks
        assert "a.created_at < b.created_at" in flap_deletes[0]
        assert "a.id < b.id" in flap_deletes[0]
        assert "FIELD(a.new_status" in flap_deletes[0]
