"""Paced backfill of missing bios (issue #258 follow-up)."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

from unittest.mock import patch

import pytest

from backend.pipeline import bio_backfill
from backend.pipeline.description import DescriptionResult

BIO = "Jane Doe is a professor of economics at MIT who studies labor markets."


def _cand(rid, url_id=None):
    return {"researcher_id": rid, "url_id": url_id or rid * 10,
            "url": f"https://r{rid}.org", "content_hash": f"h{rid}", "n_pubs": 5}


@pytest.fixture
def env():
    """Patch DB access; yields the mocks and a results list for request_description."""
    results = []
    with patch.object(bio_backfill, "count_backfill_calls_since", return_value=0) as count, \
         patch.object(bio_backfill, "count_extraction_backlog", return_value=0) as backlog, \
         patch.object(bio_backfill, "get_last_llm_quota_limit", return_value=None) as quota, \
         patch.object(bio_backfill, "get_backfill_candidates") as cands, \
         patch.object(bio_backfill, "fetch_one") as fetch_one, \
         patch.object(bio_backfill, "_record_attempt") as record, \
         patch.object(bio_backfill, "append_researcher_snapshot") as snap, \
         patch("backend.pipeline.description.request_description",
               side_effect=lambda *a, **k: results.pop(0)) as req, \
         patch.object(bio_backfill._stop_event, "wait", return_value=False) as wait:
        def fake_fetch_one(sql, params):
            if "FROM html_content" in sql:
                return {"content": "Home page text about Jane Doe", "content_hash": f"h{params[0]}"}
            return {"position": "Professor", "affiliation": "MIT", "description": None}
        fetch_one.side_effect = fake_fetch_one
        yield {"quota": quota, "backlog": backlog, "count": count, "cands": cands, "record": record, "snap": snap,
               "req": req, "wait": wait, "results": results, "fetch_one": fetch_one}


def _run(**kw):
    kw.setdefault("use_lock", False)
    return bio_backfill.run_bio_backfill_batch(**kw)


def test_saves_clean_bio_and_records_attempt(env):
    env["cands"].return_value = [_cand(1)]
    env["results"].append(DescriptionResult(description=BIO))
    stats = _run(max_calls=5, daily_limit=100, delay_seconds=0)
    assert stats["saved"] == 1 and stats["llm_calls"] == 1
    env["snap"].assert_called_once_with(1, "Professor", "MIT", BIO, source_url="https://r1.org")
    assert env["record"].call_args.args[3] == "saved"


def test_stops_at_first_quota_refusal(env):
    env["cands"].return_value = [_cand(1), _cand(2), _cand(3)]
    env["results"].extend([DescriptionResult(description=BIO),
                           DescriptionResult(retry_after=60.0, failed=True)])
    stats = _run(max_calls=5, daily_limit=100, delay_seconds=0)
    assert stats["stopped"] == "rate_limited"
    assert env["req"].call_count == 2
    # The refused researcher is not marked as tried, so it is picked up next run.
    assert [c.args[0] for c in env["record"].call_args_list] == [1]


def test_daily_limit_caps_the_batch(env):
    env["count"].return_value = 148
    env["cands"].return_value = [_cand(1), _cand(2)]
    env["results"].extend([DescriptionResult(), DescriptionResult()])
    _run(max_calls=10, daily_limit=150, delay_seconds=0)
    assert env["cands"].call_args.args[0] == 2


def test_daily_limit_reached_makes_no_calls(env):
    env["count"].return_value = 150
    stats = _run(max_calls=10, daily_limit=150, delay_seconds=0)
    assert stats["stopped"] == "daily_limit"
    env["cands"].assert_not_called()
    env["req"].assert_not_called()


def test_never_overwrites_a_bio_saved_meanwhile(env):
    env["cands"].return_value = [_cand(1)]
    env["results"].append(DescriptionResult(description=BIO))
    env["fetch_one"].side_effect = lambda sql, params: (
        {"content": "text", "content_hash": "h1"} if "FROM html_content" in sql
        else {"position": None, "affiliation": None, "description": "Existing bio."})
    stats = _run(max_calls=5, daily_limit=100, delay_seconds=0)
    env["snap"].assert_not_called()
    assert stats["skipped_has_bio"] == 1


def test_rejected_and_empty_answers_are_recorded_not_saved(env):
    env["cands"].return_value = [_cand(1), _cand(2)]
    env["results"].extend([DescriptionResult(rejected=True), DescriptionResult()])
    stats = _run(max_calls=5, daily_limit=100, delay_seconds=0)
    env["snap"].assert_not_called()
    assert [c.args[3] for c in env["record"].call_args_list] == ["rejected", "no_bio"]
    assert stats["rejected"] == 1 and stats["no_bio"] == 1


def test_consecutive_failures_stop_the_run(env):
    env["cands"].return_value = [_cand(i) for i in range(1, 6)]
    env["results"].extend([DescriptionResult(failed=True) for _ in range(5)])
    stats = _run(max_calls=5, daily_limit=100, delay_seconds=0)
    assert stats["stopped"] == "failures"
    assert env["req"].call_count == 3


def test_paces_calls_with_delay(env):
    env["cands"].return_value = [_cand(1), _cand(2)]
    env["results"].extend([DescriptionResult(), DescriptionResult()])
    _run(max_calls=5, daily_limit=100, delay_seconds=30)
    env["wait"].assert_called_once_with(30)


def test_candidates_dedupe_researchers_with_two_home_pages():
    rows = [_cand(1, 10), _cand(1, 11), _cand(2, 20)]
    with patch.object(bio_backfill, "fetch_all", return_value=rows):
        from datetime import datetime
        out = bio_backfill.get_backfill_candidates(5, datetime(2026, 11, 27))
    assert [(r["researcher_id"], r["url_id"]) for r in out] == [(1, 10), (2, 20)]


def test_scheduler_job_is_off_by_default():
    from backend.pipeline import scheduler
    if os.environ.get("BIO_BACKFILL_ENABLED") is None:
        assert scheduler.BIO_BACKFILL_ENABLED is False


def test_yields_while_extraction_backlog_is_large(env):
    env["backlog"].return_value = 51
    stats = _run(max_calls=10, daily_limit=150, delay_seconds=0, max_backlog=50)
    assert stats["stopped"] == "extraction_backlog"
    env["cands"].assert_not_called()
    env["req"].assert_not_called()


def test_runs_when_backlog_is_small_or_check_disabled(env):
    env["backlog"].return_value = 5000
    env["cands"].return_value = [_cand(1)]
    env["results"].append(DescriptionResult(description=BIO))
    stats = _run(max_calls=10, daily_limit=150, delay_seconds=0, max_backlog=-1)
    assert stats["saved"] == 1
    env["backlog"].assert_not_called()


def test_yields_after_recent_extraction_quota_refusal(env):
    env["quota"].return_value = bio_backfill._utcnow()
    stats = _run(max_calls=10, daily_limit=150, delay_seconds=0, max_backlog=50)
    assert stats["stopped"] == "extraction_quota"
    env["req"].assert_not_called()


def test_stops_mid_run_when_extraction_is_refused(env):
    from datetime import timedelta
    env["cands"].return_value = [_cand(1), _cand(2)]
    env["results"].append(DescriptionResult(description=BIO))
    old = bio_backfill._utcnow() - timedelta(hours=2)
    env["quota"].side_effect = [old, bio_backfill._utcnow()]  # pre-run, then before call 2
    stats = _run(max_calls=10, daily_limit=150, delay_seconds=0, max_backlog=50)
    assert stats["stopped"] == "extraction_quota"
    assert env["req"].call_count == 1
