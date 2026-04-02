"""Security-focused test cases.

Covers: SQL injection in query params, SSRF bypass attempts,
oversized inputs, and verifying error responses contain no stack traces.
"""
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def client():
    """Test client with mocked database and scheduler."""
    with (
        patch("database.Database.create_tables"),
        patch("database.Database.get_connection", return_value=None),
        patch("database.Database.fetch_all", return_value=[]),
        patch("database.Database.fetch_one", return_value=None),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
        patch("api.connection_scope", _noop_connection_scope),
    ):
        from api import app

        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# SQL injection — query parameters
# ---------------------------------------------------------------------------

class TestSQLInjectionQueryParams:
    """Injected SQL in query params must be rejected or safely parameterised."""

    SQL_PAYLOADS = [
        "1 OR 1=1",
        "1; DROP TABLE publications; --",
        "' OR '1'='1",
        "1 UNION SELECT null,null,null--",
        "1/**/OR/**/1=1",
    ]

    def test_year_sql_injection_returns_safe_response(self, client):
        """year= accepts only a 4-char string; SQL payload should not crash the API."""
        for payload in self.SQL_PAYLOADS:
            with patch("api.Database.fetch_all", return_value=[]):
                resp = client.get(f"/api/publications?year={payload}")
            # Must not be a 500 (unhandled exception)
            assert resp.status_code in (200, 400, 422), (
                f"Unexpected status {resp.status_code} for payload: {payload!r}"
            )

    def test_researcher_id_non_integer_rejected(self, client):
        """researcher_id must be an integer; non-integer input is rejected as 400."""
        resp = client.get("/api/publications?researcher_id=1%20OR%201%3D1")
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_publication_id_path_non_integer(self, client):
        """publication_id path param must be an integer; strings are rejected."""
        resp = client.get("/api/publications/1%27%20OR%20%271%27%3D%271")
        assert resp.status_code in (400, 404, 422)

    def test_researcher_id_path_non_integer(self, client):
        """researcher_id path param must be an integer; strings are rejected."""
        resp = client.get("/api/researchers/1%27%20OR%20%271%27%3D%271")
        assert resp.status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# SSRF bypass attempts via validate_url
# ---------------------------------------------------------------------------

class TestSSRFValidation:
    """validate_url must reject all known SSRF bypass patterns."""

    def _validate(self, url):
        from html_fetcher import HTMLFetcher
        return HTMLFetcher.validate_url(url)

    def test_rejects_file_scheme(self):
        assert self._validate("file:///etc/passwd") is False

    def test_rejects_ftp_scheme(self):
        assert self._validate("ftp://example.com/file") is False

    def test_rejects_localhost(self):
        assert self._validate("http://localhost/admin") is False

    def test_rejects_loopback_ip(self):
        assert self._validate("http://127.0.0.1/admin") is False

    def test_rejects_loopback_ipv6(self):
        assert self._validate("http://[::1]/admin") is False

    def test_rejects_private_class_a(self):
        assert self._validate("http://10.0.0.1/secret") is False

    def test_rejects_private_class_b(self):
        assert self._validate("http://172.16.0.1/secret") is False

    def test_rejects_private_class_c(self):
        assert self._validate("http://192.168.1.1/router") is False

    def test_rejects_aws_metadata(self):
        assert self._validate("http://169.254.169.254/latest/meta-data/") is False

    def test_rejects_gcp_metadata(self):
        assert self._validate("http://metadata.google.internal/computeMetadata/v1/") is False

    def test_rejects_no_scheme(self):
        assert self._validate("//example.com/page") is False

    def test_accepts_valid_public_url(self):
        """A public HTTP URL must pass (DNS resolved to a real public IP)."""
        import socket
        import ipaddress

        # Only run this assertion if the hostname resolves to a public IP
        try:
            ip_str = socket.getaddrinfo("example.com", None)[0][4][0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback:
                pytest.skip("example.com resolves to private IP in this environment")
        except socket.gaierror:
            pytest.skip("DNS not available in this environment")

        assert self._validate("https://example.com/page") is True

    def test_validate_url_returns_true_on_public_ip(self):
        """validate_url must return True for a public IP."""
        from html_fetcher import HTMLFetcher
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("1.2.3.4", 0))]):
            assert HTMLFetcher.validate_url("https://example.com/page") is True

    def test_validate_url_returns_false_on_private_ip(self):
        """validate_url must return False when IP is private."""
        from html_fetcher import HTMLFetcher
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.1", 0))]):
            assert HTMLFetcher.validate_url("https://example.com/page") is False


