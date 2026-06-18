"""Tests for publication endpoints."""
from contextlib import contextmanager
from datetime import datetime
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
        patch("backend.api.create_tables"),
        patch("backend.api.start_scheduler"),
        patch("backend.api.shutdown_scheduler"),
        patch("backend.api.connection_scope", _noop_connection_scope),
    ):
        from backend.api import app

        with TestClient(app) as c:
            yield c


# Sample data that mimics search_feed_events return shapes (dicts)
# Feed events row shape: event_id, event_type, old_status, new_status, created_at,
#   paper_id, title, year, venue, url, discovered_at, status, draft_url, abstract, draft_url_status
SAMPLE_PUBLICATIONS = [
    {"event_id": 100, "event_type": "new_paper", "old_status": None, "new_status": "working_paper",
     "old_title": None, "new_title": None,
     "created_at": datetime(2026, 3, 15, 14, 30), "paper_id": 1, "title": "Trade and Wages",
     "year": "2024", "venue": "JLE", "source_url": "https://example.com/pub",
     "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "working_paper",
     "draft_url": "https://ssrn.com/abstract=1", "abstract": None, "draft_url_status": "valid",
     "doi": None},
    {"event_id": 101, "event_type": "new_paper", "old_status": None, "new_status": "accepted",
     "old_title": None, "new_title": None,
     "created_at": datetime(2026, 3, 14, 10, 0), "paper_id": 2, "title": "Immigration Effects",
     "year": "2023", "venue": "QJE", "source_url": "https://example.com/pub2",
     "discovered_at": datetime(2026, 3, 14, 10, 0), "status": "accepted",
     "draft_url": None, "abstract": None, "draft_url_status": None,
     "doi": None},
    {"event_id": 102, "event_type": "new_paper", "old_status": None, "new_status": "working_paper",
     "old_title": None, "new_title": None,
     "created_at": datetime(2026, 3, 13, 9, 0), "paper_id": 3, "title": "Labor Markets",
     "year": "2024", "venue": "AER", "source_url": "https://example.com/pub3",
     "discovered_at": datetime(2026, 3, 13, 9, 0), "status": "working_paper",
     "draft_url": None, "abstract": None, "draft_url_status": None,
     "doi": None},
]

# 14-key papers dict for single publication detail endpoint
SAMPLE_PUB_DETAIL = {
    "id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/pub", "discovered_at": datetime(2026, 3, 15, 14, 30),
    "status": "working_paper", "draft_url": "https://ssrn.com/abstract=1",
    "abstract": None, "draft_url_status": "valid",
    "doi": "10.1257/aer.20181234",
}

