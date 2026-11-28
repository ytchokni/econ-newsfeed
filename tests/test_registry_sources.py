"""ORCID / OpenAlex / RePEc-IDEAS parsing and the miner's registry step.

Recorded fixtures only (tests/fixtures/mining); no network.
"""
import json
from collections import defaultdict
from pathlib import Path

import pytest

from backend.discovery import mining
from backend.discovery import registry_sources as rs

FIXTURES = Path(__file__).parent / "fixtures" / "mining"
ORCID = "0000-0002-1825-0097"
TITLE = "Credit Supply and Employment Dynamics in Regional Labor Markets"


def text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def data(name):
    return json.loads(text(name))


# ---------------------------------------------------------------- parsers

@pytest.mark.parametrize("value,expected", [
    (ORCID, ORCID), ("https://orcid.org/" + ORCID, ORCID), ("0000-0002-1694-233x", "0000-0002-1694-233X"),
    ("0000-0002-1825-0098", None), ("orcid", None), (None, None),
    ("https://evil.example/" + ORCID, None),
])
def test_normalize_orcid_checks_checksum_and_host(value, expected):
    assert rs.normalize_orcid(value) == expected


def test_openalex_url_batches_valid_ids_and_selects_only_orcid():
    url = rs.openalex_authors_url(["A5000000001", "bogus", "A5000000002"], mailto="ops@example.org")
    assert url.startswith(rs.OPENALEX_AUTHORS_URL + "?")
    assert "openalex%3AA5000000001%7CA5000000002" in url
    assert "select=id%2Corcid%2Cdisplay_name" in url and "mailto=ops%40example.org" in url
    assert len(rs.openalex_authors_url([f"A{5000000000 + i}" for i in range(80)]).split("%7C")) == 50


def test_parse_openalex_authors_keeps_only_valid_orcids():
    assert rs.parse_openalex_authors(data("openalex_authors.json")) == {
        "A5000000001": {"orcid": ORCID, "display_name": "Ingrid Sandberg"}}


def test_openalex_api_key_goes_in_a_header(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "secret")
    assert rs.openalex_headers() == {"Authorization": "Bearer secret"}
    monkeypatch.delenv("OPENALEX_API_KEY")
    assert rs.openalex_headers() == {}


def test_orcid_record_public_websites_with_provenance():
    sites, reason = rs.orcid_websites(data("orcid_record.json"), ORCID, "Ingrid", "Sandberg")
    assert reason is None
    assert [(s["url"], s["self_asserted"]) for s in sites] == [
        ("https://sites.google.com/view/ingridsandberg/home", True),
        ("https://www.linkedin.com/in/ingrid-sandberg-example", True),
        ("https://www.example-university.edu/people/isandberg", False),
    ]  # the LIMITED-visibility link is never used


def test_orcid_record_for_another_person_is_refused():
    assert rs.orcid_websites(data("orcid_record.json"), ORCID, "Henrik", "Sandberg") == ([], "orcid_name_mismatch")
    assert rs.orcid_websites({"names": {}}, ORCID, "Ingrid", "Sandberg") == ([], "orcid_name_mismatch")


def test_orcid_record_without_websites():
    record = data("orcid_record.json")
    record["website"]["websites"] = []
    assert rs.orcid_websites(record, ORCID, "Ingrid M.", "Sandberg") == ([], "orcid_no_websites")


def test_parse_ideas_index_reads_e_and_f_author_links_only():
    url = rs.IDEAS_INDEX_URLS[18]
    entries = rs.parse_ideas_index(text("ideas_index_s.html"), url)
    assert [(e["first_name"], e["last_name"], e["short_id"]) for e in entries] == [
        ("Ingrid Maria", "Sandberg", "psa901"), ("Kenji", "Sato", "psa902"), ("Paul", "Schuster", "psa903"),
        ("John", "Smith", "psa904"), ("John", "Smith", "psa905"), ("Mette", "Søndergaard", "psa906"),
    ]
    assert entries[0]["url"] == "https://ideas.repec.org/e/psa901.html"
    assert rs.parse_ideas_index(text("ideas_index_s.html"), "https://mirror.example/i/es.html") == []