# ---------------------------------------------------------------------------
# Oversized inputs
# ---------------------------------------------------------------------------

class TestOversizedInputs:
    """Oversized query parameters must be handled gracefully (no 500)."""

    def test_oversized_year_param(self, client):
        """A very long year string should not crash the API."""
        payload = "A" * 10_000
        with patch("api.Database.fetch_all", return_value=[]):
            resp = client.get(f"/api/publications?year={payload}")
        assert resp.status_code != 500

    def test_oversized_page_param(self, client):
        """A page number larger than sys.maxsize should not crash the API."""
        resp = client.get("/api/publications?page=99999999999999999999999999")
        assert resp.status_code != 500

    def test_oversized_per_page_param(self, client):
        """per_page above the max (100) should be rejected with 400."""
        resp = client.get("/api/publications?per_page=999")
        assert resp.status_code == 400
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Error responses must not leak stack traces
# ---------------------------------------------------------------------------

class TestNoStackTraceLeakage:
    """Error responses must never expose Python tracebacks or internal details."""

    STACK_TRACE_MARKERS = [
        "Traceback",
        "File \"",
        "line ",
        "raise ",
        "Exception",
        "mysql",
        "sqlalchemy",
        "password",
        "secret",
    ]

    def _assert_no_leak(self, body_text: str):
        lower = body_text.lower()
        for marker in self.STACK_TRACE_MARKERS:
            assert marker.lower() not in lower, (
                f"Response leaks internal detail: {marker!r} found in body"
            )

    def test_404_no_stack_trace(self, client):
        resp = client.get("/api/publications/999999")
        assert resp.status_code == 404
        self._assert_no_leak(resp.text)

    def test_400_no_stack_trace(self, client):
        resp = client.get("/api/publications?page=-1")
        assert resp.status_code == 400
        self._assert_no_leak(resp.text)

    def test_401_no_stack_trace(self, client):
        resp = client.post("/api/scrape")
        assert resp.status_code == 401
        self._assert_no_leak(resp.text)

    def test_500_no_stack_trace(self):
        """Simulated internal error must return generic 500 with no details."""
        with (
            patch("database.Database.create_tables"),
            patch("database.Database.get_connection", return_value=None),
            patch("database.Database.fetch_all", return_value=[]),
            patch("database.Database.fetch_one", side_effect=RuntimeError("DB connection failed: password=s3cr3t")),
            patch("scheduler.start_scheduler"),
            patch("scheduler.shutdown_scheduler"),
        ):
            from api import app

            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/publications/1")
        assert resp.status_code == 500
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "internal_error"
        assert body["error"]["message"] == "An unexpected error occurred."
        # Must not contain raw exception text
        assert "password" not in resp.text
        assert "s3cr3t" not in resp.text

    def test_unhandled_exception_returns_generic_500(self):
        """Any unhandled exception must produce a generic 500, not a traceback."""
        with (
            patch("database.Database.create_tables"),
            patch("database.Database.get_connection", return_value=None),
            patch("database.Database.fetch_all", side_effect=Exception("internal detail")),
            patch("database.Database.fetch_one", return_value=None),
            patch("scheduler.start_scheduler"),
            patch("scheduler.shutdown_scheduler"),
            patch("api.connection_scope", _noop_connection_scope),
        ):
            from api import app

            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/researchers")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["message"] == "An unexpected error occurred."
        assert "internal detail" not in resp.text


