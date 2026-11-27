"""Matching and classification logic of scripts/validate_top5_coverage.py (no network, no DB)."""
import gzip
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_top5_coverage.py"
_spec = importlib.util.spec_from_file_location("validate_top5_coverage", _SCRIPT)
v = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = v  # dataclasses look the module up while the class is built
_spec.loader.exec_module(v)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cr_item(doi, title, authors=(("Jane", "Doe"),), *, print_year=2022, online=None, subtitle=None,
            type_="journal-article"):
    item = {
        "DOI": doi, "type": type_, "title": [title],
        "author": [{"given": g, "family": f} for g, f in authors],
    }
    if print_year:
        item["published-print"] = {"date-parts": [[print_year, 5]]}
    if online:
        item["published-online"] = {"date-parts": [[online, 1, 2]]}
    if subtitle:
        item["subtitle"] = [subtitle]
    return item


def article(doi="10.1257/aer.20200001", title="Trade with Correlation", authors=(("Jane", "Doe"),),
            journal="aer", year=2022, subtitle=""):
    return v.Article(doi=doi, journal=journal, year=year, title=title, subtitle=subtitle,
                     authors=[v.Author(g, f) for g, f in authors])


def researcher(rid, first, last, *, tracked=True, oa=None):
    return v.Researcher(rid, first, last, oa, n_urls=1 if tracked else 0, n_active=1 if tracked else 0)


def build(papers, researchers=(), authorship=(), links=()):
    names = v.NameIndex(researchers)
    return v.PaperIndex(papers, links, [{"r": r, "p": p} for r, p in authorship], names), names


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return FakeResponse(self.pages.pop(0))


def crossref_page(items, total, cursor="next"):
    return {"message": {"items": items, "total-results": total, "next-cursor": cursor}}


# ---------------------------------------------------------------------------
# Normalisation and exclusion rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("10.1257/AER.20201234", "10.1257/aer.20201234"),
    ("https://doi.org/10.3982/ECTA17000", "10.3982/ecta17000"),
    ("http://dx.doi.org/10.1086/718000.", "10.1086/718000"),
    ("doi: 10.1093/qje/qjab001", "10.1093/qje/qjab001"),
    ("not a doi", None),
    (None, None),
])
def test_normalize_doi(raw, expected):
    assert v.normalize_doi(raw) == expected


def test_doi_from_url():
    assert v.doi_from_url("https://www.aeaweb.org/articles?id=10.1257/aer.20191234") == "10.1257/aer.20191234"
    assert v.doi_from_url("https://academic.oup.com/qje/article/138/1/1/6696000") is None


@pytest.mark.parametrize("title,authors,doi,reason", [
    ("Front Matter", (), "10.1257/aer.113.1.fm", "no_authors"),
    ("Recent Referees", (), "10.1086/724000", "no_authors"),
    ("Dividend Taxes and the Allocation of Capital: Comment", (("A", "B"),), "10.1257/aer.1", "comment_or_reply"),
    ("A Comment on: <i>“Invidious Comparisons”</i>", (("A", "B"),), "10.3982/ecta1", "comment_or_reply"),
    ("Reply to: Comments on “Presidential Address”", (("A", "B"),), "10.3982/ecta2", "comment_or_reply"),
    ("Tax Smoothing in Frictional Labor Markets: A Reply", (("A", "B"),), "10.1086/1", "comment_or_reply"),
    ("Not a Flying Start after All? A Comment", (("A", "B"),), "10.1086/4", "comment_or_reply"),
    ("Still Flying: Reply to “Not a Flying Start after All?” by Lillebø et al.", (("A", "B"),), "10.1086/5",
     "comment_or_reply"),
    ("Rational Bubbles in UK Housing Markets: Comment on “No-Bubble Condition”", (("A", "B"),), "10.3982/e5",
     "comment_or_reply"),
    ("Corrigendum: Long-Term Contracting With Time-Inconsistent Agents", (("A", "B"),), "10.3982/ecta3",
     "erratum_or_retraction"),
    ("Retraction of “Dividend Taxes and the Allocation of Capital”", (("A", "B"),), "10.1257/aer.2",
     "erratum_or_retraction"),
    ("Correction to: Uncertainty Shocks as Second-Moment News Shocks", (("A", "B"),), "10.1093/restud/1",
     "erratum_or_retraction"),
    ("Nobel Lecture: Multiple Equilibria", (("A", "B"),), "10.1086/2", "lecture_or_address"),
    ("Presidential Address: Does Monetary Policy Matter?", (("A", "B"),), "10.1257/aer.3", "lecture_or_address"),
    ("In Memoriam: A Great Economist", (("A", "B"),), "10.1086/3", "obituary"),
    ("The Econometric Society Annual Reports Report of the Secretary", (("A", "B"),), "10.3982/ecta4",
     "front_matter_or_editorial"),
    ("Something in Papers and Proceedings", (("A", "B"),), "10.1257/pandp.20231001", "aea_papers_and_proceedings"),
])
def test_exclusion_rules(title, authors, doi, reason):
    assert v.exclusion_reason(cr_item(doi, title, authors)) == reason


