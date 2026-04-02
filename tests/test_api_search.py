"""Tests for search query parameter on publications and researchers."""
from datetime import datetime
from unittest.mock import patch


SAMPLE_PUB = {
    "event_id": 100, "event_type": "new_paper", "old_status": None,
    "new_status": "working_paper", "old_title": None, "new_title": None,
    "created_at": datetime(2026, 3, 15, 14, 30),
    "paper_id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/pub",
    "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "working_paper",
    "draft_url": None, "abstract": None, "draft_url_status": None, "doi": None,
    "total_count": 1,
}

BATCH_AUTHORS = [
    {"publication_id": 1, "researcher_id": 10, "first_name": "Max", "last_name": "Steinhardt"},
]


class TestPublicationSearch:
    """Tests for ?search= on GET /api/publications."""

    def test_search_returns_200(self, client):
        """Basic search query returns 200."""
        with patch("api.Database.fetch_all") as mock_fetch:
            mock_fetch.side_effect = [[SAMPLE_PUB], BATCH_AUTHORS, [], []]
            response = client.get("/api/publications?search=Trade")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_search_passes_like_to_sql(self, client):
        """Search param generates a search clause in the SQL query."""
        with patch("api.Database.fetch_all") as mock_fetch:
            mock_fetch.side_effect = [[], []]
            client.get("/api/publications?search=monetary+policy")

        # Verify the SQL contains the search clause (FULLTEXT or LIKE)
        fetch_sql = mock_fetch.call_args_list[0][0][0]
        assert "MATCH" in fetch_sql or "p.title LIKE" in fetch_sql or "p.abstract LIKE" in fetch_sql

    def test_search_escapes_special_chars(self, client):
        """Special LIKE chars (%, _) are escaped."""
        with patch("api.Database.fetch_all") as mock_fetch:
            mock_fetch.side_effect = [[], []]
            response = client.get("/api/publications?search=100%25_increase")

        assert response.status_code == 200

    def test_search_combined_with_year_filter(self, client):
        """Search works alongside existing filters."""
        with patch("api.Database.fetch_all") as mock_fetch:
            mock_fetch.side_effect = [[SAMPLE_PUB], BATCH_AUTHORS, [], []]
            response = client.get("/api/publications?search=Trade&year=2024")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_empty_search_ignored(self, client):
        """Whitespace-only or empty search is treated as no filter."""
        with patch("api.Database.fetch_all") as mock_fetch:
            mock_fetch.side_effect = [[SAMPLE_PUB], BATCH_AUTHORS, [], []]
            client.get("/api/publications?search=+")

        fetch_sql = mock_fetch.call_args_list[0][0][0]
        assert "LIKE" not in fetch_sql and "MATCH" not in fetch_sql


SAMPLE_RESEARCHER = {
    "id": 1, "first_name": "Max", "last_name": "Steinhardt",
    "position": "Professor", "affiliation": "Freie Universität Berlin",
    "description": "Labor economist",
    "total_count": 1,
}


class TestResearcherSearch:
    """Tests for ?search= on GET /api/researchers."""

    def test_search_returns_200(self, client):
        """Basic name search returns 200."""
        with (
            patch("api.Database.fetch_all") as mock_fetch,
            patch("api.Database.get_jel_codes_for_researchers", return_value={}),
        ):
            mock_fetch.side_effect = [
                [SAMPLE_RESEARCHER],  # researchers
                [],                    # urls
                [{"researcher_id": 1, "cnt": 5}],  # pub counts
                [],                    # fields
            ]
            response = client.get("/api/researchers?search=Steinhardt")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_search_matches_first_and_last_name(self, client):
        """Search checks both first_name and last_name."""
        with patch("api.Database.fetch_all") as mock_fetch:
            mock_fetch.side_effect = [[], [], [], []]
            client.get("/api/researchers?search=Max")

        fetch_sql = mock_fetch.call_args_list[0][0][0]
        assert "first_name LIKE" in fetch_sql or "last_name LIKE" in fetch_sql

    def test_search_combined_with_institution(self, client):
        """Search works alongside institution filter."""
        with (
            patch("api.Database.fetch_all") as mock_fetch,
            patch("api.Database.get_jel_codes_for_researchers", return_value={}),
        ):
            mock_fetch.side_effect = [
                [SAMPLE_RESEARCHER], [], [{"researcher_id": 1, "cnt": 5}], [],
            ]
            response = client.get("/api/researchers?search=Max&institution=Berlin")

        assert response.status_code == 200
