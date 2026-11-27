"""Tests for the sitemap data endpoints and the internal-render rate-limit exemption."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def client():
    with (
        patch("backend.api.create_tables"),
        patch("backend.api.start_scheduler"),
        patch("backend.api.shutdown_scheduler"),
        patch("backend.api.connection_scope", _noop_connection_scope),
    ):
        import backend.api as api_mod

        api_mod._sitemap_cache.clear()
        api_mod.limiter.reset()
        with TestClient(api_mod.app) as c:
            yield c
        api_mod._sitemap_cache.clear()
        api_mod.limiter.reset()


class TestSitemapIndex:
    def test_reports_counts_and_chunk_pages(self, client):
        with (
            patch("backend.api.SITEMAP_PAGE_SIZE", 10_000),
            patch.dict("backend.api._SITEMAP_KINDS", {
                "researchers": (lambda: 11_042, None),
                "papers": (lambda: 20_000, None),
            }),
        ):
            response = client.get("/api/sitemap")

        assert response.status_code == 200
        assert response.json() == {
            "page_size": 10_000,
            "researchers": {"count": 11_042, "pages": 2},
            "papers": {"count": 20_000, "pages": 2},
        }
        assert "max-age=3600" in response.headers["cache-control"]

    def test_empty_database_has_no_chunks(self, client):
        with patch.dict("backend.api._SITEMAP_KINDS", {
            "researchers": (lambda: 0, None),
            "papers": (lambda: 0, None),
        }):
            body = client.get("/api/sitemap").json()

        assert body["researchers"] == {"count": 0, "pages": 0}
        assert body["papers"] == {"count": 0, "pages": 0}


class TestSitemapPage:
    def test_returns_ids_with_w3c_lastmod(self, client):
        rows = [
            {"id": 1, "lastmod": "2026-06-10 07:42:05"},  # GREATEST() can return a string
            {"id": 2, "lastmod": datetime(2026, 4, 9, 9, 15, 56, 123456)},
            {"id": 3, "lastmod": None},
        ]
        calls = []

        def fake_fetch(offset, limit):
            calls.append((offset, limit))
            return rows

        with patch.dict("backend.api._SITEMAP_KINDS", {"researchers": (None, fake_fetch)}):
            response = client.get("/api/sitemap/researchers?page=2")

        assert response.status_code == 200
        assert response.json() == {"items": [
            {"id": 1, "lastmod": "2026-06-10T07:42:05Z"},
            {"id": 2, "lastmod": "2026-04-09T09:15:56Z"},
            {"id": 3, "lastmod": None},
        ]}
        import backend.api as api_mod
        assert calls == [(2 * api_mod.SITEMAP_PAGE_SIZE, api_mod.SITEMAP_PAGE_SIZE)]

    def test_unknown_kind_is_404(self, client):
        assert client.get("/api/sitemap/users").status_code == 404

    def test_negative_page_rejected(self, client):
        assert client.get("/api/sitemap/papers?page=-1").status_code in (400, 422)


class TestSitemapLastmod:
    def test_formats(self):
        from backend.api import _sitemap_lastmod

        assert _sitemap_lastmod(None) is None
        assert _sitemap_lastmod("") is None
        assert _sitemap_lastmod("garbage") is None
        assert _sitemap_lastmod("2026-01-02 03:04:05") == "2026-01-02T03:04:05Z"
        aware = datetime(2026, 1, 2, 5, 4, 5, tzinfo=timezone(timedelta(hours=2)))
        assert _sitemap_lastmod(aware) == "2026-01-02T03:04:05Z"


class TestInternalRenderExemption:
    KEY = "internal-key-for-tests-0123456789"

    def _hit(self, client, n, headers=None):
        with patch.dict("backend.api._SITEMAP_KINDS", {
            "researchers": (lambda: 0, None),
            "papers": (lambda: 0, None),
        }):
            return [client.get("/api/sitemap", headers=headers or {}).status_code for _ in range(n)]

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        from backend import api_protection

        monkeypatch.setenv("INTERNAL_API_KEY", self.KEY)
        api_protection.reload_settings()
        yield
        monkeypatch.delenv("INTERNAL_API_KEY")
        api_protection.reload_settings()

    def test_public_requests_are_rate_limited(self, client):
        codes = self._hit(client, 31)
        assert codes[-1] == 429

    def test_frontend_server_with_key_is_exempt(self, client):
        codes = self._hit(client, 35, {"X-Internal-Api-Key": self.KEY})
        assert set(codes) == {200}

    def test_wrong_key_is_not_exempt(self, client):
        codes = self._hit(client, 31, {"X-Internal-Api-Key": "nope-nope-nope-nope"})
        assert codes[-1] == 429


class TestResearcherDetailOpenAlex:
    def test_detail_includes_openalex_author_id(self, client):
        import backend.api as api_mod

        api_mod._researcher_detail_cache.clear()
        detail = {"id": 77, "first_name": "Ada", "last_name": "Lovelace", "position": None,
                  "affiliation": None, "description": None, "openalex_author_id": "A5085299813"}
        with (
            patch("backend.api.get_researcher_detail", return_value=detail),
            patch("backend.api.get_urls_for_researchers", return_value={}),
            patch("backend.api.get_pub_counts_for_researchers", return_value={}),
            patch("backend.api.get_fields_for_researchers", return_value={}),
            patch("backend.api.get_jel_codes_for_researcher", return_value=[]),
            patch("backend.api.get_researcher_papers", return_value=[]),
            patch("backend.api.get_authors_for_papers", return_value={}),
            patch("backend.api.get_coauthors_for_papers", return_value={}),
            patch("backend.api.get_links_for_papers", return_value={}),
        ):
            body = client.get("/api/researchers/77").json()
        api_mod._researcher_detail_cache.clear()
        assert body["openalex_author_id"] == "A5085299813"


def test_api_responses_are_noindex(client):
    response = client.get("/api/sitemap/users")
    assert response.headers["x-robots-tag"] == "noindex"
