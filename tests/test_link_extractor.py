"""Tests for link extraction and matching."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("CONTENT_MAX_CHARS", "4000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, MagicMock
import pytest
from html_fetcher import HTMLFetcher


class TestSaveTextWithRawHtml:
    @patch("html_fetcher.Database.execute_query")
    def test_save_text_stores_raw_html(self, mock_execute):
        HTMLFetcher.save_text(url_id=1, text_content="text", text_hash="abc",
                              researcher_id=10, raw_html="<html>test</html>")
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "raw_html" in sql
        assert "<html>test</html>" in params

    @patch("html_fetcher.Database.execute_query")
    def test_save_text_without_raw_html_passes_none(self, mock_execute):
        HTMLFetcher.save_text(url_id=1, text_content="text", text_hash="abc", researcher_id=10)
        assert mock_execute.call_args[0][1][-1] is None