# ---------------------------------------------------------------------------
# Rate limit handler — error envelope
# ---------------------------------------------------------------------------

class TestRateLimitErrorEnvelope:
    """429 responses from slowapi must use the standard error envelope."""

    def test_rate_limit_response_uses_error_envelope(self, client):
        """Simulated RateLimitExceeded must return {"error": {"code": ..., "message": ...}}."""
        from slowapi.errors import RateLimitExceeded
        from starlette.requests import Request as StarletteRequest

        # Directly invoke the custom handler to verify its shape
        import asyncio
        from api import _rate_limit_handler

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/publications",
            "query_string": b"",
            "headers": [],
        }
        req = StarletteRequest(scope)
        # Create a mock Limit object that RateLimitExceeded expects
        mock_limit = MagicMock()
        mock_limit.error_message = None
        mock_limit.limit = "60 per 1 minute"
        exc = RateLimitExceeded(mock_limit)

        response = asyncio.get_event_loop().run_until_complete(
            _rate_limit_handler(req, exc)
        )
        import json
        body = json.loads(response.body)
        assert response.status_code == 429
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]
        assert body["error"]["code"] == "rate_limit_exceeded"


# ---------------------------------------------------------------------------
# DNS Pinning — validate_url_with_pin
# ---------------------------------------------------------------------------

class TestDNSPinning:
    """validate_url_with_pin must return resolved IP for SSRF prevention."""

    def test_returns_resolved_ip_for_public_address(self):
        from html_fetcher import HTMLFetcher
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
            safe, resolved_ip = HTMLFetcher.validate_url_with_pin("https://example.com/page")
            assert safe is True
            assert resolved_ip == "93.184.216.34"

    def test_rejects_private_ip(self):
        from html_fetcher import HTMLFetcher
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.1", 0))]):
            safe, resolved_ip = HTMLFetcher.validate_url_with_pin("https://evil.com/page")
            assert safe is False
            assert resolved_ip is None

    def test_rejects_metadata_endpoint(self):
        from html_fetcher import HTMLFetcher
        safe, resolved_ip = HTMLFetcher.validate_url_with_pin("http://169.254.169.254/latest/meta-data/")
        assert safe is False

    def test_rejects_non_http_scheme(self):
        from html_fetcher import HTMLFetcher
        safe, _ = HTMLFetcher.validate_url_with_pin("file:///etc/passwd")
        assert safe is False


# ---------------------------------------------------------------------------
# SSRF redirect bypass — fetch_html must validate each redirect hop
# ---------------------------------------------------------------------------

