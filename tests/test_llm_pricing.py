"""Tests for llm_usage cost estimation."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from unittest.mock import MagicMock, patch


class TestLlmPricing:
    @patch("database.llm.execute_query")
    def test_gemma_4_31b_cost_computed(self, mock_exec):
        from database.llm import log_llm_usage
        usage = MagicMock()
        usage.prompt_tokens = 1_000_000
        usage.completion_tokens = 1_000_000
        usage.total_tokens = 2_000_000

        log_llm_usage("publication_extraction", "google/gemma-4-31b-it", usage)

        assert mock_exec.called
        row = mock_exec.call_args[0][1]
        # row order: (called_at, call_type, model, prompt, completion, total, cost, is_batch, ...)
        cost = row[6]
        assert cost is not None
        # $0.14/M prompt + $0.40/M completion = $0.54 total
        assert abs(float(cost) - 0.54) < 1e-6

    @patch("database.llm.execute_query")
    def test_batch_multiplier_is_one_on_parasail(self, mock_exec):
        from database.llm import log_llm_usage
        usage = MagicMock()
        usage.prompt_tokens = 1_000_000
        usage.completion_tokens = 0
        usage.total_tokens = 1_000_000

        log_llm_usage("publication_extraction", "google/gemma-4-31b-it", usage, is_batch=True)

        row = mock_exec.call_args[0][1]
        cost = row[6]
        # $0.14/M prompt, no 50% discount on Parasail
        assert abs(float(cost) - 0.14) < 1e-6
