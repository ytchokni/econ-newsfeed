"""Tests for scrape trigger and status endpoints."""
import threading
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked database and scheduler."""
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
    ):
        from api import app

        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# Task 4.2: POST /api/scrape
# ---------------------------------------------------------------------------

class TestTriggerScrape:
    """Tests for POST /api/scrape."""

    def test_missing_api_key_returns_401(self, client):
        response = client.post("/api/scrape")
        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "unauthorized"

    def test_invalid_api_key_returns_401(self, client):
        response = client.post("/api/scrape", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "unauthorized"

    def test_valid_key_returns_201(self, client):
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        with (
            patch("scheduler._scrape_lock", mock_lock),
            patch("api.create_scrape_log", return_value=15),
            patch("api.threading.Thread") as mock_thread,
        ):
            response = client.post(
                "/api/scrape",
                headers={"X-API-Key": "test-secret-key"},
            )

        assert response.status_code == 201
        body = response.json()
        assert body["scrape_id"] == 15
        assert body["status"] == "running"
        assert "started_at" in body

    def test_already_running_returns_409(self, client):
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False

        with patch("scheduler._scrape_lock", mock_lock):
            response = client.post(
                "/api/scrape",
                headers={"X-API-Key": "test-secret-key"},
            )

        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "scrape_in_progress"


# ---------------------------------------------------------------------------
# Task 4.3: GET /api/scrape/status
# ---------------------------------------------------------------------------

class TestScrapeStatus:
    """Tests for GET /api/scrape/status."""

    def test_returns_last_scrape_info(self, client):
        last_scrape_row = (
            14, "completed",
            datetime(2026, 3, 16, 10, 0, 0),
            datetime(2026, 3, 16, 10, 4, 32),
            45, 3, 7,
        )
        with (
            patch("api.Database.fetch_one", return_value=last_scrape_row),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            response = client.get("/api/scrape/status")

        assert response.status_code == 200
        body = response.json()
        assert body["last_scrape"]["id"] == 14
        assert body["last_scrape"]["status"] == "completed"
        assert body["last_scrape"]["urls_checked"] == 45
        assert body["last_scrape"]["urls_changed"] == 3
        assert body["last_scrape"]["pubs_extracted"] == 7
        assert body["interval_hours"] == 24
        assert "next_scrape_at" in body

    def test_returns_null_when_no_scrapes(self, client):
        with (
            patch("api.Database.fetch_one", return_value=None),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            response = client.get("/api/scrape/status")

        assert response.status_code == 200
        body = response.json()
        assert body["last_scrape"] is None
        assert body["interval_hours"] == 24