@pytest.mark.parametrize("title", [
    "The Reversal Interest Rate",
    "Commitment and Competition in Markets",       # "comment" must be a whole word
    "How to Address Climate Change: Evidence from Carbon Taxes",
    "Judging Judge Fixed Effects",
    "Replication Crisis? Learning about Reproducibility",
    "Are Referees and Editors in Economics Gender Neutral?",
    "Political Correctness, Social Image, and Information Transmission",
])
def test_research_articles_are_kept(title):
    assert v.exclusion_reason(cr_item("10.1257/aer.9", title, (("A", "B"),))) is None


def test_article_year_prefers_issue_then_online():
    assert v.article_year(cr_item("10.1/x", "T", print_year=2021, online=2019)) == 2021
    assert v.article_year(cr_item("10.1/x", "T", print_year=None, online=2025)) == 2025


@pytest.mark.parametrize("venue,key", [
    ("American Economic Review", "aer"),
    ("AER 112(3)", "aer"),
    ("American Economic Review: Insights", None),
    ("AEA Papers and Proceedings", None),
    ("Journal of Political Economy", "jpe"),
    ("Journal of Political Economy Microeconomics", None),
    ("European Journal of Political Economy", None),
    ("The Review of Economic Studies", "restud"),
    ("Review of Economics and Statistics", None),
    ("Econometrica 89(4)", "ecta"),
    ("The Quarterly Journal of Economics", "qje"),
    ("", None),
])
def test_venue_journal(venue, key):
    assert v.venue_journal(venue) == key


# ---------------------------------------------------------------------------
# Crossref fetching (mocked)
# ---------------------------------------------------------------------------

def test_fetch_crossref_pages_with_cursor_and_caches(tmp_path):
    p1 = crossref_page([cr_item("10.1257/aer.1", "A Title")], total=2, cursor="c1")
    p2 = crossref_page([cr_item("10.1257/aer.2", "B Title")], total=2, cursor="c2")
    session = FakeSession([p1, p2])
    items = v.fetch_crossref_works("0002-8282", "2018-01-01", "2024-12-31", tmp_path, session=session, sleep=0)
    assert [i["DOI"] for i in items] == ["10.1257/aer.1", "10.1257/aer.2"]
    assert session.calls[0][1]["cursor"] == "*" and session.calls[1][1]["cursor"] == "c1"
    assert "type:journal-article" in session.calls[0][1]["filter"]
    assert "mailto" not in session.calls[0][1]
    # Cached: a second call makes no request.
    again = v.fetch_crossref_works("0002-8282", "2018-01-01", "2024-12-31", tmp_path,
                                   session=FakeSession([]), sleep=0)
    assert len(again) == 2


