"""Network-free regressions for bounded discovery HTTP and directory ownership."""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
import requests

from backend.discovery import mining_sources as sources


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Response:
    def __init__(self, status=200, body="<html>Person</html>", ctype="text/html", location=None, chunks=None, length=None):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        if location is not None:
            self.headers["Location"] = location
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.encoding = "utf-8"
        self.chunks = chunks if chunks is not None else [body.encode()]
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self.chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


@pytest.fixture
def network(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sources.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(sources.time, "sleep", clock.sleep)
    sources._HOST_LAST.clear()
    calls, pins, replies = [], [], {}
    validate = MagicMock(return_value=(True, "93.184.216.34"))
    monkeypatch.setattr(sources.HTMLFetcher, "validate_url_with_pin", validate)

    @contextmanager
    def pin(host, address):
        pins.append((host, address))
        yield

    monkeypatch.setattr(sources, "_pin_dns", pin)

    class Session:
        def __init__(self):
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            assert self.trust_env is False
            assert kwargs["allow_redirects"] is False
            assert kwargs["stream"] is True
            calls.append((url, clock.now, kwargs))
            reply = replies[url]
            if isinstance(reply, list):
                reply = reply.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

    monkeypatch.setattr(sources.requests, "Session", Session)
    return {"clock": clock, "calls": calls, "pins": pins, "replies": replies, "validate": validate}


def allow(network, host="example.org"):
    network["replies"][f"https://{host}/robots.txt"] = Response(404)


def test_fetch_pins_robots_and_html_counts_requests_and_spaces_host(network):
    allow(network)
    network["replies"]["https://example.org/alice"] = Response()
    fetcher = sources.MiningFetcher()
    result = fetcher.fetch("https://example.org/alice#research")
    assert result == {"url": "https://example.org/alice", "status": 200, "html": "<html>Person</html>", "content_type": "text/html"}
    assert fetcher.requests == 2
    assert len(network["pins"]) == 2
    assert network["calls"][1][1] - network["calls"][0][1] >= 1
    assert network["validate"].call_count == 2


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:password@example.org/", "https://example.org:9000/", "https://example.org\\@localhost/", "http://example.org/\nprivate"])
def test_rejects_malformed_urls_before_network(network, url):
    result = sources.MiningFetcher().fetch(url)
    assert result["error"] == "unsafe_url"
    assert not network["calls"]


@pytest.mark.parametrize("address", ["100.64.0.1", "224.0.0.1", "::1", "ff02::1"])
def test_rejects_nonpublic_dns_even_if_existing_validator_accepts(network, address):
    network["validate"].return_value = (True, address)
    assert sources.MiningFetcher().fetch("https://example.org/")["error"] == "unsafe_or_unresolved_url"
    assert not network["calls"]


def test_checks_destination_robots_for_page_redirect(network):
    allow(network)
    network["replies"].update({
        "https://example.org/alice": Response(302, location="https://other.org/research"),
        "https://other.org/robots.txt": Response(body="User-agent: *\nDisallow: /research", ctype="text/plain"),
    })
    fetcher = sources.MiningFetcher()
    result = fetcher.fetch("https://example.org/alice")
    assert result["url"] == "https://other.org/research"
    assert result["error"] == "robots_disallowed"
    assert fetcher.requests == 3
    assert all(url != "https://other.org/research" for url, *_ in network["calls"])


def test_robots_redirect_is_validated_and_pinned(network):
    network["replies"]["https://example.org/robots.txt"] = Response(302, location="http://127.0.0.1/robots.txt")
    network["validate"].side_effect = [(True, "93.184.216.34"), (False, None)]
    result = sources.MiningFetcher().fetch("https://example.org/alice")
    assert result["error"] == "unsafe_or_unresolved_url"
    assert len(network["calls"]) == 1


def test_successful_redirect_retains_final_url(network):
    allow(network)
    allow(network, "other.org")
    network["replies"].update({
        "https://example.org/alice": Response(301, location="https://other.org/alice"),
        "https://other.org/alice": Response(),
    })
    result = sources.MiningFetcher().fetch("https://example.org/alice")
    assert result["url"] == "https://other.org/alice"
    assert [host for host, _ in network["pins"]] == ["example.org", "example.org", "other.org", "other.org"]


@pytest.mark.parametrize("status", [401, 403])
def test_robots_auth_denial_blocks_page(network, status):
    network["replies"]["https://example.org/robots.txt"] = Response(status)
    fetcher = sources.MiningFetcher()
    assert fetcher.fetch("https://example.org/alice")["error"] == "robots_disallowed"
    assert fetcher.requests == 1


@pytest.mark.parametrize("failure", [Response(503), requests.Timeout("slow")])
def test_transient_robots_failure_retried_then_fails_closed_without_cache(network, failure):
    network["replies"]["https://example.org/robots.txt"] = [failure, failure, Response(404)]
    network["replies"]["https://example.org/alice"] = Response()
    fetcher = sources.MiningFetcher()
    assert "error" in fetcher.fetch("https://example.org/alice")
    assert fetcher.requests == 2
    assert fetcher.fetch("https://example.org/alice")["status"] == 200
    assert fetcher.requests == 4


def test_html_challenge_at_robots_is_not_an_allow_rule(network):
    network["replies"]["https://example.org/robots.txt"] = Response(body="<!DOCTYPE html><html>Verify you are human</html>")
    assert sources.MiningFetcher().fetch("https://example.org/alice")["error"] == "robots_invalid_html"
    assert len(network["calls"]) == 1


def test_respects_crawl_delay_and_robot_policy_cache(network):
    network["replies"]["https://example.org/robots.txt"] = Response(body="User-agent: *\nCrawl-delay: 3\nAllow: /", ctype="text/plain")
    network["replies"]["https://example.org/alice"] = Response()
    fetcher = sources.MiningFetcher()
    fetcher.fetch("https://example.org/alice")
    fetcher.fetch("https://example.org/alice")
    assert fetcher.requests == 3
    assert all(later[1] - earlier[1] >= 3 for earlier, later in zip(network["calls"], network["calls"][1:]))


def test_request_budget_includes_robots_and_stops_before_page(network):
    allow(network)
    fetcher = sources.MiningFetcher(max_requests=1)
    with pytest.raises(sources.BudgetExceeded):
        fetcher.fetch("https://example.org/alice")
    assert fetcher.requests == 1 and fetcher.budget_exhausted
    assert len(network["calls"]) == 1


def test_time_budget_stops_before_host_delay(network):
    allow(network)
    fetcher = sources.MiningFetcher(max_seconds=0.5)
    with pytest.raises(sources.BudgetExceeded):
        fetcher.fetch("https://example.org/alice")
    assert fetcher.budget_exhausted
    assert len(network["calls"]) == 1


@pytest.mark.parametrize("length,chunks", [(sources.MAX_BYTES + 1, [b"small"]), (None, [b"x" * sources.MAX_BYTES, b"x"])])
def test_caps_declared_and_streamed_response_sizes(network, length, chunks):
    allow(network)
    response = Response(chunks=chunks, length=length)
    network["replies"]["https://example.org/alice"] = response
    result = sources.MiningFetcher().fetch("https://example.org/alice")
    assert result["error"] == "response_too_large"
    assert response.closed


def test_rejects_pdf_payload_even_if_url_is_extensionless(network):
    allow(network)
    network["replies"]["https://example.org/alice"] = Response(body="%PDF", ctype="application/pdf")
    result = sources.MiningFetcher().fetch("https://example.org/alice")
    assert result["error"] == "not_html" and result["html"] == ""


def test_redirect_limit(network):
    allow(network)
    for number in range(6):
        network["replies"][f"https://example.org/{number}"] = Response(302, location=f"/{number + 1}")
    fetcher = sources.MiningFetcher()
    result = fetcher.fetch("https://example.org/0")
    assert result["error"] == "redirect_error"
    assert fetcher.requests == 7


@pytest.mark.parametrize("department,html,kind,name,target", [
    ("Stanford", '<div class="hb-card"><h3>Alice Smith</h3><a href="https://alice.example/">Website</a></div>', "website", "Alice Smith", "https://alice.example/"),
    ("Princeton", '<div class="person"><h3>Alice Smith</h3><a class="site" href="https://alice.example/">Personal website</a></div>', "website", "Alice Smith", "https://alice.example/"),
    ("Oxford", '<article><h3>Alice Smith</h3><a href="https://alice.example/">Personal website</a></article>', "website", "Alice Smith", "https://alice.example/"),
    ("Toulouse", '<article><h3>Alice Smith</h3><a href="https://alice.example/">Personal website</a><p>Supervisor <a href="https://wrong.example/">Website</a></p></article>', "website", "Alice Smith", "https://alice.example/"),
    ("MIT", '<a class="profile-teaser" href="/people/faculty/alice"><h3>Alice Smith</h3></a>', "profile", "Alice Smith", "https://economics.mit.edu/people/faculty/alice"),
    ("Berkeley", '<a href="/profile/alice">Smith, Alice</a>', "profile", "Alice Smith", "https://econ.berkeley.edu/profile/alice"),
    ("LSE", '<a href="/economics/people/alice">Dr. Alice Smith</a>', "profile", "Alice Smith", "https://www.lse.ac.uk/economics/people/alice"),
    ("Bonn", '<div class="table-row content-row"><a href="/bad">Supervisor</a><span class="fullname"><a href="/alice">Prof. Alice Smith</a></span></div>', "profile", "Alice Smith", "https://www.econ.uni-bonn.de/alice"),
])
def test_directory_adapters_bind_named_person_to_link(department, html, kind, name, target):
    seed = next(seed for seed in sources.DIRECTORY_SEEDS if seed["name"] == department)
    assert sources.directory_links(seed, html, seed["url"]) == [{"name": name, "url": target, "kind": kind}]


def test_directory_refuses_unknown_adapter_generic_card_or_mismatched_host():
    html = '<div class="hb-card"><h3>Department Website</h3><a href="https://wrong.example/">Website</a></div>'
    assert sources.directory_links("Stanford", html, sources.DIRECTORY_SEEDS[0]["url"]) == []
    assert sources.directory_links("Unknown", html, "https://unknown.example/") == []
    assert sources.directory_links("Stanford", html, "https://wrong.example/") == []


def test_profile_requires_person_heading_and_explicit_personal_link():
    html = '<main><h1>Jane Doe</h1><a href="https://jane.example/">Personal Website</a><a href="https://unknown.example/">Click here</a><a href="https://scholar.google.com/jane">Website</a><a href="/cv.pdf">Homepage</a></main>'
    result = sources.profile_websites("Jane Doe", html, "https://university.example/jane")
    assert [row["url"] for row in result] == ["https://jane.example/"]
    assert result[0]["source_kind"] == "directory"
    assert result[0]["source_url"] == "https://university.example/jane"
    assert result[0]["evidence"]["explicit_personal_link"] is True
    assert sources.profile_websites("Other Person", html, "https://university.example/jane") == []


def test_profile_rejects_supervisor_and_related_person_websites():
    html = '''<main><h1>Jane Doe</h1>
      <p>Supervisor: <a href="https://wrong.example/">Website</a></p>
      <section><h3>John Smith</h3><a href="https://john.example/">Website</a></section>
      <div class="related-person"><h3>James Smith</h3><a href="https://james.example/">Website</a></div>
      <div class="contact-info__item">Personal Website <a href="https://jane.example/">jane.example</a></div>
      </main>'''
    assert [row["url"] for row in sources.profile_websites("Jane Doe", html, "https://university.example/jane")] == ["https://jane.example/"]


def test_toulouse_supervisor_only_card_produces_no_candidates():
    seed = next(seed for seed in sources.DIRECTORY_SEEDS if seed["name"] == "Toulouse")
    html = '<article><h3>Jane Doe</h3><p>Supervisor: <a href="https://supervisor.example/">Personal website</a></p></article>'
    assert sources.directory_links(seed, html, seed["url"]) == []


def test_profile_name_matching_preserves_non_latin_identity():
    html = '<main><h1>王 小明</h1><a href="https://wang.example/">Website</a></main>'
    assert sources.profile_websites("李 小明", html, "https://university.example/wang") == []
    assert len(sources.profile_websites("王 小明", html, "https://university.example/wang")) == 1


def test_bonn_profiles_can_use_institution_subdomains_not_unrelated_domains():
    seed = next(seed for seed in sources.DIRECTORY_SEEDS if seed["name"] == "Bonn")
    card = '<div class="table-row content-row"><span class="fullname"><a href="{url}">Alice Smith</a></span></div>'
    html = card.format(url="https://micro.uni-bonn.de/alice") + card.format(url="https://uni-bonn.de.attacker.example/alice")
    assert sources.directory_links(seed, html, seed["url"]) == [{"name": "Alice Smith", "url": "https://micro.uni-bonn.de/alice", "kind": "profile"}]
