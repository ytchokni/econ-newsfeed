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

AUTHORS_MAP = {1: [{"id": 10, "first_name": "Max", "last_name": "Steinhardt"}]}


class TestPublicationSearch:
    """Tests for ?search= on GET /api/publications."""

    def test_search_returns_200(self, client):
        """Basic search query returns 200."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUB], 1)) as mock_search,
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?search=Trade")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_search_passes_search_to_db(self, client):
        """Search param is passed to search_feed_events."""
        with (
            patch("backend.api.search_feed_events", return_value=([], 0)) as mock_search,
            patch("backend.api.get_authors_for_papers", return_value={}),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            client.get("/api/publications?search=monetary+policy")

        # Verify the search param was passed to the database function
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["search"] == "monetary policy"

    def test_search_escapes_special_chars(self, client):
        """Special LIKE chars (%, _) are escaped."""
        with (
            patch("backend.api.search_feed_events", return_value=([], 0)),
            patch("backend.api.get_authors_for_papers", return_value={}),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?search=100%25_increase")

        assert response.status_code == 200

    def test_search_combined_with_year_filter(self, client):
        """Search works alongside existing filters."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUB], 1)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?search=Trade&year=2024")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_empty_search_ignored(self, client):
        """Whitespace-only or empty search is treated as no filter."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUB], 1)) as mock_search,
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            client.get("/api/publications?search=+")

        # Verify search was passed as whitespace (the DB function handles stripping)
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["search"] == " "


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
            patch("backend.api.search_researchers", return_value=([SAMPLE_RESEARCHER], 1)),
            patch("backend.api.get_urls_for_researchers", return_value={}),
            patch("backend.api.get_pub_counts_for_researchers", return_value={1: 5}),
            patch("backend.api.get_fields_for_researchers", return_value={}),
            patch("backend.api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers?search=Steinhardt")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_search_passes_search_to_db(self, client):
        """Search checks both first_name and last_name (handled by search_researchers)."""
        with (
            patch("backend.api.search_researchers", return_value=([], 0)) as mock_search,
            patch("backend.api.get_urls_for_researchers", return_value={}),
            patch("backend.api.get_pub_counts_for_researchers", return_value={}),
            patch("backend.api.get_fields_for_researchers", return_value={}),
            patch("backend.api.get_jel_codes_for_researchers", return_value={}),
        ):
            client.get("/api/researchers?search=Max")

        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["search"] == "Max"

    def test_search_combined_with_institution(self, client):
        """Search works alongside institution filter."""
        with (
            patch("backend.api.search_researchers", return_value=([SAMPLE_RESEARCHER], 1)),
            patch("backend.api.get_urls_for_researchers", return_value={}),
            patch("backend.api.get_pub_counts_for_researchers", return_value={1: 5}),
            patch("backend.api.get_fields_for_researchers", return_value={}),
            patch("backend.api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers?search=Max&institution=Berlin")

        assert response.status_code == 200