def test_load_ground_truth_filters_years_excludes_and_dedupes(tmp_path):
    items = [
        cr_item("10.1257/aer.1", "Trade with Correlation", print_year=2021, online=2019),
        cr_item("10.1257/aer.2", "Old Article", print_year=2019),
        cr_item("10.1257/aer.3", "Front Matter", authors=()),
        cr_item("10.1257/aer.4", "Some Paper: Comment"),
        cr_item("10.1257/aer.5", "Forthcoming Online", print_year=None, online=2024),
    ]
    # Both ISSNs return the same records; they must be deduplicated by DOI.
    session = FakeSession([crossref_page(items, 5, None), crossref_page(items, 5, None)])
    gt = v.load_ground_truth(["aer"], 2020, 2024, tmp_path, session=session, sleep=0, today=date(2024, 6, 1))
    assert [a.doi for a in gt.articles] == ["10.1257/aer.1", "10.1257/aer.5"]
    assert {a.doi: a.year for a in gt.articles} == {"10.1257/aer.1": 2021, "10.1257/aer.5": 2024}
    assert sorted(e["reason"] for e in gt.excluded) == ["comment_or_reply", "no_authors"]
    assert "until-pub-date:2024-06-01" in session.calls[0][1]["filter"]
    assert "from-pub-date:2018-01-01" in session.calls[0][1]["filter"]


def test_attach_openalex_authors_uses_cache(tmp_path):
    cache = {"10.1257/aer.1": [{"name": "José Pérez", "id": "A123", "orcid": None}]}
    with gzip.open(tmp_path / "openalex_authorships.json.gz", "wt") as f:
        json.dump(cache, f)
    art = article(doi="10.1257/aer.1", authors=(("Jose", "Perez"),))
    assert v.attach_openalex_authors([art], tmp_path, session=FakeSession([]), sleep=0) == 1
    assert art.authors[0].openalex_id == "A123"


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _match_ids(names, given, family, oa=None):
    return {(m.researcher_id, m.method) for m in names.match_author(v.Author(given, family, openalex_id=oa))}


def test_name_match_accents_and_hyphens():
    names = v.NameIndex([researcher(1, "Andres", "Rodriguez Clare"), researcher(2, "José", "Pérez")])
    assert _match_ids(names, "Andrés", "Rodríguez-Clare") == {(1, "name")}
    assert _match_ids(names, "Jose", "Perez") == {(2, "name")}


def test_name_match_double_surname_and_particles():
    names = v.NameIndex([researcher(1, "Maria", "Gonzalez"), researcher(2, "Jan", "Van Reenen")])
    assert _match_ids(names, "María", "González-Pérez") == {(1, "name")}
    assert _match_ids(names, "Jan", "van Reenen") == {(2, "name")}


def test_name_match_initials_only_when_unambiguous():
    names = v.NameIndex([researcher(1, "Michael", "Smith"), researcher(2, "Mary", "Smith"),
                         researcher(3, "Peter", "Jones")])
    assert _match_ids(names, "M.", "Smith") == set()             # two tracked M. Smiths
    assert _match_ids(names, "P. J.", "Jones") == {(3, "name_initial")}
    assert _match_ids(names, "J. Michael", "Smith") == {(1, "name")}  # middle-name form


def test_name_match_rejects_different_given_name():
    names = v.NameIndex([researcher(1, "David", "Card")])
    assert _match_ids(names, "Daniel", "Card") == set()


def test_openalex_id_match_requires_surname_overlap():
    names = v.NameIndex([researcher(1, "Wei", "Xiong", oa="A5000"), researcher(2, "Someone", "Else", oa="A6000")])
    assert (1, "openalex") in _match_ids(names, "W.", "Xiong", oa="A5000")
    assert _match_ids(names, "Anna", "Other", oa="A6000") == set()


def test_untracked_name_reported_but_not_tracked():
    names = v.NameIndex([researcher(1, "Jane", "Doe", tracked=False)])
    ms = names.match_author(v.Author("Jane", "Doe"))
    assert [(m.researcher_id, m.tracked) for m in ms] == [(1, False)]


# ---------------------------------------------------------------------------
# Paper matching tiers
# ---------------------------------------------------------------------------

def test_doi_tier_from_paper_and_link():
    idx, _ = build(
        [{"id": 1, "title": "Unrelated WP Title", "doi": "https://doi.org/10.1257/AER.20200001"},
         {"id": 2, "title": "Another Title Entirely"}],
        links=[{"paper_id": 2, "doi": None, "url": "https://doi.org/10.1257/aer.20200002"}],
    )
    assert idx.match(article(doi="10.1257/aer.20200001")).by_tier["doi"] == [1]
    m = idx.match(article(doi="10.1257/aer.20200002", title="Something Else"))
    assert m.tier == "doi" and m.paper_ids == [2]


