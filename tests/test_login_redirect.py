"""Pages that redirect to a sign-in page are not stored (login_required).

Retired classic Google Sites and private new Sites 302 to accounts.google.com.
That sign-in page is 1.25 MB and was rejected as response_too_large; since
compaction lets it fit, it would otherwise be saved as the researcher's page.
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.pipeline.html_fetcher import HTMLFetcher, LoginRequired, is_login_redirect

_SIGNIN = ("https://accounts.google.com/ServiceLogin?continue=https://sites.google.com/site/x/"
           "&followup=https://sites.google.com/site/x/&passive=1209600")


def _redirect(location: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 302
    resp.headers = {"Location": location}
    return resp


@pytest.mark.parametrize("url, expected", [
    (_SIGNIN, True),
    ("https://accounts.google.com/v3/signin/identifier?continue=x", True),
    ("https://ACCOUNTS.google.com/ServiceLogin", True),
    ("https://sites.google.com/view/someone/research", False),
    ("https://www.google.com/search?q=accounts.google.com", False),
    ("https://accounts.google.com.evil.example/ServiceLogin", False),
    ("/relative/path", False),
])
def test_is_login_redirect(url, expected):
    assert is_login_redirect(url) is expected


class TestFetchHtmlLoginRedirect:
    def test_redirect_to_google_signin_raises_without_following(self):
        session = MagicMock()
        session.get.return_value = _redirect(_SIGNIN)

        with patch.object(HTMLFetcher, "_get_session", return_value=session), \
             patch.object(HTMLFetcher, "_rate_limit"), \
             patch.object(HTMLFetcher, "validate_url_with_pin") as validate:
            with pytest.raises(LoginRequired):
                HTMLFetcher.fetch_html("https://sites.google.com/site/x/")

        assert session.get.call_count == 1  # the sign-in page is never downloaded
        validate.assert_not_called()

    def test_signin_after_an_ordinary_hop_raises(self):
        """sites.google.com/site/x -> /site/x/ -> accounts.google.com."""
        session = MagicMock()
        session.get.side_effect = [_redirect("https://sites.google.com/site/x/"), _redirect(_SIGNIN)]

        with patch.object(HTMLFetcher, "_get_session", return_value=session), \
             patch.object(HTMLFetcher, "_rate_limit"), \
             patch.object(HTMLFetcher, "validate_url_with_pin", return_value=(True, None)):
            with pytest.raises(LoginRequired):
                HTMLFetcher.fetch_html("https://sites.google.com/site/x")

        assert session.get.call_count == 2

    def test_unsafe_redirect_still_blocked(self):
        """SSRF validation of other hops is unchanged."""
        session = MagicMock()
        session.get.return_value = _redirect("http://169.254.169.254/latest/meta-data/")

        with patch.object(HTMLFetcher, "_get_session", return_value=session), \
             patch.object(HTMLFetcher, "_rate_limit"):
            assert HTMLFetcher.fetch_html("https://example.com/page") is None


_F = "backend.pipeline.html_fetcher.HTMLFetcher"


@patch(f"{_F}._was_fetched_recently", return_value=False)
@patch(f"{_F}.validate_url_with_pin", return_value=(True, "1.2.3.4"))
@patch(f"{_F}.is_allowed_by_robots", return_value=True)
@patch(f"{_F}.fetch_html", side_effect=LoginRequired(_SIGNIN))
@patch(f"{_F}.save_text")
@patch("backend.pipeline.html_fetcher.record_url_fetch_success")
@patch("backend.pipeline.html_fetcher.record_url_fetch_failure")
def test_scrape_records_login_required_and_saves_nothing(
        mock_failure, mock_success, mock_save, *_):
    assert HTMLFetcher.fetch_and_save_if_changed(1, "https://sites.google.com/site/x/", 10) is False
    mock_failure.assert_called_once_with(1, "login_required")
    mock_success.assert_not_called()
    mock_save.assert_not_called()


@pytest.mark.parametrize("exc", [LoginRequired("x"), __import__(
    "backend.pipeline.html_fetcher", fromlist=["ResponseTooLarge"]).ResponseTooLarge("x")])
def test_subpage_crawler_skips_rejected_pages(exc):
    from backend.discovery import subpage_crawler

    with patch.object(subpage_crawler.HTMLFetcher, "validate_url_with_pin", return_value=(True, None)), \
         patch.object(subpage_crawler.HTMLFetcher, "fetch_html", side_effect=exc):
        assert subpage_crawler.crawl_subpages("https://sites.google.com/site/x/") == []


class TestRecheckTooLargeScript:
    """scripts/recheck_too_large_urls.py backdates pending rows past the window."""

    def _load(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "scripts" / "recheck_too_large_urls.py"
        spec = importlib.util.spec_from_file_location("recheck_too_large_urls", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_dry_run_writes_nothing(self, capsys):
        mod = self._load()
        rows = [{"id": 1, "url": "https://sites.google.com/view/a", "deactivated_at": None}]
        with patch.object(mod, "fetch_all", return_value=rows), \
             patch.object(mod, "execute_query") as exec_q:
            assert mod.main([]) == 0
        exec_q.assert_not_called()
        assert "sites.google.com" in capsys.readouterr().out

    def test_apply_backdates_past_recheck_window(self):
        from backend.database.researchers import TOO_LARGE_RECHECK_DAYS

        mod = self._load()
        rows = [{"id": 1, "url": "https://a.example/", "deactivated_at": None}]
        with patch.object(mod, "fetch_all", side_effect=[rows, []]), \
             patch.object(mod, "execute_query") as exec_q:
            assert mod.main(["--apply", "--before", "2026-11-27T15:00:00"]) == 0
        sql, params = exec_q.call_args[0]
        sql = " ".join(sql.split())
        assert "SET deactivated_at = NOW() - INTERVAL %s DAY" in sql
        assert "deactivation_reason = 'response_too_large'" in sql
        assert "is_active = FALSE" in sql
        assert params[0] == TOO_LARGE_RECHECK_DAYS + 1
        assert params[1] == TOO_LARGE_RECHECK_DAYS
