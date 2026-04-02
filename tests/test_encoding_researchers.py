"""Integration tests: encoding guard in researcher write paths."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")

from unittest.mock import patch, MagicMock


class TestResearcherEncodingGuard:
    """Verify researcher write paths pass text through encoding guard."""

    @patch("database.researchers.fetch_one", return_value=None)
    @patch("database.researchers.fetch_all", return_value=[])
    @patch("database.researchers.execute_query", return_value=99)
    def test_mojibake_name_fixed_on_insert(self, mock_exec, mock_fetch_all, mock_fetch_one):
        from database.researchers import get_researcher_id

        result = get_researcher_id(
            first_name="FrÃ©dÃ©ric",
            last_name="MÃ¼ller",
            position="Professor",
            affiliation="UniversitÃ¤t ZÃ¼rich",
        )

        assert result == 99
        # Check the INSERT call
        insert_call = mock_exec.call_args
        params = insert_call[0][1]
        # params: (first_name, last_name, position, affiliation)
        assert params[0] == "Frédéric"
        assert params[1] == "Müller"
        assert params[2] == "Professor"  # ASCII, unchanged
        assert params[3] == "Universität Zürich"

    @patch("database.researchers.execute_query")
    def test_mojibake_bio_fixed_on_update(self, mock_exec):
        from database.researchers import update_researcher_bio

        update_researcher_bio(1, "Forschung Ã¼ber Ã–konomie")

        params = mock_exec.call_args[0][1]
        assert params[0] == "Forschung über Ökonomie"
