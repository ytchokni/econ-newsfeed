import os
from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport

from backend.api import app


@pytest.fixture
def api_key_header():
    return {"X-API-Key": os.environ["SCRAPE_API_KEY"]}


@pytest.mark.anyio
async def test_get_deactivated_urls(api_key_header):
    with patch("backend.api.db_get_deactivated_urls") as mock_fn:
        mock_fn.return_value = [
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
    with patch("backend.api.db_get_at_risk_urls") as mock_fn:
        mock_fn.return_value = [
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
    with patch("backend.api.db_reactivate_url") as mock_fn:
        mock_fn.return_value = None
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/reactivate-url/42", headers=api_key_header)
        assert resp.status_code == 200
        mock_fn.assert_called_once_with(42)


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
