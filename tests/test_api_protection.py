"""Anti-scraping protection: trusted client IP, tiers, limits, paging caps, gate."""
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend import api_protection

KEY = "test-internal-key-0123456789abcdef"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/130 Safari/537.36"


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def protection_env(monkeypatch):
    """Set protection env vars, reload settings, and restore them afterwards."""

    def apply(**env):
        for name in (
            "INTERNAL_API_KEY", "API_DIRECT_ACCESS", "API_MAX_PER_PAGE", "API_MAX_RESULT_DEPTH",
            "RATE_LIMIT_DATA", "RATE_LIMIT_DATA_DIRECT", "RATE_LIMIT_DATA_USER",
            "RATE_LIMIT_DATA_LEGACY", "RATE_LIMIT_SSR",
        ):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        api_protection.reload_settings()

    yield apply
    monkeypatch.undo()
    api_protection.reload_settings()


@pytest.fixture
def client():
    with (
        patch("backend.api.create_tables"),
        patch("backend.api.start_scheduler"),
        patch("backend.api.shutdown_scheduler"),
        patch("backend.api.connection_scope", _noop_connection_scope),
        patch("backend.api.search_feed_events", return_value=([], 0, 0)) as feed,
        patch("backend.api.get_authors_for_papers", return_value={}),
        patch("backend.api.get_coauthors_for_papers", return_value={}),
        patch("backend.api.get_links_for_papers", return_value={}),
    ):
        from backend.api import app

        with TestClient(app, headers={"User-Agent": BROWSER_UA}) as c:
            c.feed_mock = feed
            yield c


