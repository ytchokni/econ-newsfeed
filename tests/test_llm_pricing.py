"""Tests for llm_usage cost estimation."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from unittest.mock import MagicMock, patch


class TestLlmPricing:
    @patch("backend.database.llm.execute_query")
    def test_gemini_flash_cost_computed(self, mock_exec):
        from backend.database.llm import log_llm_usage
        usage = MagicMock()
        usage.prompt_tokens = 1_000_000
        usage.completion_tokens = 1_000_000
        usage.total_tokens = 2_000_000

        log_llm_usage("publication_extraction", "gemini-2.5-flash", usage)

        assert mock_exec.called
        row = mock_exec.call_args[0][1]
        cost = row[6]
        assert cost is not None
        # $0.30/M prompt + $2.50/M completion = $2.80 total
        assert abs(float(cost) - 2.80) < 1e-6