def test_title_tier_uses_project_normalisation_and_subtitle():
    idx, _ = build([{"id": 5, "title": "Trade with correlation!"},
                    {"id": 6, "title": "Inference on Winners: Theory and Practice"}])
    assert idx.match(article(title="Trade With Correlation")).tier == "title"
    m = idx.match(article(title="Inference on Winners", subtitle="Theory and Practice"))
    assert m.tier == "title" and m.paper_ids == [6]


def test_fuzzy_accepts_typo_and_rejects_different_title():
    idx, _ = build([{"id": 1, "title": "The Macroeconomic Impact of Short-Termism in Public Firms"},
                    {"id": 2, "title": "The Macroeconomic Impact of Fiscal Policy in Small Economies"}])
    m = idx.match(article(title="The Macroeconomic Impact of Short Termism in Public Firm"))
    assert m.tier == "fuzzy" and m.paper_ids == [1]
    assert idx.match(article(title="The Macroeconomic Impact of Monetary Policy in Large Economies")).tier is None


def test_fuzzy_prefix_rule_requires_shared_author():
    papers = [{"id": 1, "title": "Gender Differences in Job Search and the Earnings Gap"}]
    art = article(title="Gender Differences in Job Search and the Earnings Gap",
                  subtitle="Evidence from the Field and Lab", authors=(("Thomas", "Le Barbanchon"),))
    art.title, art.subtitle = "Gender Differences in Job Search and the Earnings Gap: Evidence from the Field", ""
    idx, _ = build(papers)
    assert idx.match(art).tier is None
    idx, _ = build(papers, [researcher(9, "Thomas", "Le Barbanchon")], authorship=[(9, 1)])
    assert idx.match(art).tier == "fuzzy"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_observed_correct_with_doi():
    idx, names = build([{"id": 1, "title": "Trade with Correlation", "venue": "American Economic Review",
                         "status": "published", "doi": "10.1257/aer.20200001"}],
                       [researcher(7, "Jane", "Doe")], authorship=[(7, 1)])
    r = v.classify_article(article(), idx, names)
    assert (r.observed, r.category, r.detail, r.doi_linked, r.tracked) == (True, "observed_correct", "doi_linked",
                                                                          True, True)


def test_c_wrong_status_and_wrong_venue_and_missing_doi():
    papers = [{"id": 1, "title": "Trade with Correlation", "venue": "American Economic Review",
               "status": "working_paper"}]
    idx, names = build(papers)
    assert v.classify_article(article(), idx, names).detail == "c_wrong_status"
    papers[0].update(status="published", venue="Journal of Political Economy")
    idx, names = build(papers)
    assert v.classify_article(article(), idx, names).detail == "c_wrong_venue"
    papers[0].update(venue=None)
    idx, names = build(papers)
    assert v.classify_article(article(), idx, names).detail == "c_missing_venue"
    papers[0].update(venue="Journal of Polit ical Economy")  # stray space from text extraction
    idx, names = build(papers)
    assert v.classify_article(article(journal="jpe"), idx, names).detail == "c_venue_variant"
    papers[0].update(venue="American Economics Review")
    idx, names = build(papers)
    assert v.classify_article(article(), idx, names).detail == "c_venue_variant"
    papers[0].update(venue="AER")
    idx, names = build(papers)
    r = v.classify_article(article(), idx, names)
    assert (r.category, r.detail) == ("observed_correct", "c_doi_unlinked")


def test_duplicates_prefer_the_correct_record():
    idx, names = build([
        {"id": 1, "title": "Trade with Correlation", "status": "working_paper"},
        {"id": 2, "title": "Old WP name", "doi": "10.1257/aer.20200001", "status": "published",
         "venue": "American Economic Review"},
    ])
    r = v.classify_article(article(), idx, names)
    assert r.duplicate and r.category == "observed_correct" and r.best_paper["id"] == 2


