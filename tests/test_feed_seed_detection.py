"""Tests that extraction pipelines pass is_seed=True on first extraction."""

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


class TestExtractDataSeedDetection:
    """extract_data_from_htmls passes is_seed based on is_first_extraction."""

    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction")
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    @patch("main.Researcher.get_all_researcher_urls")
    def test_first_extraction_passes_is_seed_true(
        self, mock_urls, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links,
    ):
        mock_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "https://example.com", "page_type": "personal"}
        ]
        mock_first.return_value = True

        from main import extract_data_from_htmls
        extract_data_from_htmls()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is True

    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction")
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    @patch("main.Researcher.get_all_researcher_urls")
    def test_subsequent_extraction_passes_is_seed_false(
        self, mock_urls, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links,
    ):
        mock_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "https://example.com", "page_type": "personal"}
        ]
        mock_first.return_value = False

        from main import extract_data_from_htmls
        extract_data_from_htmls()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is False


class TestProcessOneUrlSeedDetection:
    """_process_one_url passes is_seed based on is_first_extraction."""

    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction", return_value=True)
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    def test_first_extraction_passes_is_seed_true(
        self, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links,
    ):
        from main import _process_one_url
        _process_one_url(1, 10, "https://example.com", "personal")

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is True


class TestBatchCheckSeedDetection:
    """batch_check passes is_seed based on is_first_extraction."""

    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.match_and_save_paper_links")
    @patch("main.Publication.save_publications")
    @patch("main.HTMLFetcher.is_first_extraction", return_value=True)
    @patch("main.Database.log_llm_usage")
    @patch("main.Database.execute_query")
    @patch("main.Database.fetch_one")
    @patch("main.Database.fetch_all")
    def test_batch_check_first_extraction_passes_is_seed_true(
        self, mock_fetch_all, mock_fetch_one, mock_exec, mock_log,
        mock_first, mock_save, mock_links, mock_mark,
    ):
        """batch_check should pass is_seed=True for URLs never extracted before."""
        import json
        from unittest.mock import MagicMock

        # One pending batch
        mock_fetch_all.return_value = [{"id": 1, "openai_batch_id": "batch_abc"}]

        # url_row lookup and cost aggregation
        mock_fetch_one.side_effect = [
            {"url": "https://example.com"},  # url_row for url_id=5
            {"total_cost": 0.01},            # cost aggregation
        ]

        # Build a valid batch API response
        batch_result = {
            "custom_id": "url_5",
            "response": {
                "body": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "choices": [{"message": {"content": json.dumps([{
                        "title": "Paper",
                        "authors": [["A", "B"]],
                        "year": "2024",
                        "venue": None,
                        "status": "working_paper",
                        "draft_url": None,
                        "abstract": None,
                    }])}}],
                },
            },
        }

        # Mock OpenAI client
        mock_client = MagicMock()
        mock_batch = MagicMock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "file_out"
        mock_client.batches.retrieve.return_value = mock_batch
        mock_client.files.content.return_value.text = json.dumps(batch_result)

        with patch("openai.OpenAI", return_value=mock_client):
            from main import batch_check
            batch_check()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is True
