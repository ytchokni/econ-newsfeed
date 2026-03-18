"""Security-focused test cases.

Covers: SQL injection in query params, SSRF bypass attempts,
oversized inputs, and verifying error responses contain no stack traces.
"""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


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
            with patch("api.Database.fetch_one", return_value=(0,)), \
                 patch("api.Database.fetch_all", return_value=[]):
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
        with patch("api.Database.fetch_one", return_value=(0,)), \
             patch("api.Database.fetch_all", return_value=[]):
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
