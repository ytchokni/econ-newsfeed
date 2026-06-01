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

from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport

from api import app


@pytest.fixture
def api_key_header():
    return {"X-API-Key": os.environ["SCRAPE_API_KEY"]}


@pytest.mark.anyio
async def test_get_deactivated_urls(api_key_header):
    with patch("api.Database") as mock_db:
        mock_db.get_deactivated_urls.return_value = [
            {"id": 1, "url": "https://dead.example.com", "researcher_name": "Smith",
             "deactivation_reason": "consecutive_failures", "deactivated_at": "2026-06-01",
             "page_type": "HOME", "consecutive_failures": 3, "researcher_id": 10}
        ]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/admin/deactivated-urls", headers=api_key_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["url"] == "https://dead.example.com"


@pytest.mark.anyio
async def test_get_at_risk_urls(api_key_header):
    with patch("api.Database") as mock_db:
        mock_db.get_at_risk_urls.return_value = [
            {"id": 5, "url": "https://flaky.example.com", "consecutive_failures": 2,
             "page_type": "PAPERS", "researcher_name": "Jones", "researcher_id": 20}
        ]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/admin/at-risk-urls", headers=api_key_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


@pytest.mark.anyio
async def test_reactivate_url(api_key_header):
    with patch("api.Database") as mock_db:
        mock_db.reactivate_url.return_value = None
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/reactivate-url/42", headers=api_key_header)
        assert resp.status_code == 200
        mock_db.reactivate_url.assert_called_once_with(42)


@pytest.mark.anyio
async def test_deactivated_urls_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/admin/deactivated-urls")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_reactivate_url_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/admin/reactivate-url/42")
    assert resp.status_code == 401
