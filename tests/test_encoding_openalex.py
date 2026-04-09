"""Integration tests: encoding guard in OpenAlex enrichment writes."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")

from unittest.mock import patch, MagicMock


class TestOpenAlexEncodingGuard:
    """Verify update_openalex_data passes text through encoding guard."""

    @patch("database.papers.get_connection")
    def test_mojibake_abstract_is_fixed(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Support both `with conn` and `with conn.cursor() as cursor` patterns
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        # cursor() used as context manager: cursor().__enter__() returns mock_cursor
        mock_cursor_ctx = MagicMock()
        mock_cursor_ctx.__enter__ = lambda s: mock_cursor
        mock_cursor_ctx.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor_ctx
        mock_get_conn.return_value = mock_conn

        from database.papers import update_openalex_data

        update_openalex_data(
            paper_id=1,
            doi="10.1234/test",
            openalex_id="W123",
            coauthors=[{"display_name": "FrÃ©dÃ©ric Dupont", "openalex_author_id": "A1"}],
            abstract="Eine Analyse fÃ¼r Ã–konomen",
        )

        # Check the UPDATE papers call
        update_calls = [
            call for call in mock_cursor.execute.call_args_list
            if "UPDATE papers" in str(call)
        ]
        assert len(update_calls) >= 1
        params = update_calls[0][0][1]
        # params: (doi, openalex_id, abstract, paper_id)
        abstract_param = params[2]
        assert abstract_param == "Eine Analyse für Ökonomen"

        # Check the INSERT coauthors call
        executemany_calls = mock_cursor.executemany.call_args_list
        assert len(executemany_calls) >= 1
        coauthor_params = executemany_calls[0][0][1]
        display_name = coauthor_params[0][1]
        assert display_name == "Frédéric Dupont"
