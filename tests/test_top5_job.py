"""Weekly top-5 coverage job (backend/coverage/top5_job.py): streaming index and storage."""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import backend.coverage.top5_job as job
import backend.coverage.top5_validation as v


def article(doi, title, authors, journal="aer", year=2022, subtitle=""):
    return v.Article(doi=doi, journal=journal, year=year, title=title, subtitle=subtitle,
                     authors=[v.Author(g, f) for g, f in authors])


ARTICLES = [
    # DOI match through papers.doi
    article("10.1257/aer.1", "Trade with Correlation", [("Jane", "Doe")]),
    # exact title match, still a working paper
    article("10.1257/aer.2", "Robots and Jobs: Evidence from US Labor Markets", [("Daron", "Acemoglu")]),
    # DOI match only through a paper link
    article("10.1086/jpe.3", "A Completely Renamed Journal Version", [("Ann", "Smith")], journal="jpe"),
    # fuzzy match (typo) that needs the shared author surname
    article("10.1093/qje.4", "Monetary Policy Transmission through Bank Balance Sheets in Europe",
            [("Luc", "Laeven")], journal="qje"),
    # tracked author, no paper (b)
    article("10.1093/restud.5", "Information Frictions in Housing Search", [("Lena", "Muller")],
            journal="restud"),
    # no tracked author (a)
    article("10.3982/ecta.6", "Identification of Auction Models", [("Pat", "Nobody")], journal="ecta"),
    # duplicate: matched by DOI and by title to two papers
    article("10.1257/aer.7", "Credit Cycles and Firm Dynamics", [("Jane", "Doe")]),
]

PAPERS = [
    {"id": 1, "title": "Trade with Correlation", "doi": "https://doi.org/10.1257/AER.1",
     "status": "published", "venue": "American Economic Review", "year": "2022"},
    {"id": 2, "title": "Robots and Jobs: Evidence from US Labor Markets", "status": "working_paper"},
    {"id": 3, "title": "Old Working Paper Title on Something", "status": "published", "venue": "JPE"},
    {"id": 4, "title": "Monetary Policy Transmision through Bank Balance Sheets in Europe",
     "status": "published", "venue": "Quarterly Journal of Economics"},
    {"id": 5, "title": "Credit Cycles and Firm Dynamics", "status": "working_paper"},
    {"id": 6, "title": "Unrelated", "doi": "10.1257/aer.7", "status": "published",
     "venue": "American Economic Review"},
    {"id": 7, "title": "A Paper Nobody Asked About Monetary Things", "status": "working_paper"},
    {"id": 8, "title": None},
]
LINKS = [{"paper_id": 3, "doi": None, "url": "https://doi.org/10.1086/jpe.3"},
         {"paper_id": 7, "doi": "10.9999/other", "url": None}]
AUTHORSHIP = [{"r": 1, "p": 1}, {"r": 2, "p": 2}, {"r": 3, "p": 3}, {"r": 4, "p": 4},
              {"r": 1, "p": 5}, {"r": 1, "p": 6}, {"r": 9, "p": 7}]
RESEARCHERS = [
    {"id": 1, "first": "Jane", "last": "Doe", "n_urls": 1},
    {"id": 2, "first": "Daron", "last": "Acemoglu", "n_urls": 2},
    {"id": 3, "first": "Ann", "last": "Smith", "n_urls": 1},
    {"id": 4, "first": "Luc", "last": "Laeven", "n_urls": 1},
    {"id": 5, "first": "Lena", "last": "Müller", "n_urls": 1},
    {"id": 6, "first": "Pat", "last": "Nobody", "n_urls": 0},
    {"id": 9, "first": "Zed", "last": "Unrelated", "n_urls": 3},
]


def source():
    return job.RowsSource(PAPERS, LINKS, AUTHORSHIP, RESEARCHERS)


def full_summary():
    """What the validation script computes with everything in memory."""
    researchers = [v.Researcher(r["id"], r["first"], r["last"], r.get("oa"), r["n_urls"]) for r in RESEARCHERS]
    names = v.NameIndex(researchers)
    index = v.PaperIndex([p for p in PAPERS if p["title"] is not None], LINKS, AUTHORSHIP, names)
    return v.summarize([v.classify_article(a, index, names) for a in ARTICLES], [])


def test_streaming_index_matches_the_full_index():
    gt = v.GroundTruth(list(ARTICLES), [])
    lean = job.compute_coverage(source(), from_year=2020, until_year=2026, ground_truth=gt)
    full = full_summary()
    for key in ("overall", "by_journal", "by_year"):
        assert lean[key] == full[key]
    o = lean["overall"]
    # every matching tier is exercised
    assert (o["articles"], o["observed"], o["tier_doi"], o["tier_title"], o["tier_fuzzy"]) == (7, 5, 3, 1, 1)
    assert (o["a_no_tracked_author"], o["b_tracked_absent"], o["d_duplicates"]) == (1, 1, 1)


