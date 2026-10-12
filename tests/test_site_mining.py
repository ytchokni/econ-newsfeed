"""End-to-end miner decisions with real parsing and fake network/storage."""
from unittest.mock import MagicMock

import pytest

from backend.discovery import mining
from backend.discovery.mining_sources import BudgetExceeded

TITLE = "Credit Supply and Employment Dynamics in Regional Labor Markets"
PERSON = {"id": 2, "first_name": "Alice", "last_name": "Smith", "affiliation": None}


def html(name="Alice Smith", paper=TITLE, links=""):
    return f"""<html><title>{name}</title><body><h1>{name}</h1>
    <p>I am a professor of economics. My research examines labor markets,
    credit supply and employment. This website contains my publications,
    current working papers, and ongoing research projects.</p>
    <h2>Research</h2><p>{paper}</p>{links}</body></html>"""


class Fetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requests = 0

    def fetch(self, url):
        self.requests += 1
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return {"url": url, "status": 200, "html": value, "content_type": "text/html"} if isinstance(value, str) else value


def candidate(**kwargs):
    return dict({"id": 7, "researcher_id": 2, "name": "Alice Smith",
                 "url": "https://alice.example/", "source_url": "https://bob.example/",
                 "source_kind": "cached_link", "evidence": {"source_researcher_id": 1},
                 "attempts": 0}, **kwargs)


def store():
    result = MagicMock()
    result.paper_titles.return_value = [TITLE]
    result.approve_bundle.side_effect = lambda c, urls, name: {"held": False, "inserted": urls}
    return result


def stats():
    return {k: 0 for k in ["queued", "retry", "review", "added_urls", "approved_sites", "cached_pages"]}


def verify(c, pages, db=None):
    db = db or store()
    counts = stats()
    mining._verify(db, Fetcher(pages), c, {2: PERSON}, {"alice smith": [PERSON]}, set(), counts)
    return db, counts


def test_adds_home_and_all_verified_sections_with_shared_paper_proof():
    home = "https://alice.example/"
    research = home + "research"
    papers = home + "publications"
    db, counts = verify(candidate(), {
        home: html(links='<a href="research">Research</a><a href="publications">Publications</a>'),
        research: html(), papers: html(),
    })
    db.paper_titles.assert_called_once_with(2, 1)
    urls = db.approve_bundle.call_args.args[1]
    assert {(p["url"], p["page_type"]) for p in urls} == {
        (home, "HOME"), (research, "RESEARCH"), (papers, "PUBLICATIONS")}
    assert counts["added_urls"] == 3


def test_research_anchor_recovers_home_before_approval():
    home = "https://alice.example/"
    db, _ = verify(candidate(url=home + "research"), {home: html(), home + "research": html()})
    assert any(p["page_type"] == "HOME" for p in db.approve_bundle.call_args.args[1])


def test_research_navigation_reaches_second_level_working_papers():
    home = "https://alice.example/"
    db, _ = verify(candidate(), {
        home: html(links='<a href="research">Research</a>'),
        home + "research": html(links='<a href="working-papers">Working papers</a>'),
        home + "working-papers": html(),
    })
    assert any(p["page_type"] == "WORKING_PAPERS" for p in db.approve_bundle.call_args.args[1])


def test_name_match_without_paper_corroboration_is_reviewed():
    db, counts = verify(candidate(), {"https://alice.example/": html(paper="Unrelated title")})
    db.approve_bundle.assert_not_called()
    assert counts["review"] == 1


def test_new_papers_section_inherits_corroboration_from_verified_home():
    home = "https://alice.example/"
    db, _ = verify(candidate(), {home: html(links='<a href="working-papers">Working papers</a>'),
        home + "working-papers": html(paper="An entirely new research paper about economic policy")})
    assert any(p["page_type"] == "WORKING_PAPERS" for p in db.approve_bundle.call_args.args[1])


def test_uncertain_linked_section_is_held_not_silently_omitted():
    home = "https://alice.example/"
    db, counts = verify(candidate(), {home: html(links='<a href="research">Research</a>'),
        home + "research": '<html><h1>Unrelated page</h1><p>Nothing academic.</p></html>'})
    db.approve_bundle.assert_not_called()
    assert counts["review"] == 1


def test_ambiguous_person_is_not_assigned_to_first_name_match():
    db, counts = verify(candidate(researcher_id=None), {"https://alice.example/": html()})
    db.approve_bundle.assert_not_called()
    assert counts["review"] == 1


def test_initials_only_identity_is_not_automatically_accepted():
    db, counts = verify(candidate(), {"https://alice.example/": html(name="A. Smith")})
    db.approve_bundle.assert_not_called()
    assert counts["review"] == 1


