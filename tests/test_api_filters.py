"""Tests for new v2 query-parameter filters on GET /api/publications.

Covers:
- ?institution=  (subquery filter on author affiliation)
- ?status=working_paper  (new status accepted in v2)
- ?preset=top20  (top-20 economics department filter)
- Invalid status returns 400
- abstract and draft_url_status appear in response items
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Test client with mocked DB and scheduler (same pattern as existing tests)."""
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
    ):
        from api import app

        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# Sample data
#
# Row shape (10 columns) matches the v2 SELECT in list_publications:
#   id, title, year, venue, url, timestamp, status, draft_url, abstract, draft_url_status
# ---------------------------------------------------------------------------

_PUB_WITH_ABSTRACT = (
    1,
    "Trade and Wages",
    "2024",
    "JLE",
    "https://example.com/pub1",
    datetime(2026, 3, 15, 14, 30),
    "working_paper",
    "https://ssrn.com/abstract=1",
    "This paper examines trade and wages.",
    "valid",
)

_PUB_NO_ABSTRACT = (
    2,
    "Immigration Effects",
    "2023",
    "QJE",
    "https://example.com/pub2",
    datetime(2026, 3, 14, 10, 0),
    "published",
    None,
    None,
    "unchecked",
)

_PUB_MIT = (
    3,
    "Labor Markets and MIT",
    "2024",
    "AER",
    "https://example.com/pub3",
    datetime(2026, 3, 13, 9, 0),
    "accepted",
    None,
    None,
    None,
)

# Batch author rows: (publication_id, researcher_id, first_name, last_name)
_AUTHORS_PUB1 = [(1, 10, "Jane", "Doe")]
_AUTHORS_PUB2 = [(2, 11, "John", "Smith")]
_AUTHORS_PUB3 = [(3, 12, "Alice", "Brown")]
_NO_AUTHORS = []


# ---------------------------------------------------------------------------
# Helper — mock a successful single-publication list response
# ---------------------------------------------------------------------------

def _mock_single_pub(mock_fetch, pub_row, author_rows):
    """Configure mock_fetch for a two-call sequence: pubs + batch-authors."""
    mock_fetch.side_effect = [
        [pub_row],
        author_rows,
    ]


# ---------------------------------------------------------------------------
# ?status= filter
# ---------------------------------------------------------------------------

class TestStatusFilter:
    """?status= validation and working_paper support."""

    def test_working_paper_status_returns_200(self, client):
        """'working_paper' is a valid v2 status; must not return 400."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_WITH_ABSTRACT, _AUTHORS_PUB1)
            response = client.get("/api/publications?status=working_paper")

        assert response.status_code == 200

    def test_working_paper_status_included_in_items(self, client):
        """Items filtered by working_paper should carry status='working_paper'."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_WITH_ABSTRACT, _AUTHORS_PUB1)
            response = client.get("/api/publications?status=working_paper")

        item = response.json()["items"][0]
        assert item["status"] == "working_paper"

    def test_published_status_still_accepted(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_NO_ABSTRACT, _AUTHORS_PUB2)
            response = client.get("/api/publications?status=published")

        assert response.status_code == 200

    def test_accepted_status_accepted(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?status=accepted")

        assert response.status_code == 200

    def test_revise_and_resubmit_status_accepted(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(0,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            # Zero results: first call returns empty pub list; second call (batch authors) never reached
            mock_fetch.side_effect = [[], []]
            response = client.get("/api/publications?status=revise_and_resubmit")

        assert response.status_code == 200

    def test_reject_and_resubmit_status_accepted(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(0,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            response = client.get("/api/publications?status=reject_and_resubmit")

        assert response.status_code == 200

    def test_invalid_status_returns_400(self, client):
        """An unrecognised status must be rejected with 400 and an error envelope."""
        response = client.get("/api/publications?status=under_review")

        assert response.status_code == 400
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "bad_request"

    def test_invalid_status_error_message_mentions_valid_values(self, client):
        response = client.get("/api/publications?status=bogus")
        body = response.json()
        msg = body["error"]["message"]
        # At minimum the message should contain some hint about valid choices
        assert "status" in msg.lower() or "working_paper" in msg.lower() or "invalid" in msg.lower()


# ---------------------------------------------------------------------------
# ?institution= filter
# ---------------------------------------------------------------------------

class TestInstitutionFilter:
    """?institution= builds a subquery against authors' affiliations."""

    def test_institution_filter_returns_200(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?institution=MIT")

        assert response.status_code == 200

    def test_institution_filter_returns_items(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?institution=MIT")

        assert len(response.json()["items"]) == 1

    def test_institution_filter_no_results_returns_empty_list(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(0,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            response = client.get("/api/publications?institution=Nonexistent+University")

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_institution_filter_combined_with_year(self, client):
        """institution and year can be combined; must return 200."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?institution=MIT&year=2024")

        assert response.status_code == 200

    def test_institution_filter_escapes_percent(self, client):
        """A literal % in the institution name must not break the LIKE query."""
        with (
            patch("api.Database.fetch_one", return_value=(0,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            response = client.get("/api/publications?institution=100%25MIT")

        assert response.status_code == 200

    def test_institution_filter_combined_with_status(self, client):
        """institution and status can be combined."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?institution=MIT&status=accepted")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# ?preset=top20 filter
# ---------------------------------------------------------------------------

class TestPresetTop20Filter:
    """?preset=top20 filters publications whose authors belong to top-20 departments."""

    def test_preset_top20_returns_200(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?preset=top20")

        assert response.status_code == 200

    def test_preset_top20_returns_items(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?preset=top20")

        body = response.json()
        assert "items" in body
        assert body["total"] == 1

    def test_preset_top20_no_results_returns_empty(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(0,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [[], []]
            response = client.get("/api/publications?preset=top20")

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_preset_top20_combined_with_year(self, client):
        """preset=top20 and year can be used together."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_MIT, _AUTHORS_PUB3)
            response = client.get("/api/publications?preset=top20&year=2024")

        assert response.status_code == 200

    def test_unknown_preset_returns_400(self, client):
        """An unrecognised preset value must be rejected with 400 and an error envelope."""
        response = client.get("/api/publications?preset=unknown_preset")

        assert response.status_code == 400
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "bad_request"


# ---------------------------------------------------------------------------
# Response shape — abstract and draft_url_status fields
# ---------------------------------------------------------------------------

class TestResponseShape:
    """Items must include abstract and draft_url_status from the v2 schema."""

    def test_item_includes_abstract_field(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_WITH_ABSTRACT, _AUTHORS_PUB1)
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert "abstract" in item
        assert item["abstract"] == "This paper examines trade and wages."

    def test_item_includes_draft_url_status_field(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_WITH_ABSTRACT, _AUTHORS_PUB1)
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert "draft_url_status" in item
        assert item["draft_url_status"] == "valid"

    def test_draft_available_true_when_status_is_valid(self, client):
        """draft_available must be True only when draft_url_status == 'valid'."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_WITH_ABSTRACT, _AUTHORS_PUB1)
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert item["draft_available"] is True

    def test_draft_available_false_when_status_is_unchecked(self, client):
        """draft_available must be False when draft_url_status is not 'valid'."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_NO_ABSTRACT, _AUTHORS_PUB2)
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert item["draft_available"] is False

    def test_abstract_is_none_when_not_present(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            _mock_single_pub(mock_fetch, _PUB_NO_ABSTRACT, _AUTHORS_PUB2)
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert item["abstract"] is None
