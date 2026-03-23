"""Tests for researcher disambiguation with OpenAlex author ID."""
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import patch, MagicMock
from database.researchers import get_researcher_id


class TestOpenalexAuthorIdMatching:
    """get_researcher_id should match by openalex_author_id before LLM."""

    @patch("database.researchers._disambiguate_researcher")
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    def test_matches_by_openalex_id_skips_llm(self, mock_fetch_one, mock_fetch_all, mock_disambig):
        # No exact name match, but openalex_author_id match succeeds
        mock_fetch_one.side_effect = [
            None,  # exact name match fails
            {"id": 42},  # openalex_author_id match succeeds
        ]

        result = get_researcher_id("M.", "Steinhardt", openalex_author_id="A5023888391")

        assert result == 42
        mock_disambig.assert_not_called()

    @patch("database.researchers._disambiguate_researcher", return_value=99)
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    def test_falls_back_to_llm_when_no_openalex_id(self, mock_fetch_one, mock_fetch_all, mock_disambig):
        mock_fetch_one.return_value = None  # no exact match
        mock_fetch_all.return_value = [{"id": 99, "first_name": "Max", "last_name": "Steinhardt"}]

        result = get_researcher_id("M.", "Steinhardt")

        assert result == 99
        mock_disambig.assert_called_once()
