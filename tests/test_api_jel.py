"""Tests for JEL code API endpoints and researcher extensions."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
    ):
        from api import app
        with TestClient(app) as c:
            yield c


SAMPLE_JEL_CODES = [
    {"code": "J", "name": "Labor and Demographic Economics", "parent_code": None},
    {"code": "F", "name": "International Economics", "parent_code": None},
]


class TestGetJelCodes:
    @patch("database.Database.get_all_jel_codes", return_value=SAMPLE_JEL_CODES)
    def test_returns_jel_codes(self, mock_db, client):
        resp = client.get("/api/jel-codes")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 2
        assert data["items"][0]["code"] == "J"

    @patch("database.Database.get_all_jel_codes", return_value=[])
    def test_empty_list(self, mock_db, client):
        resp = client.get("/api/jel-codes")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# Researcher detail should include jel_codes
SAMPLE_RESEARCHER = {
    "id": 1, "first_name": "Jane", "last_name": "Doe",
    "position": "Professor", "affiliation": "MIT",
    "description": "Labor economist.",
}

SAMPLE_JEL_FOR_R1 = [
    {"code": "J", "name": "Labor and Demographic Economics"},
]


class TestResearcherDetailIncludesJel:
    @patch("database.Database.fetch_all")
    @patch("database.Database.fetch_one")
    def test_researcher_detail_has_jel_codes(self, mock_one, mock_all, client):
        """Researcher detail endpoint should include a jel_codes array."""
        mock_one.return_value = SAMPLE_RESEARCHER
        mock_all.return_value = []

        with patch("api._get_urls_for_researcher", return_value=[]), \
             patch("api._get_pub_count_for_researcher", return_value=0), \
             patch("api._get_fields_for_researcher", return_value=[]), \
             patch("api._get_jel_codes_for_researcher", return_value=SAMPLE_JEL_FOR_R1):
            resp = client.get("/api/researchers/1")

        assert resp.status_code == 200
        data = resp.json()
        assert "jel_codes" in data
        assert data["jel_codes"][0]["code"] == "J"