def test_parse_ideas_author_reads_personal_homepage_not_affiliation():
    author = rs.parse_ideas_author(text("ideas_author.html"), "https://ideas.repec.org/e/psa901.html")
    assert author["first_name"] == "Ingrid Maria" and author["last_name"] == "Sandberg"
    assert author["short_id"] == "psa901"
    assert author["homepage"] == "https://sites.google.com/view/ingridsandberg/home"
    assert TITLE in author["research_text"]


def test_parse_ideas_author_without_homepage_or_on_other_host():
    html = text("ideas_author.html").replace('<td class="homelabel"></td>', '<td class="otherlabel"></td>')
    assert rs.parse_ideas_author(html, "https://ideas.repec.org/e/psa901.html")["homepage"] is None
    assert rs.parse_ideas_author(text("ideas_author.html"), "https://other.example/e/psa901.html") is None


# ---------------------------------------------------------------- miner step

INGRID = {"id": 11, "first_name": "Ingrid", "last_name": "Sandberg", "openalex_author_id": "A5000000001"}
KENJI = {"id": 12, "first_name": "Kenji", "last_name": "Sato", "openalex_author_id": "A5000000002"}
JOHN = {"id": 13, "first_name": "John", "last_name": "Smith", "openalex_author_id": None}
MAIN_HOME = "https://sites.google.com/view/ingridsandberg/home"


class Store:
    apply = False

    def __init__(self, unlinked, titles=(TITLE,)):
        self.unlinked, self.titles = unlinked, list(titles)
        self.queued, self.checkpoints, self.due = [], {}, {}

    def unlinked_researchers(self):
        return self.unlinked

    def orcid_lookup_targets(self, limit):
        return [p for p in self.unlinked if p["openalex_author_id"]
                and self.source_due("openalex_orcid", "openalex:" + p["openalex_author_id"])][:limit]

    def source_due(self, kind, url):
        return self.due.get((kind, url), (kind, url) not in self.checkpoints)

    def checkpoint(self, kind, url, content_hash, error=None, retry_hours=168):
        self.checkpoints[(kind, url)] = {"error": error, "retry_hours": retry_hours}

    def enqueue(self, candidate):
        self.queued.append(candidate)
        return True

    def paper_titles(self, rid, source_rid=None):
        return self.titles


class Fetcher:
    budget_exhausted = False

    def __init__(self, pages, cost=1):
        self.pages, self.requests, self.urls, self.cost = pages, 0, [], cost

    def _reply(self, url):
        self.requests += self.cost
        self.urls.append(url)
        return self.pages.get(url, {"url": url, "status": 404, "html": "", "error": "http_404"})

    def fetch(self, url):
        reply = self._reply(url)
        return {"url": url, "status": 200, "html": reply} if isinstance(reply, str) else reply

    def fetch_json(self, url, headers=None):
        reply = self._reply(url)
        if isinstance(reply, dict) and "status" in reply:
            return reply
        return {"url": url, "status": 200, "html": "", "json": reply}


def settings(**kw):
    return mining._registry_settings(dict({"max_requests": 60, "orcid_authors": 50, "repec_index_pages": 1}, **kw))


def run(store, fetcher, monkeypatch, **kw):
    monkeypatch.setattr(mining, "_openalex_request_allowed", lambda store, settings: True)
    stats = defaultdict(int)
    mining._mine_registries(store, fetcher, {}, set(), stats, settings(**kw))
    return stats


def orcid_pages():
    url = rs.openalex_authors_url(["A5000000001", "A5000000002"])
    return {url: data("openalex_authors.json"), rs.orcid_record_url(ORCID): data("orcid_record.json")}


