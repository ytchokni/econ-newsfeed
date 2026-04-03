"""Tests for API middleware: security headers and CORS."""
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def client():
    """Create a test client with mocked database and scheduler."""
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
# Task 1.1: Security headers middleware
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    """All responses must include the four security headers from DESIGN.md 4.5."""

    def test_x_content_type_options(self, client):
        response = client.get("/api/publications")
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_x_frame_options(self, client):
        response = client.get("/api/publications")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_content_security_policy(self, client):
        response = client.get("/api/publications")
        assert response.headers["Content-Security-Policy"] == "default-src 'self'"

    def test_strict_transport_security(self, client):
        response = client.get("/api/publications")
        assert response.headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"

    def test_all_security_headers_present_on_error(self, client):
        """Security headers must be present even on error responses."""
        response = client.get("/api/publications/999999")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Content-Security-Policy") == "default-src 'self'"
        assert response.headers.get("Strict-Transport-Security") == "max-age=63072000; includeSubDomains"


# ---------------------------------------------------------------------------
# Task 1.2: CORS middleware
# ---------------------------------------------------------------------------

class TestCORS:
    """CORS must allow only the FRONTEND_URL origin."""

    def test_allowed_origin_returns_cors_headers(self, client):
        response = client.options(
            "/api/publications",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_disallowed_origin_no_cors_header(self, client):
        response = client.options(
            "/api/publications",
            headers={
                "Origin": "http://evil.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "http://evil.com"

    def test_no_wildcard_origin(self, client):
        response = client.options(
            "/api/publications",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "*"


# ---------------------------------------------------------------------------
# Task 1.3: Standard error envelope
# ---------------------------------------------------------------------------

class TestErrorEnvelope:
    """All error responses must use the standard envelope from DESIGN.md 4.4."""

    def test_404_error_envelope(self, client):
        response = client.get("/api/publications/999999")
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "not_found"
        assert "message" in body["error"]

    def test_400_error_envelope(self, client):
        response = client.get("/api/publications?page=-1")
        assert response.status_code == 400
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "bad_request"
        assert "message" in body["error"]

    def test_422_validation_error_envelope(self, client):
        """FastAPI validation errors (e.g. type mismatch) get wrapped in our envelope as 400."""
        response = client.get("/api/publications?page=abc")
        assert response.status_code == 400
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "bad_request"
        assert "message" in body["error"]

    def test_401_error_envelope(self, client):
        response = client.post("/api/scrape")
        assert response.status_code == 401
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "unauthorized"
        assert "message" in body["error"]