def test_failed_section_keeps_task_retryable_and_does_not_partially_insert():
    home = "https://alice.example/"
    db, counts = verify(candidate(), {home: html(links='<a href="research">Research</a>'),
        home + "research": {"url": home + "research", "status": 503, "html": ""}})
    db.approve_bundle.assert_not_called()
    assert counts["retry"] == 1


def test_different_institutional_person_is_not_fetched():
    home = "https://university.edu/people/alice-smith"
    db, counts = verify(candidate(url=home), {home: html(links='<a href="/people/bob-jones/research">Research</a>')})
    assert counts["added_urls"] == 1


def test_profile_is_enqueued_even_if_its_url_is_already_active():
    db = store()
    c = candidate(source_kind="directory_profile")
    assert mining._enqueue(db, c, {}, {mining.url_key(c["url"])})
    db.enqueue.assert_called_once()


def test_overlong_untrusted_url_cannot_poison_every_source_retry():
    db = store()
    assert not mining._enqueue(db, candidate(url="https://alice.example/" + "a" * 2048), {}, set())
    db.enqueue.assert_not_called()


def test_known_site_papers_subdomain_retains_home_scope_and_page_type():
    home = "https://alice.example/"
    papers = "https://papers.alice.example/"
    c = candidate(url=papers, source_url=home, source_kind="known_site",
                  evidence={"source_researcher_id": 2, "page_type": "RESEARCH"})
    db = store()
    assert mining._enqueue(db, c, {}, {mining.url_key(home)})
    db, _ = verify(c, {home: html(), papers: html()}, db)
    assert {(p["url"], p["page_type"]) for p in db.approve_bundle.call_args.args[1]} == {
        (home, "HOME"), (papers, "RESEARCH")}


def test_research_subdomain_with_other_person_and_shared_paper_is_held():
    home = "https://alice.example/"
    papers = "https://research.alice.example/"
    db, counts = verify(candidate(), {
        home: html(links=f'<a href="{papers}">Research</a>'),
        papers: html(name="Bob Jones"),
    })
    db.approve_bundle.assert_not_called()
    assert counts["review"] == 1


def test_checkpoint_only_advances_after_all_durable_enqueues():
    db = store()
    db.cached_sources.return_value = [{"url_id": 1, "researcher_id": 2,
        "url": "https://alice.example/", "raw_html": html(links='<a href="research">Research</a>'), "content_hash": "abc"}]
    db.enqueue.side_effect = RuntimeError("Database temporarily unavailable")
    with pytest.raises(RuntimeError):
        mining._mine_cached(db, {2: PERSON}, {"alice smith": [PERSON]}, set(), 1, stats(), float("inf"))
    db.checkpoint.assert_not_called()


def test_retry_exhaustion_holds_instead_of_repeating_forever():
    db = store()
    counts = stats()
    mining._retry(db, candidate(attempts=2), "Unavailable", counts)
    assert db.save_result.call_args.args[1] == "review"


def test_batch_default_is_read_only_and_lock_contention_is_noop(monkeypatch):
    db = MagicMock()
    db.acquired = False
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    monkeypatch.setattr(mining, "MiningStore", factory)
    result = mining.run_site_mining_batch()
    factory.assert_called_once_with(apply=False)
    assert result["skipped"] == "another_mining_job_is_running"
    db.researchers.assert_not_called()


def test_budget_exhaustion_leaves_candidate_retryable(monkeypatch):
    db = store()
    db.acquired = True
    db.researchers.return_value = [PERSON]
    db.known_urls.return_value = []
    db.due_candidates.return_value = [candidate()]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    monkeypatch.setattr(mining, "MiningStore", factory)
    monkeypatch.setattr(mining, "MiningFetcher", lambda **kwargs: Fetcher({"https://alice.example/": BudgetExceeded()}))
    result = mining.run_site_mining_batch(mode="cached", source_limit=0)
    assert result["budget_exhausted"]
    assert result["retry"] == 1
    assert db.save_result.call_args.kwargs["count_attempt"] is False
    db.finish_run.assert_called_once()


def test_zero_limits_do_not_fall_back_to_large_defaults(monkeypatch):
    db = store()
    db.acquired = True
    db.researchers.return_value = []
    db.known_urls.return_value = []
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    monkeypatch.setattr(mining, "MiningStore", factory)
    mining.run_site_mining_batch(mode="cached", source_limit=0, candidate_limit=0, max_requests=0)
    db.cached_sources.assert_not_called()
    db.due_candidates.assert_not_called()