class TestSSRFRedirectBypass:
    """fetch_html must validate redirect targets to prevent SSRF via redirect."""

    def test_redirect_to_private_ip_is_blocked(self):
        """A 302 redirect to 169.254.169.254 must return None."""
        from html_fetcher import HTMLFetcher

        # First request returns 302 pointing to AWS metadata endpoint
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        mock_session = MagicMock()
        mock_session.get.return_value = redirect_response

        with patch.object(HTMLFetcher, "_get_session", return_value=mock_session), \
             patch.object(HTMLFetcher, "_rate_limit"):
            result = HTMLFetcher.fetch_html("https://example.com/page")

        assert result is None

    def test_redirect_without_location_header_returns_none(self):
        """A redirect response with no Location header must return None."""
        from html_fetcher import HTMLFetcher

        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {}  # No Location header
        redirect_response.raise_for_status = MagicMock()

        with patch.object(HTMLFetcher, "_rate_limit"), \
             patch.object(HTMLFetcher, "_get_session") as mock_session:
            session = MagicMock()
            session.get.return_value = redirect_response
            mock_session.return_value = session
            result = HTMLFetcher.fetch_html("https://example.com/redirect")

        assert result is None

    def test_redirect_to_public_ip_is_followed(self):
        """A 302 redirect to a public IP should follow and return content."""
        from html_fetcher import HTMLFetcher

        # First request returns 302 to a public URL
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {"Location": "https://public.example.com/page"}

        # Second request returns 200 with HTML content
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.content = b"<html>OK</html>"
        ok_response.text = "<html>OK</html>"
        ok_response.apparent_encoding = "utf-8"
        ok_response.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.side_effect = [redirect_response, ok_response]

        with patch.object(HTMLFetcher, "_get_session", return_value=mock_session), \
             patch.object(HTMLFetcher, "_rate_limit"), \
             patch.object(HTMLFetcher, "validate_url", return_value=True):
            result = HTMLFetcher.fetch_html("https://example.com/page")

        assert result == "<html>OK</html>"

    def test_redirect_chain_limit(self):
        """More than 5 redirects must return None."""
        from html_fetcher import HTMLFetcher

        # Create a redirect response that always redirects
        def make_redirect():
            resp = MagicMock()
            resp.status_code = 302
            resp.headers = {"Location": "https://public.example.com/hop"}
            return resp

        mock_session = MagicMock()
        mock_session.get.side_effect = [make_redirect() for _ in range(10)]

        with patch.object(HTMLFetcher, "_get_session", return_value=mock_session), \
             patch.object(HTMLFetcher, "_rate_limit"), \
             patch.object(HTMLFetcher, "validate_url", return_value=True):
            result = HTMLFetcher.fetch_html("https://example.com/page")

        assert result is None


# ---------------------------------------------------------------------------
# DNS Rebinding Prevention — resolved IP wired through fetch
# ---------------------------------------------------------------------------

class TestDNSRebindingPrevention:
    """fetch_and_save_if_changed must pass the pinned IP to fetch_html."""

    def test_fetch_and_save_passes_resolved_ip(self):
        """fetch_and_save_if_changed must pass the pinned IP to fetch_html."""
        from html_fetcher import HTMLFetcher

        with patch.object(HTMLFetcher, "_was_fetched_recently", return_value=False), \
             patch.object(HTMLFetcher, "validate_url_with_pin", return_value=(True, "93.184.216.34")), \
             patch.object(HTMLFetcher, "is_allowed_by_robots", return_value=True), \
             patch.object(HTMLFetcher, "fetch_html", return_value=None) as mock_fetch:
            HTMLFetcher.fetch_and_save_if_changed(1, "https://example.com/page", 1)

        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("resolved_ip") == "93.184.216.34"

    def test_fetch_html_uses_dns_pinning(self):
        """When resolved_ip is provided, DNS pinning must be active during fetch."""
        from html_fetcher import HTMLFetcher

        with patch.object(HTMLFetcher, "_rate_limit"), \
             patch("html_fetcher._pin_dns") as mock_pin_dns, \
             patch.object(HTMLFetcher, "_get_session") as mock_session_fn:
            # Set up the context manager mock
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=None)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_pin_dns.return_value = mock_ctx

            session = MagicMock()
            response = MagicMock()
            response.status_code = 200
            response.content = b"<html>OK</html>"
            response.text = "<html>OK</html>"
            response.apparent_encoding = "utf-8"
            response.raise_for_status = MagicMock()
            session.get.return_value = response
            mock_session_fn.return_value = session

            HTMLFetcher.fetch_html("https://example.com/page", resolved_ip="93.184.216.34")

        mock_pin_dns.assert_called_once_with("example.com", "93.184.216.34")
        mock_ctx.__enter__.assert_called_once()
        # URL must NOT be rewritten — original hostname preserved for TLS SNI
        actual_url = session.get.call_args[0][0]
        assert actual_url == "https://example.com/page"

    def test_pin_dns_routes_to_resolved_ip(self):
        """_pin_dns must make urllib3 connect to the pinned IP."""
        from html_fetcher import _pin_dns
        import urllib3.util.connection as urllib3_cn

        original_fn = urllib3_cn.create_connection

        with _pin_dns("example.com", "93.184.216.34"):
            patched_fn = urllib3_cn.create_connection
            assert patched_fn is not original_fn

        # After exiting, original is restored
        assert urllib3_cn.create_connection is original_fn

    def test_pin_dns_only_affects_target_hostname(self):
        """_pin_dns must not affect connections to other hostnames."""
        from html_fetcher import _pin_dns
        import urllib3.util.connection as urllib3_cn

        original_fn = urllib3_cn.create_connection
        calls = []

        # Temporarily replace original to track calls
        def tracking_create_connection(address, *args, **kwargs):
            calls.append(address)
            raise OSError("test — not actually connecting")

        urllib3_cn.create_connection = tracking_create_connection
        try:
            with _pin_dns("example.com", "93.184.216.34"):
                patched = urllib3_cn.create_connection
                # Call for the pinned hostname — should rewrite to IP
                try:
                    patched(("example.com", 443))
                except OSError:
                    pass
                # Call for a different hostname — should pass through unchanged
                try:
                    patched(("other.com", 443))
                except OSError:
                    pass
        finally:
            urllib3_cn.create_connection = original_fn

        assert calls[0] == ("93.184.216.34", 443)
        assert calls[1] == ("other.com", 443)