def test_a_categories():
    idx, names = build([], [researcher(1, "Jane", "Doe", tracked=False)])
    assert v.classify_article(article(), idx, names).detail == "a1_author_known_untracked"
    idx, names = build([])
    assert v.classify_article(article(), idx, names).detail == "a2_no_known_author"


def _b_case(pages, papers=(), authorship=()):
    idx, names = build(list(papers), [researcher(7, "Jane", "Doe")], authorship=list(authorship))
    r = v.classify_article(article(title="Trade with Correlation in Global Supply Chains"), idx, names)
    assert r.category == "b"
    v.diagnose_b(r, {7: pages}, idx, names, content_max_chars=100)
    return r


def test_b_in_page_text_flags_pending_extraction():
    r = _b_case([{"url": "u", "is_active": 1, "pending": 1,
                  "content": "Papers. Trade With Correlation in Global Supply Chains, AER 2022."}])
    assert r.detail == "b1_in_page_text" and r.evidence["pending_extraction"] is True


def test_b_in_raw_html_only_flags_truncation():
    raw = "<li><em>Trade with Correlation</em> in Global Supply&nbsp;Chains</li>"
    r = _b_case([{"url": "u", "is_active": 1, "pending": 0, "content": "x" * 100, "raw_html": raw}])
    assert r.detail == "b2_in_raw_html_only" and r.evidence["truncated"] is True


def test_b_possible_renamed_and_not_on_page_and_no_html():
    renamed = _b_case([{"url": "u", "is_active": 1, "content": "nothing here"}],
                      papers=[{"id": 3, "title": "Supply Chains and Trade with Correlation", "status": "working_paper"}],
                      authorship=[(7, 3)])
    assert renamed.detail == "b3_possible_renamed" and renamed.evidence["similar_paper_id"] == 3
    absent = _b_case([{"url": "u", "is_active": 0, "content": "nothing here"}])
    assert absent.detail == "b4_not_on_tracked_pages" and absent.evidence["all_urls_inactive"]
    assert _b_case([{"url": "u", "is_active": 0, "content": None, "raw_html": None}]).detail == "b5_no_stored_html"


def test_summary_conditional_coverage():
    idx, names = build([{"id": 1, "title": "Trade with Correlation", "venue": "AER", "status": "published"}],
                       [researcher(7, "Jane", "Doe"), researcher(8, "John", "Roe")], authorship=[(7, 1)])
    arts = [article(),
            article(doi="10.1257/aer.2", title="Unobserved Offers Bargaining Model", authors=(("John", "Roe"),)),
            article(doi="10.1257/aer.3", title="Nobody Tracked Here At All", authors=(("Ann", "Nobody"),),
                    year=2021)]
    results = [v.classify_article(a, idx, names) for a in arts]
    s = v.summarize(results, [{"reason": "no_authors", "journal": "aer"}])
    o = s["overall"]
    assert (o["articles"], o["observed"], o["tracked_articles"], o["tracked_observed"]) == (3, 1, 2, 1)
    assert o["coverage_tracked_pct"] == 50.0 and o["a_no_tracked_author"] == 1 and o["b_tracked_absent"] == 1
    assert s["by_year"]["2021"]["articles"] == 1
    assert s["excluded"] == {"no_authors": 1}


def test_fuzzy_short_main_title_matches_with_shared_author_only():
    papers = [{"id": 1, "title": "Robots and Jobs", "venue": "Journal of Political Economy", "status": "published"}]
    art = article(title="Robots and Jobs: Evidence from US Labor Markets", journal="jpe",
                  authors=(("Daron", "Acemoglu"), ("Pascual", "Restrepo")))
    idx, names = build(papers)
    assert idx.match(art).tier is None
    idx, names = build(papers, [researcher(4, "Daron", "Acemoglu")], authorship=[(4, 1)])
    assert idx.match(art).tier == "fuzzy"
    # Two words are never enough, even with a shared author.
    idx, names = build([{"id": 2, "title": "Robots"}], [researcher(4, "Daron", "Acemoglu")], authorship=[(4, 2)])
    assert idx.match(art).tier is None