SAMPLE_AUTHORS_PUB1 = [
    # {id, first_name, last_name} -- used for single-pub endpoint
    {"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
    {"id": 11, "first_name": "Jane", "last_name": "Doe"},
]

SAMPLE_AUTHORS_PUB2 = [
    {"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
]

# Authors maps returned by Database.get_authors_for_papers
AUTHORS_MAP_PUBS_1_2_3 = {
    1: [{"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
        {"id": 11, "first_name": "Jane", "last_name": "Doe"}],
    2: [{"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"}],
    3: [],
}

AUTHORS_MAP_PUB1 = {
    1: [{"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
        {"id": 11, "first_name": "Jane", "last_name": "Doe"}],
}

AUTHORS_MAP_PUB2 = {
    2: [{"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"}],
}


# ---------------------------------------------------------------------------
# Task 2.2: GET /api/publications
# ---------------------------------------------------------------------------

class TestListPublications:
    """Tests for GET /api/publications."""

    def test_default_pagination(self, client):
        """Default page=1, per_page=20."""
        with (
            patch("backend.api.search_feed_events", return_value=(SAMPLE_PUBLICATIONS, 3)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUBS_1_2_3),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications")

        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 1
        assert body["per_page"] == 20
        assert body["total"] == 3
        assert body["pages"] == 1
        assert len(body["items"]) == 3

    def test_custom_pagination(self, client):
        """Custom page and per_page values."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUBLICATIONS[1]], 3)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUB2),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?page=2&per_page=1")

        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 2
        assert body["per_page"] == 1
        assert body["pages"] == 3

    def test_per_page_capped_at_100(self, client):
        """per_page > 100 should be rejected as 400."""
        response = client.get("/api/publications?per_page=200")
        assert response.status_code == 400

    def test_year_filter(self, client):
        """Filter publications by year."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUBLICATIONS[0]], 1)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUB1),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?year=2024")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_researcher_id_filter(self, client):
        """Filter publications by researcher_id."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUBLICATIONS[0]], 1)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUB1),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?researcher_id=10")

        assert response.status_code == 200

    def test_invalid_page_returns_400(self, client):
        response = client.get("/api/publications?page=-1")
        assert response.status_code == 400

    def test_since_filter(self, client):
        """?since= filters publications discovered after the given timestamp."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUBLICATIONS[0]], 1)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUB1),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?since=2026-03-15T00:00:00Z")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_since_invalid_format_returns_400(self, client):
        """Invalid ?since= value returns 400."""
        response = client.get("/api/publications?since=not-a-date")
        assert response.status_code == 400

    def test_publication_item_shape(self, client):
        """Each item must have id, title, authors, year, venue, source_url, discovered_at."""
        with (
            patch("backend.api.search_feed_events", return_value=([SAMPLE_PUBLICATIONS[0]], 1)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUB1),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert item["id"] == 1
        assert item["title"] == "Trade and Wages"
        assert item["year"] == "2024"
        assert item["venue"] == "JLE"
        assert item["source_url"] == "https://example.com/pub"
        assert "discovered_at" in item
        assert item["status"] == "working_paper"
        assert item["draft_url"] == "https://ssrn.com/abstract=1"
        assert item["draft_available"] is True
        assert len(item["authors"]) == 2
        assert item["authors"][0]["id"] == 10
        assert item["authors"][0]["first_name"] == "Max Friedrich"
        assert item["authors"][0]["last_name"] == "Steinhardt"

    def test_event_type_filter_new_paper(self, client):
        """?event_type=new_paper returns only new_paper events."""
        with (
            patch("backend.api.search_feed_events", return_value=(SAMPLE_PUBLICATIONS[:2], 2)) as mock_search,
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUBS_1_2_3),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?event_type=new_paper")

        assert response.status_code == 200
        # Verify the event_type was passed to search_feed_events
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["event_type"] == "new_paper"

    def test_event_type_filter_status_change(self, client):
        """?event_type=status_change returns only status_change events."""
        status_change_pub = {
            **SAMPLE_PUBLICATIONS[0],
            "event_id": 200,
            "event_type": "status_change",
            "old_status": "working_paper",
            "new_status": "accepted",
        }
        with (
            patch("backend.api.search_feed_events", return_value=([status_change_pub], 1)) as mock_search,
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUB1),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications?event_type=status_change")

        assert response.status_code == 200
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["event_type"] == "status_change"

    def test_event_type_omitted_returns_both(self, client):
        """Omitting event_type returns all events (backward compatible)."""
        with (
            patch("backend.api.search_feed_events", return_value=(SAMPLE_PUBLICATIONS, 3)) as mock_search,
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUBS_1_2_3),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications")

        assert response.status_code == 200
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["event_type"] is None

    def test_event_type_invalid_returns_400(self, client):
        """Invalid event_type value returns 400."""
        response = client.get("/api/publications?event_type=invalid")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Task 2.3: GET /api/publications/{id}
# ---------------------------------------------------------------------------

class TestGetPublication:
    """Tests for GET /api/publications/{id}."""

    def test_found_with_authors(self, client):
        """Returns publication with nested authors."""
        with (
            patch("backend.api.get_paper_detail", return_value=SAMPLE_PUB_DETAIL),
            patch("backend.api.get_authors_for_papers", return_value={1: SAMPLE_AUTHORS_PUB1}),
            patch("backend.api.get_coauthors_for_papers", return_value={1: []}),
            patch("backend.api.get_links_for_papers", return_value={1: []}),
        ):
            response = client.get("/api/publications/1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 1
        assert body["title"] == "Trade and Wages"
        assert len(body["authors"]) == 2

    def test_not_found_returns_404(self, client):
        """Non-existent publication returns 404 with error envelope."""
        with patch("backend.api.get_paper_detail", return_value=None):
            response = client.get("/api/publications/999999")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# OpenAlex enrichment fields
# ---------------------------------------------------------------------------

class TestPublicationOpenAlexFields:
    """Tests for OpenAlex enrichment fields in publication responses."""

    def test_feed_includes_doi_and_coauthors(self, client):
        """GET /api/publications returns doi and coauthors."""
        sample_pubs = [
            {**SAMPLE_PUBLICATIONS[0], "doi": "10.1257/aer.20181234"},
        ]
        coauthors_map = {
            1: [
                {"display_name": "Max Steinhardt", "openalex_author_id": "A111"},
                {"display_name": "Jane Doe", "openalex_author_id": "A222"},
            ],
        }
        with (
            patch("backend.api.search_feed_events", return_value=(sample_pubs, 1)),
            patch("backend.api.get_authors_for_papers", return_value=AUTHORS_MAP_PUB1),
            patch("backend.api.get_coauthors_for_papers", return_value=coauthors_map),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/publications")

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["doi"] == "10.1257/aer.20181234"
        assert len(item["coauthors"]) == 2
        assert item["coauthors"][0]["display_name"] == "Max Steinhardt"

    def test_single_publication_includes_doi(self, client):
        """GET /api/publications/{id} returns doi and coauthors."""
        pub_with_doi = {**SAMPLE_PUB_DETAIL, "doi": "10.1257/aer.20181234"}
        coauthors_map = {
            1: [{"display_name": "Max Steinhardt", "openalex_author_id": "A111"}],
        }
        with (
            patch("backend.api.get_paper_detail", return_value=pub_with_doi),
            patch("backend.api.get_authors_for_papers", return_value={1: SAMPLE_AUTHORS_PUB1}),
            patch("backend.api.get_coauthors_for_papers", return_value=coauthors_map),
            patch("backend.api.get_links_for_papers", return_value={1: []}),
        ):
            response = client.get("/api/publications/1")

        assert response.status_code == 200
        body = response.json()
        assert body["doi"] == "10.1257/aer.20181234"
        assert body["coauthors"][0]["display_name"] == "Max Steinhardt"


# ---------------------------------------------------------------------------
# Task 1: GET /api/publications/{id}?include_history=true -- feed_events + extra fields
# ---------------------------------------------------------------------------

SAMPLE_FEED_EVENTS = [
    {"id": 5, "event_type": "status_change", "old_status": "working_paper",
     "new_status": "published", "old_title": None, "new_title": None,
     "created_at": datetime(2026, 3, 20, 12, 0)},
    {"id": 1, "event_type": "new_paper", "old_status": None,
     "new_status": None, "old_title": None, "new_title": None,
     "created_at": datetime(2026, 3, 15, 14, 30)},
]

SAMPLE_SNAPSHOTS = [
    {"status": "published", "venue": "JLE", "abstract": None,
     "draft_url": "https://ssrn.com/abstract=1", "draft_url_status": "valid",
     "year": "2024", "scraped_at": datetime(2026, 3, 20, 12, 0),
     "source_url": "https://example.com/pub"},
]


class TestGetPublicationHistory:
    """Tests for GET /api/publications/{id}?include_history=true."""

    def test_includes_feed_events(self, client):
        """Response includes feed_events when include_history=true."""
        pub_detail = {**SAMPLE_PUB_DETAIL, "is_seed": False,
                      "title_hash": "abc123", "openalex_id": "W12345"}
        with (
            patch("backend.api.get_paper_detail", return_value=pub_detail),
            patch("backend.api.get_authors_for_papers", return_value={1: SAMPLE_AUTHORS_PUB1}),
            patch("backend.api.get_coauthors_for_papers", return_value={1: []}),
            patch("backend.api.get_links_for_papers", return_value={1: []}),
            patch("backend.api.get_paper_snapshots", return_value=SAMPLE_SNAPSHOTS),
            patch("backend.api.get_paper_history", return_value=SAMPLE_FEED_EVENTS),
        ):
            response = client.get("/api/publications/1?include_history=true")

        assert response.status_code == 200
        body = response.json()
        assert "feed_events" in body
        assert len(body["feed_events"]) == 2
        assert body["feed_events"][0]["event_type"] == "status_change"
        assert body["feed_events"][0]["old_status"] == "working_paper"
        assert body["feed_events"][1]["event_type"] == "new_paper"
        assert "history" in body
        assert len(body["history"]) == 1

    def test_includes_extra_fields(self, client):
        """Response includes is_seed, title_hash, openalex_id when include_history=true."""
        pub_detail = {**SAMPLE_PUB_DETAIL, "is_seed": False,
                      "title_hash": "abc123", "openalex_id": "W12345"}
        with (
            patch("backend.api.get_paper_detail", return_value=pub_detail),
            patch("backend.api.get_authors_for_papers", return_value={1: SAMPLE_AUTHORS_PUB1}),
            patch("backend.api.get_coauthors_for_papers", return_value={1: []}),
            patch("backend.api.get_links_for_papers", return_value={1: []}),
            patch("backend.api.get_paper_snapshots", return_value=[]),
            patch("backend.api.get_paper_history", return_value=[]),
        ):
            response = client.get("/api/publications/1?include_history=true")

        assert response.status_code == 200
        body = response.json()
        assert body["is_seed"] is False
        assert body["title_hash"] == "abc123"
        assert body["openalex_id"] == "W12345"

    def test_no_history_without_flag(self, client):
        """feed_events and history are not included without include_history=true."""
        with (
            patch("backend.api.get_paper_detail", return_value=SAMPLE_PUB_DETAIL),
            patch("backend.api.get_authors_for_papers", return_value={1: SAMPLE_AUTHORS_PUB1}),
            patch("backend.api.get_coauthors_for_papers", return_value={1: []}),
            patch("backend.api.get_links_for_papers", return_value={1: []}),
        ):
            response = client.get("/api/publications/1")

        assert response.status_code == 200
        body = response.json()
        assert "feed_events" not in body
        assert "history" not in body
        assert "is_seed" not in body
        assert "title_hash" not in body
        assert "openalex_id" not in body