def test_orcid_step_queues_permitted_record_websites_with_researcher(monkeypatch):
    store = Store([INGRID, KENJI])
    stats = run(store, Fetcher(orcid_pages()), monkeypatch, repec_index_pages=0)
    assert [(c["researcher_id"], c["url"], c["source_kind"]) for c in store.queued] == [
        (11, MAIN_HOME, "orcid"), (11, "https://www.example-university.edu/people/isandberg", "orcid")]
    assert store.queued[0]["source_url"] == "https://orcid.org/" + ORCID
    assert store.queued[0]["evidence"]["self_asserted"] is True
    assert store.queued[1]["evidence"]["self_asserted"] is False
    # LinkedIn is not a crawlable homepage; both authors are checkpointed.
    assert stats["orcid_queued"] == 2 and stats["openalex_requests"] == 1
    assert store.checkpoints[("openalex_orcid", "openalex:A5000000001")]["retry_hours"] == 24 * 90
    assert store.checkpoints[("openalex_orcid", "openalex:A5000000002")]["retry_hours"] == 24 * 30


def test_orcid_name_mismatch_queues_nothing(monkeypatch):
    other = dict(INGRID, first_name="Henrik")
    store = Store([other])
    pages = {rs.openalex_authors_url(["A5000000001"]): data("openalex_authors.json"),
             rs.orcid_record_url(ORCID): data("orcid_record.json")}
    stats = run(store, Fetcher(pages), monkeypatch, repec_index_pages=0)
    assert store.queued == [] and stats["orcid_name_mismatch"] == 1
    assert store.checkpoints[("openalex_orcid", "openalex:A5000000001")]["error"] == "orcid_name_mismatch"


def test_failed_openalex_request_checkpoints_nobody(monkeypatch):
    store = Store([INGRID])
    stats = run(store, Fetcher({}), monkeypatch, repec_index_pages=0)
    assert store.checkpoints == {} and stats["registry_errors"] == 1


def test_openalex_daily_budget_stops_the_orcid_step(monkeypatch):
    store = Store([INGRID])
    fetcher = Fetcher(orcid_pages())
    monkeypatch.setattr(mining, "_openalex_request_allowed", lambda store, settings: False)
    mining._mine_orcid(store, fetcher, mining._RegistryRoom(fetcher, 30), {}, set(), defaultdict(int), settings())
    assert fetcher.requests == 0


def test_orcid_step_respects_its_half_of_the_registry_budget(monkeypatch):
    store = Store([INGRID, KENJI])
    fetcher = Fetcher(orcid_pages())
    run(store, fetcher, monkeypatch, max_requests=2, repec_index_pages=0)
    assert fetcher.requests == 0  # one request of room cannot cover robots + batch + record


def repec_pages():
    return {rs.IDEAS_INDEX_URLS[18]: text("ideas_index_s.html"),
            "https://ideas.repec.org/e/psa901.html": text("ideas_author.html")}


def only_letter_s(store):
    for url in rs.IDEAS_INDEX_URLS:
        if url != rs.IDEAS_INDEX_URLS[18]:
            store.due[("repec_index", url)] = False


def test_repec_step_queues_homepage_of_unique_exact_name_with_title_on_ideas(monkeypatch):
    ingrid = dict(INGRID, first_name="Ingrid Maria", openalex_author_id=None)
    store = Store([ingrid, JOHN])
    only_letter_s(store)
    fetcher = Fetcher(repec_pages())
    stats = run(store, fetcher, monkeypatch)
    assert [(c["researcher_id"], c["url"], c["source_kind"]) for c in store.queued] == [(11, MAIN_HOME, "repec")]
    assert store.queued[0]["evidence"]["repec_short_id"] == "psa901"
    # Two IDEAS "John Smith" entries: ambiguous, no author page fetched.
    assert stats["repec_ambiguous"] == 1
    assert not any("psa904" in u or "psa905" in u for u in fetcher.urls)
    assert store.checkpoints[("repec_index", rs.IDEAS_INDEX_URLS[18])]["retry_hours"] == 168
    assert ("repec_author", "https://ideas.repec.org/e/psa901.html") in store.checkpoints