def test_streaming_index_keeps_only_candidates():
    index, names, stats = job.build_indexes(ARTICLES, source())
    assert stats["papers"] == 7  # paper 8 has no title
    assert 8 not in index.norm
    assert 9 not in names.researchers  # no Crossref author shares a surname key
    assert set(names.researchers) == {1, 2, 3, 4, 5, 6}
    assert index.by_doi["10.1086/jpe.3"] == {3}  # attached through the link
    assert "10.9999/other" not in index.by_doi


def test_streaming_index_loads_paper_details_and_authors_lazily():
    src = source()
    src.paper = MagicMock(side_effect=src.paper)
    src.authors_of = MagicMock(side_effect=src.authors_of)
    index, names, _ = job.build_indexes(ARTICLES, src)
    assert src.paper.call_count == 0 and src.authors_of.call_count == 0
    [v.classify_article(a, index, names) for a in ARTICLES]
    loaded = {c.args[0] for c in src.paper.call_args_list}
    assert loaded <= {1, 2, 3, 4, 5, 6}
    assert len(src.paper.call_args_list) == len(loaded)  # cached


def test_fuzzy_candidate_tokens_are_deterministic():
    freq = {"monetary": 5, "policy": 5, "transmission": 5, "balance": 2, "sheets": 50000}.get
    target = "monetary policy transmission through bank balance sheets"
    toks = v.fuzzy_candidate_tokens(target, lambda t: freq(t, 0))
    assert toks == ["balance", "monetary", "transmission"]


# ---------------------------------------------------------------------------
# Storage and scheduling
# ---------------------------------------------------------------------------

def summary():
    gt = v.GroundTruth(list(ARTICLES), [])
    return job.compute_coverage(source(), from_year=2020, until_year=2026, ground_truth=gt)


def test_store_run_writes_headline_counts_and_summary():
    s = summary()
    with patch.object(job, "execute_query") as eq:
        job.store_run(s, 42, computed_at=datetime(2026, 11, 29, 4, 30))
    sql, params = eq.call_args.args
    assert "INSERT INTO top5_coverage_runs" in sql
    assert params[:7] == (datetime(2026, 11, 29, 4, 30), 2020, 2026, 7, 5, 6, 5)
    stored = json.loads(params[7])
    assert set(stored) == {"overall", "by_journal", "by_year", "params"}
    assert params[8] == 42


@pytest.mark.parametrize("raw", ['{"overall": {"articles": 7}}', b'{"overall": {"articles": 7}}',
                                 {"overall": {"articles": 7}}])
def test_latest_run_decodes_json(raw):
    with patch.object(job, "fetch_one", return_value={"computed_at": datetime(2026, 11, 29), "summary": raw}):
        assert job.latest_run()["summary"] == {"overall": {"articles": 7}}


def test_latest_run_none_when_empty():
    with patch.object(job, "fetch_one", return_value=None):
        assert job.latest_run() is None


def test_is_due():
    now = datetime(2026, 11, 29, 4, 30)
    with patch.object(job, "latest_run", return_value=None):
        assert job.is_due(now)
    with patch.object(job, "latest_run", return_value={"computed_at": now - timedelta(days=7)}):
        assert not job.is_due(now)
    with patch.object(job, "latest_run", return_value={"computed_at": now - timedelta(days=8)}):
        assert job.is_due(now)


def _lock_conn(got_lock: int):
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (got_lock,)
    return conn


def test_run_skips_when_recent_result_exists():
    with (patch.object(job, "is_due", return_value=False),
          patch.object(job, "compute_coverage") as cc):
        assert job.run_top5_coverage_job() is None
    cc.assert_not_called()


def test_run_skips_when_another_run_holds_the_lock():
    with (patch.object(job, "is_due", return_value=True),
          patch.object(job, "get_connection", return_value=_lock_conn(0)),
          patch.object(job, "compute_coverage") as cc):
        assert job.run_top5_coverage_job() is None
    cc.assert_not_called()


def test_run_computes_and_stores():
    s = summary()
    conn = _lock_conn(1)
    with (patch.object(job, "is_due", return_value=True),
          patch.object(job, "get_connection", return_value=conn),
          patch.object(job, "compute_coverage", return_value=s),
          patch.object(job, "store_run") as store):
        assert job.run_top5_coverage_job() is s
    store.assert_called_once()
    executed = [c.args[0] for c in conn.cursor.return_value.execute.call_args_list]
    assert any("RELEASE_LOCK" in sql for sql in executed)
    conn.close.assert_called_once()


def test_run_dry_run_does_not_store():
    with (patch.object(job, "get_connection", return_value=_lock_conn(1)),
          patch.object(job, "compute_coverage", return_value=summary()),
          patch.object(job, "store_run") as store):
        assert job.run_top5_coverage_job(force=True, store=False) is not None
    store.assert_not_called()


def test_run_never_raises():
    conn = _lock_conn(1)
    with (patch.object(job, "is_due", return_value=True),
          patch.object(job, "get_connection", return_value=conn),
          patch.object(job, "compute_coverage", side_effect=RuntimeError("crossref down")),
          patch.object(job, "store_run") as store):
        assert job.run_top5_coverage_job() is None
    store.assert_not_called()
    conn.close.assert_called_once()
