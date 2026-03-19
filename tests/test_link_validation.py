"""Tests for HTMLFetcher.validate_draft_url() and the PublicationExtraction model.

validate_draft_url() is a new v2 method that performs an HTTP HEAD request with
SSRF protection and returns 'valid', 'invalid', or 'timeout'.

PublicationExtraction is also tested here for the new 'working_paper' status and
the 'abstract' field added in v2.
"""
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

import pytest
import requests

from html_fetcher import HTMLFetcher
from publication import PublicationExtraction


# ---------------------------------------------------------------------------
# PublicationExtraction model — new v2 fields
# ---------------------------------------------------------------------------

class TestPublicationExtractionModel:
    """Pydantic model validation for the new abstract and working_paper fields."""

    def _valid_base(self, **overrides):
        """Return a minimal valid publication dict, with overrides applied."""
        data = {
            "title": "Trade and Wages",
            "authors": [["Jane", "Doe"]],
        }
        data.update(overrides)
        return data

    # abstract field

    def test_abstract_accepts_string(self):
        pub = PublicationExtraction(**self._valid_base(abstract="This paper studies trade."))
        assert pub.abstract == "This paper studies trade."

    def test_abstract_defaults_to_none_when_absent(self):
        pub = PublicationExtraction(**self._valid_base())
        assert pub.abstract is None

    def test_abstract_accepts_none_explicitly(self):
        pub = PublicationExtraction(**self._valid_base(abstract=None))
        assert pub.abstract is None

    def test_abstract_accepts_empty_string(self):
        pub = PublicationExtraction(**self._valid_base(abstract=""))
        assert pub.abstract == ""

    # working_paper status

    def test_working_paper_is_accepted_status(self):
        pub = PublicationExtraction(**self._valid_base(status="working_paper"))
        assert pub.status == "working_paper"

    def test_all_valid_statuses_are_accepted(self):
        valid = {"published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper"}
        for s in valid:
            pub = PublicationExtraction(**self._valid_base(status=s))
            assert pub.status == s, f"Status {s!r} was rejected"

    def test_invalid_status_is_rejected(self):
        """Unknown status values are rejected by the Literal type constraint."""
        with pytest.raises(ValidationError):
            PublicationExtraction(**self._valid_base(status="under_review"))

    def test_status_none_is_valid(self):
        pub = PublicationExtraction(**self._valid_base(status=None))
        assert pub.status is None

    # model_dump includes both fields

    def test_model_dump_includes_abstract_and_status(self):
        pub = PublicationExtraction(**self._valid_base(abstract="Some abstract", status="working_paper"))
        d = pub.model_dump()
        assert "abstract" in d
        assert d["abstract"] == "Some abstract"
        assert d["status"] == "working_paper"

    # draft_url validation still works

    def test_http_draft_url_accepted(self):
        pub = PublicationExtraction(**self._valid_base(draft_url="http://ssrn.com/abstract=1"))
        assert pub.draft_url == "http://ssrn.com/abstract=1"

    def test_ftp_draft_url_coerced_to_none(self):
        pub = PublicationExtraction(**self._valid_base(draft_url="ftp://example.com/paper.pdf"))
        assert pub.draft_url is None


# ---------------------------------------------------------------------------
# HTMLFetcher.validate_draft_url() — HTTP HEAD with SSRF protection
# ---------------------------------------------------------------------------

