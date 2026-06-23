"""Tests for URL deactivation / failure-tracking functions in database.researchers."""
from unittest.mock import patch, call

from backend.pipeline.html_fetcher import HTMLFetcher, ResponseTooLarge

from backend.database.researchers import (
    _URL_DEACTIVATION_THRESHOLD,
    record_url_fetch_failure,
    record_url_fetch_success,
    get_deactivated_urls,
    get_at_risk_urls,
    reactivate_url,
)

_MOD = "backend.database.researchers"


class TestRecordUrlFetchFailure:
    """Tests for record_url_fetch_failure."""

    @patch(f"{_MOD}.fetch_one")
    @patch(f"{_MOD}.execute_query")
    def test_increments_and_conditionally_deactivates_atomically(self, mock_exec, mock_fetch_one):
        """Should use a single atomic UPDATE that increments and conditionally deactivates."""
        mock_fetch_one.return_value = {"consecutive_failures": 1}

        record_url_fetch_failure(42, "http_error")

        assert mock_exec.call_count == 1
        sql = mock_exec.call_args[0][0]
        assert "consecutive_failures = consecutive_failures + 1" in sql
        assert "is_active = IF" in sql

    @patch(f"{_MOD}.fetch_one")
    @patch(f"{_MOD}.execute_query")
    def test_logs_warning_when_threshold_reached(self, mock_exec, mock_fetch_one):
        """Should log a warning when consecutive_failures reaches the threshold."""
        mock_fetch_one.return_value = {"consecutive_failures": _URL_DEACTIVATION_THRESHOLD}

        record_url_fetch_failure(42, "http_error")

        assert mock_exec.call_count == 1
        args = mock_exec.call_args[0][1]
        assert args[-1] == 42

    @patch(f"{_MOD}.fetch_one")
    @patch(f"{_MOD}.execute_query")
    def test_does_not_log_below_threshold(self, mock_exec, mock_fetch_one):
        """Should NOT log a warning when consecutive_failures is below threshold."""
        mock_fetch_one.return_value = {"consecutive_failures": _URL_DEACTIVATION_THRESHOLD - 1}

        record_url_fetch_failure(42, "http_error")

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
            "UPDATE researcher_urls SET consecutive_failures = 0 WHERE id = %s AND consecutive_failures > 0",
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


class TestFetchFailureTracking:
    """Test that fetch_and_save_if_changed records success/failure."""

    @patch("backend.pipeline.html_fetcher.HTMLFetcher._was_fetched_recently", return_value=False)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.validate_url_with_pin", return_value=(True, "1.2.3.4"))
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.is_allowed_by_robots", return_value=True)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.fetch_html", return_value=None)
    @patch("backend.pipeline.html_fetcher.record_url_fetch_failure")
    def test_failed_fetch_records_failure(self, mock_record, mock_fetch, mock_robots, mock_validate, mock_recent):
        HTMLFetcher.fetch_and_save_if_changed(1, "https://dead.example.com", 10)
        mock_record.assert_called_once_with(1, "fetch_failed")

    @patch("backend.pipeline.html_fetcher.HTMLFetcher._was_fetched_recently", return_value=False)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.validate_url_with_pin", return_value=(True, "1.2.3.4"))
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.is_allowed_by_robots", return_value=False)
    @patch("backend.pipeline.html_fetcher.record_url_fetch_failure")
    def test_robots_blocked_records_failure(self, mock_record, mock_robots, mock_validate, mock_recent):
        HTMLFetcher.fetch_and_save_if_changed(1, "https://blocked.example.com", 10)
        mock_record.assert_called_once_with(1, "robots_blocked")

    @patch("backend.pipeline.html_fetcher.HTMLFetcher._was_fetched_recently", return_value=False)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.validate_url_with_pin", return_value=(False, None))
    @patch("backend.pipeline.html_fetcher.record_url_fetch_failure")
    def test_ssrf_failure_records_failure(self, mock_record, mock_validate, mock_recent):
        HTMLFetcher.fetch_and_save_if_changed(1, "https://evil.example.com", 10)
        mock_record.assert_called_once_with(1, "ssrf_blocked")

    @patch("backend.pipeline.html_fetcher.HTMLFetcher._was_fetched_recently", return_value=False)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.validate_url_with_pin", return_value=(True, "1.2.3.4"))
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.is_allowed_by_robots", return_value=True)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.fetch_html", side_effect=ResponseTooLarge("2MB"))
    @patch("backend.pipeline.html_fetcher.record_url_fetch_failure")
    def test_response_too_large_records_failure(self, mock_record, mock_fetch, mock_robots, mock_validate, mock_recent):
        HTMLFetcher.fetch_and_save_if_changed(1, "https://huge.example.com", 10)
        mock_record.assert_called_once_with(1, "response_too_large")

    @patch("backend.pipeline.html_fetcher.HTMLFetcher._was_fetched_recently", return_value=False)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.validate_url_with_pin", return_value=(True, "1.2.3.4"))
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.is_allowed_by_robots", return_value=True)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.fetch_html", return_value="<html>content</html>")
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.extract_text_content", return_value="content")
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.normalize_text", return_value="content")
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.hash_text_content", return_value="abc123")
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.has_text_changed", return_value=True)
    @patch("backend.pipeline.html_fetcher.HTMLFetcher.save_text")
    @patch("backend.pipeline.html_fetcher.record_url_fetch_success")
    def test_successful_fetch_records_success(self, mock_record, mock_save, mock_changed,
                                               mock_hash, mock_norm, mock_extract,
                                               mock_fetch, mock_robots, mock_validate, mock_recent):
        HTMLFetcher.fetch_and_save_if_changed(1, "https://good.example.com", 10)
        mock_record.assert_called_once_with(1)