# ---------------------------------------------------------------------------
# Curl option injection prevention
# ---------------------------------------------------------------------------

class TestCurlOptionInjection:
    """curl subprocess must use -- to prevent option injection via URL."""

    def test_curl_uses_end_of_options_marker(self):
        """The curl command must include '--' before the URL argument."""
        from html_fetcher import HTMLFetcher

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="<html></html>", stderr="")
            HTMLFetcher._fetch_with_curl("https://example.com/page")

        cmd = mock_run.call_args[0][0]
        url_index = cmd.index("https://example.com/page")
        assert cmd[url_index - 1] == "--", f"Expected '--' before URL, got: {cmd}"

    def test_curl_rejects_non_http_url(self):
        """_fetch_with_curl must reject URLs without http(s) scheme."""
        from html_fetcher import HTMLFetcher

        with patch("subprocess.run") as mock_run:
            result = HTMLFetcher._fetch_with_curl("-o /tmp/evil http://attacker.com")

        mock_run.assert_not_called()
        assert result is None

    def test_curl_does_not_follow_redirects(self):
        """curl must not use -L flag (redirects handled at application level)."""
        from html_fetcher import HTMLFetcher

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="<html></html>", stderr="")
            HTMLFetcher._fetch_with_curl("https://example.com/page")

        cmd = mock_run.call_args[0][0]
        assert "-L" not in cmd, f"curl should not follow redirects, found -L in: {cmd}"


# ---------------------------------------------------------------------------
# Operational endpoint authentication
# ---------------------------------------------------------------------------

class TestOperationalEndpointAuth:
    """Operational endpoints must require API key authentication."""

    def test_metrics_requires_api_key(self, client):
        """GET /api/metrics without API key must return 401."""
        resp = client.get("/api/metrics")
        assert resp.status_code == 401

    def test_metrics_with_valid_key_succeeds(self, client):
        """GET /api/metrics with valid API key must return 200."""
        with patch("api.Database.fetch_one", return_value={"publications": 0, "researchers": 0, "scrapes": 0}):
            resp = client.get("/api/metrics", headers={"X-API-Key": "test-secret-key-for-ci-runs"})
        assert resp.status_code == 200

    def test_scrape_status_requires_api_key(self, client):
        """GET /api/scrape/status without API key must return 401."""
        resp = client.get("/api/scrape/status")
        assert resp.status_code == 401

    def test_scrape_status_with_valid_key_succeeds(self, client):
        """GET /api/scrape/status with valid API key must return 200."""
        with patch("api.Database.fetch_one", return_value=None):
            resp = client.get("/api/scrape/status", headers={"X-API-Key": "test-secret-key-for-ci-runs"})
        assert resp.status_code == 200