class TestValidateDraftUrl:
    """validate_draft_url() returns 'valid', 'invalid', or 'timeout'."""

    # ------------------------------------------------------------------
    # SSRF guard — private/invalid URLs are rejected immediately
    # ------------------------------------------------------------------

    def test_private_ip_url_returns_invalid(self):
        """SSRF-blocked URLs (private IP) must return 'invalid' without making HTTP call."""
        with patch("html_fetcher.HTMLFetcher.validate_url", return_value=(False, None)):
            result = HTMLFetcher.validate_draft_url("http://192.168.1.1/paper.pdf")
        assert result == "invalid"

    def test_localhost_url_returns_invalid(self):
        with patch("html_fetcher.HTMLFetcher.validate_url", return_value=(False, None)):
            result = HTMLFetcher.validate_draft_url("http://localhost/paper.pdf")
        assert result == "invalid"

    def test_non_http_scheme_returns_invalid(self):
        with patch("html_fetcher.HTMLFetcher.validate_url", return_value=(False, None)):
            result = HTMLFetcher.validate_draft_url("ftp://example.com/paper.pdf")
        assert result == "invalid"

    # ------------------------------------------------------------------
    # HTTP HEAD responses
    # ------------------------------------------------------------------

    def _make_mock_response(self, status_code: int) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        return resp

    def _patch_for_valid_ssrf(self, resolved_ip="1.2.3.4"):
        """Context manager: SSRF validation passes, resolved_ip is returned."""
        return patch(
            "html_fetcher.HTMLFetcher.validate_url",
            return_value=(True, resolved_ip),
        )

    def test_200_response_returns_valid(self):
        mock_resp = self._make_mock_response(200)
        with self._patch_for_valid_ssrf():
            with patch("requests.Session.head", return_value=mock_resp):
                result = HTMLFetcher.validate_draft_url("https://ssrn.com/abstract=12345")
        assert result == "valid"

    def test_301_redirect_returns_valid(self):
        """Responses below 400 (including redirects) are considered valid."""
        mock_resp = self._make_mock_response(301)
        with self._patch_for_valid_ssrf():
            with patch("requests.Session.head", return_value=mock_resp):
                result = HTMLFetcher.validate_draft_url("https://papers.nber.org/paper/123")
        assert result == "valid"

    def test_404_response_returns_invalid(self):
        mock_resp = self._make_mock_response(404)
        with self._patch_for_valid_ssrf():
            with patch("requests.Session.head", return_value=mock_resp):
                result = HTMLFetcher.validate_draft_url("https://example.com/missing.pdf")
        assert result == "invalid"

    def test_500_response_returns_invalid(self):
        mock_resp = self._make_mock_response(500)
        with self._patch_for_valid_ssrf():
            with patch("requests.Session.head", return_value=mock_resp):
                result = HTMLFetcher.validate_draft_url("https://example.com/broken")
        assert result == "invalid"

    def test_403_response_returns_invalid(self):
        mock_resp = self._make_mock_response(403)
        with self._patch_for_valid_ssrf():
            with patch("requests.Session.head", return_value=mock_resp):
                result = HTMLFetcher.validate_draft_url("https://example.com/forbidden.pdf")
        assert result == "invalid"

    # ------------------------------------------------------------------
    # Timeout and network errors
    # ------------------------------------------------------------------

    def test_timeout_exception_returns_timeout(self):
        with self._patch_for_valid_ssrf():
            with patch(
                "requests.Session.head",
                side_effect=requests.exceptions.Timeout("timed out"),
            ):
                result = HTMLFetcher.validate_draft_url("https://slow-server.example.com/paper.pdf")
        assert result == "timeout"

    def test_connection_error_returns_invalid(self):
        with self._patch_for_valid_ssrf():
            with patch(
                "requests.Session.head",
                side_effect=requests.exceptions.ConnectionError("connection refused"),
            ):
                result = HTMLFetcher.validate_draft_url("https://offline.example.com/paper.pdf")
        assert result == "invalid"

    def test_generic_request_exception_returns_invalid(self):
        with self._patch_for_valid_ssrf():
            with patch(
                "requests.Session.head",
                side_effect=requests.exceptions.RequestException("unknown error"),
            ):
                result = HTMLFetcher.validate_draft_url("https://example.com/paper.pdf")
        assert result == "invalid"

    # ------------------------------------------------------------------
    # Return type contract
    # ------------------------------------------------------------------

    def test_return_value_is_always_string(self):
        """The return value must always be a plain string (not an enum or None)."""
        scenarios = [
            (False, None),   # SSRF blocked
        ]
        for ssrf_return in scenarios:
            with patch("html_fetcher.HTMLFetcher.validate_url", return_value=ssrf_return):
                result = HTMLFetcher.validate_draft_url("https://example.com/paper.pdf")
            assert isinstance(result, str), f"Expected str, got {type(result)}"

    def test_return_value_is_one_of_three_values(self):
        """validate_draft_url must return exactly one of: 'valid', 'invalid', 'timeout'."""
        mock_resp = self._make_mock_response(200)
        with self._patch_for_valid_ssrf():
            with patch("requests.Session.head", return_value=mock_resp):
                result = HTMLFetcher.validate_draft_url("https://ssrn.com/abstract=1")
        assert result in {"valid", "invalid", "timeout"}
