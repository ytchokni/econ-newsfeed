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
    def test_researcher_detail_has_jel_codes(self, client):
        """Researcher detail endpoint should include a jel_codes array."""
        with (
            patch("api.Database.get_researcher_detail", return_value=SAMPLE_RESEARCHER),
            patch("api.Database.get_urls_for_researchers", return_value={1: []}),
            patch("api.Database.get_pub_counts_for_researchers", return_value={1: 0}),
            patch("api.Database.get_fields_for_researchers", return_value={1: []}),
            patch("api.Database.get_jel_codes_for_researcher", return_value=SAMPLE_JEL_FOR_R1),
            patch("api.Database.get_researcher_papers", return_value=[]),
            patch("api.Database.get_authors_for_papers", return_value={}),
            patch("api.Database.get_coauthors_for_papers", return_value={}),
            patch("api.Database.get_links_for_papers", return_value={}),
        ):
            resp = client.get("/api/researchers/1")

        assert resp.status_code == 200
        data = resp.json()
        assert "jel_codes" in data
        assert data["jel_codes"][0]["code"] == "J"
