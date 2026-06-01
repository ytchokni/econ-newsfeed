"""Tests for URL deactivation / failure-tracking functions in database.researchers."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("LLM_MODEL", "gemma-4-31b-it")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, call

from database.researchers import (
    _URL_DEACTIVATION_THRESHOLD,
    record_url_fetch_failure,
    record_url_fetch_success,
    get_deactivated_urls,
    get_at_risk_urls,
    reactivate_url,
)

_MOD = "database.researchers"


class TestRecordUrlFetchFailure:
    """Tests for record_url_fetch_failure."""

    @patch(f"{_MOD}.fetch_one")
    @patch(f"{_MOD}.execute_query")
    def test_increments_consecutive_failures(self, mock_exec, mock_fetch_one):
        """Should increment consecutive_failures by 1."""
        mock_fetch_one.return_value = {"consecutive_failures": 1}

        record_url_fetch_failure(42, "http_error")

        # First call increments the counter
        mock_exec.assert_any_call(
            "UPDATE researcher_urls SET consecutive_failures = consecutive_failures + 1 WHERE id = %s",
            (42,),
        )

    @patch(f"{_MOD}.fetch_one")
    @patch(f"{_MOD}.execute_query")
    def test_deactivates_after_threshold(self, mock_exec, mock_fetch_one):
        """Should deactivate URL when consecutive_failures reaches the threshold."""
        mock_fetch_one.return_value = {"consecutive_failures": _URL_DEACTIVATION_THRESHOLD}

        record_url_fetch_failure(42, "http_error")

        # Should have: 1) increment, 2) deactivate
        assert mock_exec.call_count == 2
        deactivate_call = mock_exec.call_args_list[1]
        assert "is_active = FALSE" in deactivate_call[0][0]
        assert deactivate_call[0][1] == ("consecutive_failures", 42)

    @patch(f"{_MOD}.fetch_one")
    @patch(f"{_MOD}.execute_query")
    def test_does_not_deactivate_below_threshold(self, mock_exec, mock_fetch_one):
        """Should NOT deactivate when consecutive_failures is below threshold."""
        mock_fetch_one.return_value = {"consecutive_failures": _URL_DEACTIVATION_THRESHOLD - 1}

        record_url_fetch_failure(42, "http_error")

        # Only the increment call — no deactivation
        assert mock_exec.call_count == 1

    @patch(f"{_MOD}.execute_query")
    def test_response_too_large_deactivates_immediately(self, mock_exec):
        """response_too_large should deactivate without incrementing."""
        record_url_fetch_failure(99, "response_too_large")

        assert mock_exec.call_count == 1
        sql = mock_exec.call_args[0][0]
        assert "is_active = FALSE" in sql
        assert mock_exec.call_args[0][1] == ("response_too_large", 99)


class TestRecordUrlFetchSuccess:
    """Tests for record_url_fetch_success."""

    @patch(f"{_MOD}.execute_query")
    def test_resets_counter(self, mock_exec):
        """Should reset consecutive_failures to 0."""
        record_url_fetch_success(42)

        mock_exec.assert_called_once_with(
            "UPDATE researcher_urls SET consecutive_failures = 0 WHERE id = %s",
            (42,),
        )


class TestGetDeactivatedUrls:
    """Tests for get_deactivated_urls."""

    @patch(f"{_MOD}.fetch_all")
    def test_queries_inactive_urls(self, mock_fetch_all):
        """Should query for is_active = FALSE."""
        mock_fetch_all.return_value = []

        result = get_deactivated_urls()

        assert result == []
        sql = mock_fetch_all.call_args[0][0]
        assert "is_active = FALSE" in sql
        assert "ORDER BY ru.deactivated_at DESC" in sql


class TestGetAtRiskUrls:
    """Tests for get_at_risk_urls."""

    @patch(f"{_MOD}.fetch_all")
    def test_queries_at_risk_urls(self, mock_fetch_all):
        """Should query for active URLs with consecutive_failures >= 2."""
        mock_fetch_all.return_value = []

        result = get_at_risk_urls()

        assert result == []
        sql = mock_fetch_all.call_args[0][0]
        assert "is_active = TRUE" in sql
        assert "consecutive_failures >= 2" in sql


class TestReactivateUrl:
    """Tests for reactivate_url."""

    @patch(f"{_MOD}.execute_query")
    def test_resets_everything(self, mock_exec):
        """Should set is_active=TRUE, reset failures, clear deactivation fields."""
        reactivate_url(42)

        mock_exec.assert_called_once()
        sql = mock_exec.call_args[0][0]
        assert "is_active = TRUE" in sql
        assert "consecutive_failures = 0" in sql
        assert "deactivated_at = NULL" in sql
        assert "deactivation_reason = NULL" in sql
        assert mock_exec.call_args[0][1] == (42,)