def _resolve(headers: dict, peer: str = "172.18.0.1"):
    """Build a bare request and classify it."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/publications",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 12345),
    }
    return api_protection.resolve_client(Request(scope))


def _user_token(sub="google-123"):
    from backend.auth import NEXTAUTH_SECRET

    return jwt.encode({"sub": sub, "email": "u@example.com"}, NEXTAUTH_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Client IP resolution
# ---------------------------------------------------------------------------

class TestClientIp:
    def test_rightmost_forwarded_for_is_used_behind_local_proxy(self, protection_env):
        protection_env()
        info = _resolve({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
        assert info.ip == "203.0.113.9"

    def test_forwarded_for_ignored_from_public_peer(self, protection_env):
        protection_env()
        info = _resolve({"X-Forwarded-For": "6.6.6.6"}, peer="8.8.4.4")
        assert info.ip == "8.8.4.4"

    def test_real_ip_header_is_never_trusted(self, protection_env):
        protection_env()
        info = _resolve({"X-Real-IP": "6.6.6.6", "X-Forwarded-For": "203.0.113.9"})
        assert info.ip == "203.0.113.9"

    def test_spoofed_visitor_ip_without_key_is_ignored(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY)
        info = _resolve({"X-Forwarded-For": "203.0.113.9", "X-Visitor-IP": "6.6.6.6"})
        assert info.ip == "203.0.113.9"
        assert info.tier == api_protection.LEGACY  # API_DIRECT_ACCESS=open default

    def test_spoofed_visitor_ip_with_wrong_key_is_ignored(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, API_DIRECT_ACCESS="limited")
        info = _resolve({
            "X-Forwarded-For": "203.0.113.9", "X-Visitor-IP": "6.6.6.6", "X-Internal-Api-Key": "nope",
        })
        assert (info.tier, info.ip) == (api_protection.DIRECT, "203.0.113.9")

    def test_visitor_ip_trusted_with_valid_key(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY)
        info = _resolve({
            "X-Forwarded-For": "76.76.21.21", "X-Visitor-IP": "198.51.100.23", "X-Internal-Api-Key": KEY,
        })
        assert (info.tier, info.ip) == (api_protection.VISITOR, "198.51.100.23")

    def test_ssr_source(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY)
        info = _resolve({
            "X-Visitor-IP": "198.51.100.23", "X-Visitor-Source": "ssr", "X-Internal-Api-Key": KEY,
        })
        assert (info.tier, info.ip) == (api_protection.SSR, "198.51.100.23")

    def test_key_without_visitor_is_internal(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY)
        assert _resolve({"X-Internal-Api-Key": KEY}).tier == api_protection.INTERNAL

    def test_unparseable_visitor_ip_shares_unknown_bucket(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY)
        info = _resolve({"X-Visitor-IP": "not-an-ip", "X-Internal-Api-Key": KEY})
        assert (info.tier, info.ip) == (api_protection.VISITOR, "unknown")

    def test_key_header_ignored_when_backend_has_no_key(self, protection_env):
        protection_env()
        info = _resolve({"X-Visitor-IP": "6.6.6.6", "X-Internal-Api-Key": KEY, "X-Forwarded-For": "76.76.21.21"})
        assert (info.tier, info.ip) == (api_protection.LEGACY, "76.76.21.21")

    def test_wrong_key_is_logged_only_when_backend_has_a_key(self, protection_env, caplog):
        protection_env()
        api_protection._warned.clear()
        with caplog.at_level("WARNING"):
            _resolve({"X-Internal-Api-Key": KEY})
        assert not any("internal_key_invalid" in r.getMessage() for r in caplog.records)
        protection_env(INTERNAL_API_KEY=KEY)
        with caplog.at_level("WARNING"):
            _resolve({"X-Internal-Api-Key": "wrong-key-of-some-length"})
        assert any("internal_key_invalid" in r.getMessage() for r in caplog.records)

    def test_short_backend_key_is_ignored(self, protection_env):
        protection_env(INTERNAL_API_KEY="short")
        info = _resolve({"X-Internal-Api-Key": "short", "X-Visitor-IP": "6.6.6.6", "X-Forwarded-For": "203.0.113.9"})
        assert (info.tier, info.ip) == (api_protection.LEGACY, "203.0.113.9")

    def test_direct_limited(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, API_DIRECT_ACCESS="limited")
        assert _resolve({"X-Forwarded-For": "203.0.113.9"}).tier == api_protection.DIRECT

    def test_signed_in_user(self, protection_env):
        protection_env(INTERNAL_API_KEY=KEY)
        info = _resolve({"Authorization": f"Bearer {_user_token()}", "X-Forwarded-For": "203.0.113.9"})
        assert (info.tier, info.ident) == (api_protection.USER, "google-123")

    def test_invalid_jwt_is_anonymous(self, protection_env):
        protection_env()
        info = _resolve({"Authorization": "Bearer garbage", "X-Forwarded-For": "203.0.113.9"})
        assert info.tier == api_protection.LEGACY


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------

class TestRateLimits:
    def test_429_with_retry_after_and_log(self, client, protection_env, caplog):
        protection_env(RATE_LIMIT_DATA_LEGACY="2/minute")
        headers = {"X-Forwarded-For": "203.0.113.9"}
        assert client.get("/api/publications", headers=headers).status_code == 200
        assert client.get("/api/publications", headers=headers).status_code == 200
        with caplog.at_level("WARNING"):
            resp = client.get("/api/publications", headers=headers)
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "rate_limit_exceeded"
        assert 1 <= int(resp.headers["Retry-After"]) <= 60
        assert any(
            "rate_limited ip=203.0.113.9" in r.getMessage() and "path=/api/publications" in r.getMessage()
            for r in caplog.records
        )

    def test_data_budget_is_shared_across_endpoints(self, client, protection_env):
        protection_env(RATE_LIMIT_DATA_LEGACY="2/minute")
        headers = {"X-Forwarded-For": "203.0.113.9"}
        with patch("backend.api.search_researchers", return_value=([], 0)), \
             patch("backend.api.get_urls_for_researchers", return_value={}), \
             patch("backend.api.get_pub_counts_for_researchers", return_value={}), \
             patch("backend.api.get_fields_for_researchers", return_value={}), \
             patch("backend.api.get_jel_codes_for_researchers", return_value={}):
            assert client.get("/api/publications", headers=headers).status_code == 200
            assert client.get("/api/researchers", headers=headers).status_code == 200
            assert client.get("/api/researchers", headers=headers).status_code == 429

    def test_visitors_behind_frontend_have_separate_budgets(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, RATE_LIMIT_DATA="1/minute")
        vercel = {"X-Forwarded-For": "76.76.21.21", "X-Internal-Api-Key": KEY}
        a = {**vercel, "X-Visitor-IP": "198.51.100.1"}
        b = {**vercel, "X-Visitor-IP": "198.51.100.2"}
        assert client.get("/api/publications", headers=a).status_code == 200
        assert client.get("/api/publications", headers=b).status_code == 200
        assert client.get("/api/publications", headers=a).status_code == 429

    def test_spoofed_visitor_ips_share_the_real_ip_budget(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, RATE_LIMIT_DATA_LEGACY="1/minute")
        base = {"X-Forwarded-For": "203.0.113.9"}
        assert client.get("/api/publications", headers={**base, "X-Visitor-IP": "1.1.1.1"}).status_code == 200
        assert client.get("/api/publications", headers={**base, "X-Visitor-IP": "2.2.2.2"}).status_code == 429

    def test_key_only_on_backend_keeps_the_site_on_fail_open_limits(self, client, protection_env):
        # Frontend without the key: every visitor arrives from one Vercel IP.
        protection_env(INTERNAL_API_KEY=KEY, RATE_LIMIT_DATA="1/minute", RATE_LIMIT_DATA_LEGACY="3/minute")
        vercel = {"X-Forwarded-For": "76.76.21.21"}
        codes = [client.get("/api/publications", headers=vercel).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]

    def test_direct_limited_is_stricter(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, API_DIRECT_ACCESS="limited",
                       RATE_LIMIT_DATA="5/minute", RATE_LIMIT_DATA_DIRECT="1/minute")
        direct = {"X-Forwarded-For": "203.0.113.9"}
        assert client.get("/api/publications", headers=direct).status_code == 200
        assert client.get("/api/publications", headers=direct).status_code == 429

    def test_internal_requests_are_exempt(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, RATE_LIMIT_DATA="1/minute")
        for _ in range(3):
            assert client.get("/api/publications", headers={"X-Internal-Api-Key": KEY}).status_code == 200

    def test_signed_in_users_get_their_own_limit(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, RATE_LIMIT_DATA="1/minute", RATE_LIMIT_DATA_USER="3/minute")
        auth = {"Authorization": f"Bearer {_user_token()}", "X-Forwarded-For": "203.0.113.9"}
        codes = [client.get("/api/publications", headers=auth).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]


# ---------------------------------------------------------------------------
# Paging caps
# ---------------------------------------------------------------------------

class TestPaging:
    def test_per_page_is_clamped_for_anonymous(self, client, protection_env):
        protection_env(API_MAX_PER_PAGE="50")
        client.feed_mock.return_value = ([], 500, 0)
        resp = client.get("/api/publications?per_page=100")
        assert resp.status_code == 200
        body = resp.json()
        assert body["per_page"] == 50
        assert body["pages"] == 10
        assert client.feed_mock.call_args.kwargs["limit"] == 50

    def test_pages_capped_at_depth(self, client, protection_env):
        protection_env(API_MAX_RESULT_DEPTH="100")
        client.feed_mock.return_value = ([], 9000, 0)
        body = client.get("/api/publications?per_page=20").json()
        assert body["pages"] == 5

    def test_deep_page_rejected(self, client, protection_env):
        protection_env(API_MAX_RESULT_DEPTH="100")
        resp = client.get("/api/publications?per_page=20&page=6")
        assert resp.status_code == 400
        assert "first 100 items" in resp.json()["error"]["message"]
        assert client.get("/api/publications?per_page=20&page=5").status_code == 200

    def test_signed_in_user_is_uncapped(self, client, protection_env):
        protection_env(API_MAX_PER_PAGE="50", API_MAX_RESULT_DEPTH="100")
        client.feed_mock.return_value = ([], 9000, 0)
        auth = {"Authorization": f"Bearer {_user_token()}"}
        resp = client.get("/api/publications?per_page=100&page=20", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["per_page"] == 100
        assert resp.json()["pages"] == 90
        assert client.feed_mock.call_args.kwargs["offset"] == 1900

    def test_researchers_list_clamped(self, client, protection_env):
        protection_env(API_MAX_PER_PAGE="50", API_MAX_RESULT_DEPTH="2000")
        with patch("backend.api.search_researchers", return_value=([], 58040)) as search, \
             patch("backend.api.get_urls_for_researchers", return_value={}), \
             patch("backend.api.get_pub_counts_for_researchers", return_value={}), \
             patch("backend.api.get_fields_for_researchers", return_value={}), \
             patch("backend.api.get_jel_codes_for_researchers", return_value={}):
            body = client.get("/api/researchers?per_page=100&page=2").json()
            assert search.call_args.kwargs["limit"] == 50
            assert search.call_args.kwargs["offset"] == 50
        assert body["per_page"] == 50
        assert body["pages"] == 40
        assert client.get("/api/researchers?per_page=50&page=41").status_code == 400


# ---------------------------------------------------------------------------
# Gate: User-Agent and direct-access policy; public endpoints keep working
# ---------------------------------------------------------------------------

class TestGate:
    @pytest.mark.parametrize("ua", ["", "curl/8.4.0", "python-requests/2.32", "Scrapy/2.11", "GPTBot/1.2"])
    def test_scraper_user_agents_blocked(self, client, protection_env, ua):
        protection_env()
        resp = client.get("/api/publications", headers={"User-Agent": ua})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    @pytest.mark.parametrize("ua", [
        BROWSER_UA,
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "node",
        "econ-newsfeed-monitor",
    ])
    def test_browsers_crawlers_and_frontend_allowed(self, client, protection_env, ua):
        protection_env()
        assert client.get("/api/publications", headers={"User-Agent": ua}).status_code == 200

    def test_internal_key_bypasses_user_agent_check(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY)
        resp = client.get("/api/publications", headers={"User-Agent": "", "X-Internal-Api-Key": KEY})
        assert resp.status_code == 200

    def test_health_feed_and_robots_not_gated(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, API_DIRECT_ACCESS="deny")
        curl = {"User-Agent": "curl/8.4.0"}
        assert client.get("/api/health", headers=curl).status_code == 200
        feed = client.get("/api/feed.xml", headers=curl)
        assert feed.status_code == 200
        assert feed.headers["content-type"].startswith("application/atom+xml")
        robots = client.get("/robots.txt", headers=curl)
        assert robots.status_code == 200
        assert "Disallow: /" in robots.text

    def test_direct_deny_blocks_data_but_not_frontend(self, client, protection_env):
        protection_env(INTERNAL_API_KEY=KEY, API_DIRECT_ACCESS="deny")
        assert client.get("/api/publications").status_code == 403
        via_frontend = {"X-Internal-Api-Key": KEY, "X-Visitor-IP": "198.51.100.1"}
        assert client.get("/api/publications", headers=via_frontend).status_code == 200

    def test_feed_xml_has_its_own_limit(self, client, protection_env):
        protection_env(RATE_LIMIT_DATA_LEGACY="1/minute")
        headers = {"X-Forwarded-For": "203.0.113.9"}
        assert client.get("/api/publications", headers=headers).status_code == 200
        assert client.get("/api/publications", headers=headers).status_code == 429
        assert client.get("/api/feed.xml", headers=headers).status_code == 200
