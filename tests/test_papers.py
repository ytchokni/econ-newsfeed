import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import MagicMock, patch, call


class TestUpdateOpenalexDataYear:
    """update_openalex_data backfills year when provided."""

    def _mock_connection(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn, mock_cursor

    def test_year_included_in_update_query(self):
        mock_conn, mock_cursor = self._mock_connection()
        with patch("database.papers.get_connection", return_value=mock_conn):
            from database.papers import update_openalex_data
            update_openalex_data(
                paper_id=1,
                doi="10.1234/test",
                openalex_id="W123",
                coauthors=[],
                abstract=None,
                year="2024",
            )
        # The UPDATE query should include year backfill
        update_call = mock_cursor.execute.call_args_list[0]
        sql = update_call[0][0]
        assert "year" in sql.lower()
        params = update_call[0][1]
        assert "2024" in params

    def test_none_year_does_not_overwrite_existing(self):
        mock_conn, mock_cursor = self._mock_connection()
        with patch("database.papers.get_connection", return_value=mock_conn):
            from database.papers import update_openalex_data
            update_openalex_data(
                paper_id=1,
                doi="10.1234/test",
                openalex_id="W123",
                coauthors=[],
                abstract=None,
                year=None,
            )
        # When year is None, should use COALESCE to preserve existing
        update_call = mock_cursor.execute.call_args_list[0]
        sql = update_call[0][0]
        assert "COALESCE" in sql
