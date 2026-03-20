"""Tests for search query parameter on publications and researchers."""
from datetime import datetime
from unittest.mock import patch


SAMPLE_PUB = {
    "event_id": 100, "event_type": "new_paper", "old_status": None,
    "new_status": "working_paper", "created_at": datetime(2026, 3, 15, 14, 30),
    "paper_id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/pub",
    "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "working_paper",
    "draft_url": None, "abstract": None, "draft_url_status": None,
}

BATCH_AUTHORS = [
    {"publication_id": 1, "researcher_id": 10, "first_name": "Max", "last_name": "Steinhardt"},
]


class TestPublicationSearch:
    """Tests for ?search= on GET /api/publications."""

    def test_search_returns_200(self, client):
        """Basic search query returns 200."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[SAMPLE_PUB], BATCH_AUTHORS]
            response = client.get("/api/publications?search=Trade")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_search_passes_like_to_sql(self, client):
        """Search param generates a LIKE clause in the SQL query."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 0}) as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            client.get("/api/publications?search=monetary+policy")

        # Verify the SQL contains the LIKE clause
        count_sql = mock_one.call_args[0][0]
        assert "p.title LIKE" in count_sql or "p.abstract LIKE" in count_sql

    def test_search_escapes_special_chars(self, client):
        """Special LIKE chars (%, _) are escaped."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 0}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            response = client.get("/api/publications?search=100%25_increase")

        assert response.status_code == 200

    def test_search_combined_with_year_filter(self, client):
        """Search works alongside existing filters."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[SAMPLE_PUB], BATCH_AUTHORS]
            response = client.get("/api/publications?search=Trade&year=2024")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_empty_search_ignored(self, client):
        """Whitespace-only or empty search is treated as no filter."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}) as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[SAMPLE_PUB], BATCH_AUTHORS]
            client.get("/api/publications?search=+")

        count_sql = mock_one.call_args[0][0]
        assert "LIKE" not in count_sql
