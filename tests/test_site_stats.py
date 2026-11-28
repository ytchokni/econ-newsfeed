"""GET /api/stats and backend/database/site_stats.py (About page numbers)."""
import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.database.site_stats as ss

NOW = datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc)  # a Friday


class TestWeeklyActivity:
    def test_buckets_events_into_utc_weeks_oldest_first(self):
        rows = [
            {"day": date(2026, 11, 23), "event_type": "new_paper", "n": 10},        # current week (Mon)
            {"day": datetime(2026, 11, 27), "event_type": "status_change", "n": 3},  # current week
            {"day": "2026-11-22", "event_type": "new_paper", "n": 7},              # previous week (Sun)
            {"day": date(2026, 11, 16), "event_type": "title_change", "n": 2},      # previous week
            {"day": date(2026, 9, 1), "event_type": "new_paper", "n": 99},          # before the window
        ]
        with patch.object(ss, "fetch_all", return_value=rows) as fa:
            weeks = ss.weekly_activity(NOW, weeks=12)
        assert fa.call_args.args[1] == (datetime(2026, 9, 7),)  # Monday 11 weeks before this one
        assert len(weeks) == 12
        assert weeks[0]["week_start"] == "2026-09-07"
        assert weeks[-1] == {"week_start": "2026-11-23", "partial": True, "new_papers": 10,
                             "status_changes": 3, "events": 13}
        assert weeks[-2] == {"week_start": "2026-11-16", "partial": False, "new_papers": 7,
                             "status_changes": 0, "events": 9}
        assert sum(w["events"] for w in weeks) == 22
        assert [w["partial"] for w in weeks].count(True) == 1

    def test_empty(self):
        with patch.object(ss, "fetch_all", return_value=[]):
            weeks = ss.weekly_activity(NOW, weeks=4)
        assert [w["new_papers"] for w in weeks] == [0, 0, 0, 0]


LATEST = {
    "computed_at": datetime(2026, 11, 29, 4, 31, 12),
    "from_year": 2020, "until_year": 2026,
    "articles": 3011, "observed": 2131, "tracked_articles": 2726, "tracked_observed": 2114,
    "summary": {
        "overall": {"observed_correct": 2057},
        "by_journal": {
            "other": {"articles": 5, "observed": 1, "coverage_pct": 20.0, "coverage_tracked_pct": None},
            "ecta": {"articles": 400, "observed": 280, "coverage_pct": 70.0, "coverage_tracked_pct": None},
            "aer": {"articles": 800, "observed": 600, "coverage_pct": 75.0, "coverage_tracked_pct": 80.0},
        },
    },
}


class TestTop5Coverage:
    def test_public_shape_from_latest_run(self):
        with patch("backend.coverage.top5_job.latest_run", return_value=LATEST):
            t = ss.top5_coverage()
        assert t["as_of"] == "2026-11-29T04:31:12Z"
        assert (t["articles"], t["found"], t["coverage_pct"]) == (3011, 2131, 70.8)
        assert (t["tracked_articles"], t["tracked_found"], t["coverage_tracked_pct"]) == (2726, 2114, 77.5)
        assert t["correct_record_pct"] == 68.3
        assert t["by_journal"][0] == {"journal": "aer", "name": "American Economic Review", "articles": 800,
                                      "found": 600, "coverage_pct": 75.0, "coverage_tracked_pct": 80.0}
        assert t["by_journal"][1]["coverage_tracked_pct"] is None
        # only the five journals, in the fixed AER, ECTA, JPE, QJE, REStud order
        assert [j["journal"] for j in t["by_journal"]] == ["aer", "ecta"]

    def test_none_before_first_run(self):
        with patch("backend.coverage.top5_job.latest_run", return_value=None):
            assert ss.top5_coverage() is None

    def test_none_when_table_unreadable(self):
        with patch("backend.coverage.top5_job.latest_run", side_effect=RuntimeError("no table")):
            assert ss.top5_coverage() is None


def _fake_fetch_one(sql, params=None):
    if "researchers_total" in sql:
        return {"researchers_total": 166174, "researchers_tracked": 41000, "pages_tracked": 52000,
                "papers_total": 259794}
    if "scrape_log" in sql:
        return {"t": datetime(2026, 11, 26, 6, 0)}
    if "html_content" in sql:
        return {"t": datetime(2026, 11, 27, 14, 59, 1)}
    raise AssertionError(sql)


def _fake_fetch_all(sql, params=None):
    if "FROM papers GROUP BY status" in sql:
        return [{"status": "published", "n": 100}, {"status": "accepted", "n": 10},
                {"status": "revise_and_resubmit", "n": 5}, {"status": "reject_and_resubmit", "n": 1},
                {"status": "working_paper", "n": 50}, {"status": "work_in_progress", "n": 4},
                {"status": None, "n": 3}]
    if "feed_events" in sql:
        return [{"day": date(2026, 11, d), "event_type": "new_paper", "n": 7 * i}
                for i, d in enumerate((2, 9, 16, 23), start=1)]
    raise AssertionError(sql)


class TestGetSiteStats:
    def test_shape(self):
        with (patch.object(ss, "fetch_one", _fake_fetch_one), patch.object(ss, "fetch_all", _fake_fetch_all),
              patch.object(ss, "top5_coverage", return_value=None),
              patch.object(ss, "_fetch_interval_hours", return_value=48)):
            s = ss.get_site_stats(NOW)
        assert s["generated_at"] == "2026-11-27T15:00:00Z"
        assert s["researchers"] == {"total": 166174, "tracked": 41000, "pages_tracked": 52000}
        assert s["papers"] == {"total": 259794, "by_status": {
            "working_paper": 54, "under_review": 6, "accepted": 10, "published": 100, "unknown": 3}}
        assert len(s["activity"]["weeks"]) == ss.WEEKS
        # average over the last 4 full weeks (the current, partial week is left out): 0, 7, 14, 21
        assert s["activity"]["new_papers_per_week"] == 10
        assert s["top5"] is None
        assert s["fetch_interval_hours"] == 48
        assert s["last_fetch_at"] == "2026-11-26T06:00:00Z"
        assert s["last_extraction_at"] == "2026-11-27T14:59:01Z"
        json.dumps(s)  # serialisable as is


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

        api_mod._site_stats_cache.clear()
        api_mod.limiter.reset()
        with TestClient(api_mod.app) as c:
            yield c
        api_mod._site_stats_cache.clear()
        api_mod.limiter.reset()


class TestStatsEndpoint:
    def test_returns_stats_and_caches_them(self, client):
        payload = {"generated_at": "2026-11-27T15:00:00Z", "top5": None}
        with patch("backend.api.get_site_stats", return_value=payload) as gs:
            first = client.get("/api/stats")
            second = client.get("/api/stats")
        assert first.status_code == 200 and second.status_code == 200
        assert first.json() == payload == second.json()
        assert gs.call_count == 1
        assert "max-age=900" in first.headers["cache-control"]

    def test_not_a_data_path(self):
        # Aggregates only: scraper-UA blocking and API_DIRECT_ACCESS=deny do not apply.
        from backend.api_protection import DATA_PATHS

        assert not any("/api/stats".startswith(p) for p in DATA_PATHS)