def test_repec_homonym_without_shared_title_is_not_queued(monkeypatch):
    ingrid = dict(INGRID, first_name="Ingrid Maria", openalex_author_id=None)
    store = Store([ingrid], titles=["A Completely Different Paper About Something Else Entirely"])
    only_letter_s(store)
    stats = run(store, Fetcher(repec_pages()), monkeypatch)
    assert store.queued == [] and stats["repec_no_title_match"] == 1


def test_repec_two_database_people_with_one_name_are_ambiguous(monkeypatch):
    first = dict(INGRID, first_name="Ingrid Maria", openalex_author_id=None)
    store = Store([first, dict(first, id=99)])
    only_letter_s(store)
    fetcher = Fetcher(repec_pages())
    stats = run(store, fetcher, monkeypatch)
    assert store.queued == [] and stats["repec_ambiguous"] == 1
    assert fetcher.urls == [rs.IDEAS_INDEX_URLS[18]]


def test_repec_letter_is_reread_until_all_matches_are_fetched(monkeypatch):
    ingrid = dict(INGRID, first_name="Ingrid Maria", openalex_author_id=None)
    store = Store([ingrid])
    only_letter_s(store)
    # Room for the index page only (ORCID takes nothing: nobody has an OpenAlex ID).
    fetcher = Fetcher(repec_pages(), cost=3)
    run(store, fetcher, monkeypatch, max_requests=3)
    assert fetcher.urls == [rs.IDEAS_INDEX_URLS[18]]
    assert ("repec_index", rs.IDEAS_INDEX_URLS[18]) not in store.checkpoints
    fetcher.cost = 1
    run(store, fetcher, monkeypatch, max_requests=10)
    assert ("repec_index", rs.IDEAS_INDEX_URLS[18]) in store.checkpoints
    assert len(store.queued) == 1


def test_zero_registry_budget_makes_no_requests(monkeypatch):
    store = Store([INGRID])
    fetcher = Fetcher(orcid_pages())
    run(store, fetcher, monkeypatch, max_requests=0)
    assert fetcher.requests == 0


def test_registry_candidate_still_needs_full_verification(monkeypatch):
    """A queued ORCID website is approved only through _verify + approve_bundle."""
    from unittest.mock import MagicMock
    page = f"""<html><title>Ingrid Sandberg</title><body><h1>Ingrid Sandberg</h1>
      <p>I am an assistant professor of economics. My research studies labor markets,
      credit supply and employment; this site lists my publications and working papers.</p>
      <h2>Research</h2><p>{TITLE}</p></body></html>"""
    SITE = "https://ingridsandberg.example/"
    person = {"id": 11, "first_name": "Ingrid", "last_name": "Sandberg", "affiliation": None}
    candidate = {"id": 5, "researcher_id": 11, "name": "Ingrid Sandberg", "url": SITE,
                 "source_url": "https://orcid.org/" + ORCID, "source_kind": "orcid",
                 "evidence": {"orcid": ORCID}, "attempts": 0}
    db = MagicMock()
    db.paper_titles.return_value = [TITLE]
    db.approve_bundle.side_effect = lambda c, urls, name: {"held": False, "inserted": urls}
    stats = defaultdict(int)
    mining._verify(db, Fetcher({SITE: page}), candidate, {11: person}, {}, set(), stats)
    assert db.approve_bundle.call_args.args[1] == [{"url": SITE, "page_type": "HOME"}]
    assert db.approve_bundle.call_args.args[2] == ("Ingrid", "Sandberg")
    # Without one of the researcher's titles on the page it is held for review.
    db2 = MagicMock()
    db2.paper_titles.return_value = [TITLE]
    mining._verify(db2, Fetcher({SITE: page.replace(TITLE, "Something else")}), candidate,
                   {11: person}, {}, set(), defaultdict(int))
    db2.approve_bundle.assert_not_called()
    assert db2.save_result.call_args.args[1] == "review"


def test_registry_settings_reject_negative_limits():
    with pytest.raises(ValueError):
        mining._registry_settings({"max_requests": -1})
    assert mining.registry_host_delays(settings(delay_seconds=3)) == {"orcid.org": 3, "ideas.repec.org": 3}
