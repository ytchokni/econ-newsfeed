import os
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from backend.api import app


@pytest.fixture
def headers():
    return {"X-API-Key": os.environ["SCRAPE_API_KEY"]}


@pytest.mark.anyio
async def test_bulk_approve_is_disabled(headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/admin/discoveries/bulk-approve", headers=headers)
    assert resp.status_code == 410


@pytest.mark.anyio
async def test_approve_returns_409_with_preflight_reason(headers):
    with patch("backend.api.db_approve_discovery", return_value={"approved": False, "reason": "URL belongs to another researcher"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/discoveries/5/approve", headers=headers)
    assert resp.status_code == 409
    assert "another researcher" in resp.text


@pytest.mark.anyio
async def test_approve_ok(headers):
    with patch("backend.api.db_approve_discovery", return_value={"approved": True, "reason": None}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/discoveries/5/approve", headers=headers)
    assert resp.status_code == 200
