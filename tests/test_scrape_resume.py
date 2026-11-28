"""Resumable, parallel, stoppable fetch pass (scheduler.run_scrape_job) and the
separate scraper service (backend.pipeline.scrape_worker)."""
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import backend.pipeline.scheduler as scheduler
from backend.pipeline import scrape_worker
from backend.pipeline.fetch_lanes import HostLanes, lane_key


def _row(url_id, url, researcher_id=1):
    return {"id": url_id, "researcher_id": researcher_id, "url": url, "page_type": "HOME"}


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def patched():
    """Patch every database touch point of run_scrape_job."""
    patches = {
        "acquire": patch.object(scheduler, "_acquire_db_lock", return_value=MagicMock(name="lock_conn")),
        "release": patch.object(scheduler, "_release_db_lock"),
        "create_log": patch.object(scheduler, "create_scrape_log", return_value=42),
        "update_log": patch.object(scheduler, "update_scrape_log"),
        "update_progress": patch.object(scheduler, "_update_progress"),
        "mark_checked": patch.object(scheduler, "_mark_urls_checked"),
        "mark_interrupted": patch.object(scheduler, "_mark_interrupted"),
        "execute": patch.object(scheduler, "execute_query"),
        "get_urls": patch.object(scheduler.Researcher, "get_all_researcher_urls", return_value=[]),
        "validate": patch.object(scheduler, "_validate_draft_urls"),
        "fetch": patch.object(scheduler.HTMLFetcher, "fetch_and_save_if_changed", return_value=False),
        "fetch_one": patch.object(scheduler, "fetch_one", return_value=None),
        "merge": patch("backend.enrichment.paper_merge.merge_duplicate_papers"),
        "top5": patch("backend.database.researchers.refresh_all_has_top5", return_value=0),
    }
    mocks = {name: p.start() for name, p in patches.items()}
    yield mocks
    for p in patches.values():
        p.stop()
    scheduler._lock_conn = None
    scheduler._scrape_lock_lost = False


def _checked_ids(mocks):
    return sorted(i for call in mocks["mark_checked"].call_args_list for i in call.args[0])


# ---------------------------------------------------------------------------
# Host lanes
# ---------------------------------------------------------------------------

class TestHostLanes:
    def test_lane_key_is_lowercase_hostname(self):
        assert lane_key("https://Sites.Google.com/view/x") == "sites.google.com"
        assert lane_key("not a url") == ""

    def test_one_in_flight_row_per_host_in_id_order(self):
        lanes = HostLanes([_row(3, "https://a.org/2"), _row(1, "https://a.org/1"),
                           _row(2, "https://b.org/1")])
        first = lanes.take()
        second = lanes.take()
        assert (first["id"], second["id"]) == (1, 2)
        # a.org is busy until row 1 is done, b.org has nothing left.
        got = []
        t = threading.Thread(target=lambda: got.append(lanes.take()))
        t.start()
        time.sleep(0.05)
        assert got == []
        lanes.done(first)
        t.join(1)
        assert got[0]["id"] == 3
        lanes.done(second)
        lanes.done(got[0])
        assert lanes.take() is None
        assert lanes.remaining == 0

    def test_close_releases_waiting_workers(self):
        lanes = HostLanes([_row(1, "https://a.org/1"), _row(2, "https://a.org/2")])
        lanes.take()
        got = []
        t = threading.Thread(target=lambda: got.append(lanes.take()))
        t.start()
        lanes.close()
        t.join(1)
        assert got == [None]
        assert lanes.remaining == 1


# ---------------------------------------------------------------------------
# Concurrency limits
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_workers_bounded_and_one_request_per_host(self, patched):
        rows = [_row(i, f"https://host{i % 6}.org/p{i}") for i in range(1, 37)]
        patched["get_urls"].return_value = rows
        lock = threading.Lock()
        active, active_hosts, peak, overlap = [0], set(), [0], []

        def fetch(url_id, url, researcher_id):
            host = lane_key(url)
            with lock:
                if host in active_hosts:
                    overlap.append(host)
                active_hosts.add(host)
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            with lock:
                active[0] -= 1
                active_hosts.discard(host)
            return url_id % 5 == 0

        patched["fetch"].side_effect = fetch
        with patch.object(scheduler, "SCRAPE_CONCURRENCY", 3):
            status = scheduler.run_scrape_job(stop_event=threading.Event())

        assert status == "completed"
        assert patched["fetch"].call_count == 36
        assert peak[0] == 3
        assert overlap == []
        patched["update_log"].assert_called_once_with(42, "completed", 36, 7)
        assert _checked_ids(patched) == list(range(1, 37))

    def test_concurrency_env_is_clamped(self, monkeypatch):
        monkeypatch.setenv("SCRAPE_CONCURRENCY", "500")
        assert scheduler._env_int("SCRAPE_CONCURRENCY", 6, 1, 32) == 32
        monkeypatch.setenv("SCRAPE_CONCURRENCY", "0")
        assert scheduler._env_int("SCRAPE_CONCURRENCY", 6, 1, 32) == 1
        monkeypatch.setenv("SCRAPE_CONCURRENCY", "x")
        assert scheduler._env_int("SCRAPE_CONCURRENCY", 6, 1, 32) == 6


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

class TestResume:
    def test_new_pass_when_last_completed(self, patched):
        started = _now_naive() - timedelta(hours=30)
        patched["fetch_one"].return_value = {"id": 7, "started_at": started, "status": "completed",
                                             "urls_checked": 10, "urls_changed": 1}
        run = scheduler._start_or_resume_run()
        assert run["id"] == 42 and run["resumed"] is False
        assert patched["create_log"].call_args.kwargs["fetch_run"] is True

    @pytest.mark.parametrize("status", ["interrupted", "failed", "running"])
    def test_resumes_unfinished_pass(self, patched, status):
        started = _now_naive() - timedelta(hours=3)
        patched["fetch_one"].return_value = {"id": 7, "started_at": started, "status": status,
                                             "urls_checked": 120, "urls_changed": 9}
        run = scheduler._start_or_resume_run()
        assert run == {"id": 7, "started_at": started, "urls_checked": 120,
                       "urls_changed": 9, "resumed": True}
        patched["create_log"].assert_not_called()
        sql = patched["execute"].call_args.args[0]
        assert "status = 'running'" in sql and "resumed_count = resumed_count + 1" in sql

    def test_does_not_resume_past_duration_budget(self, patched):
        started = _now_naive() - timedelta(hours=scheduler._MAX_SCRAPE_DURATION_HOURS + 1)
        patched["fetch_one"].return_value = {"id": 7, "started_at": started, "status": "interrupted",
                                             "urls_checked": 120, "urls_changed": 9}
        assert scheduler._start_or_resume_run()["resumed"] is False

    def test_resumed_pass_skips_checked_urls_and_continues_counters(self, patched):
        started = _now_naive() - timedelta(hours=2)
        patched["fetch_one"].return_value = {"id": 7, "started_at": started, "status": "interrupted",
                                             "urls_checked": 100, "urls_changed": 4}
        patched["get_urls"].return_value = [_row(201, "https://a.org"), _row(202, "https://b.org")]
        patched["fetch"].return_value = True

        assert scheduler.run_scrape_job(stop_event=threading.Event()) == "completed"

        patched["get_urls"].assert_called_once_with(checked_before=started)
        patched["update_log"].assert_called_once_with(7, "completed", 102, 6)
        patched["mark_checked"].assert_called()
        assert patched["mark_checked"].call_args.args[1] >= started
        assert _checked_ids(patched) == [201, 202]

    def test_mark_urls_checked_sql(self):
        with patch.object(scheduler, "execute_query") as exec_:
            at = _now_naive()
            scheduler._mark_urls_checked([3, 4], at)
            sql, params = exec_.call_args.args
            assert "scrape_checked_at = %s" in sql and "IN (%s, %s)" in sql
            assert params == (at, 3, 4)
            exec_.reset_mock()
            scheduler._mark_urls_checked([], at)
            exec_.assert_not_called()

    def test_researcher_urls_filtered_by_checked_before(self):
        from backend.researcher import Researcher
        at = _now_naive()
        with patch("backend.researcher.fetch_all", return_value=[]) as fetch_all:
            Researcher.get_all_researcher_urls(checked_before=at)
        sql, params = fetch_all.call_args.args
        assert "(scrape_checked_at IS NULL OR scrape_checked_at < %s)" in sql
        assert params[-1] == at


class TestScrapeDue:
    def test_due_without_any_pass(self):
        with patch.object(scheduler, "fetch_one", return_value=None):
            assert scheduler.scrape_due() == (True, 0.0)

    def test_due_when_resumable(self):
        row = {"id": 1, "started_at": _now_naive() - timedelta(hours=1), "status": "interrupted"}
        with patch.object(scheduler, "fetch_one", return_value=row):
            assert scheduler.scrape_due()[0] is True

    def test_waits_for_interval_after_completed_pass(self):
        row = {"id": 1, "started_at": _now_naive() - timedelta(hours=4), "status": "completed"}
        with patch.object(scheduler, "fetch_one", return_value=row), \
             patch.object(scheduler, "SCRAPE_INTERVAL_HOURS", 24):
            due, wait = scheduler.scrape_due()
        assert due is False
        assert 19.9 * 3600 < wait <= 20 * 3600

    def test_due_after_interval(self):
        row = {"id": 1, "started_at": _now_naive() - timedelta(hours=25), "status": "completed"}
        with patch.object(scheduler, "fetch_one", return_value=row), \
             patch.object(scheduler, "SCRAPE_INTERVAL_HOURS", 24):
            assert scheduler.scrape_due() == (True, 0.0)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

class TestGracefulShutdown:
    def test_stop_finishes_in_flight_and_marks_interrupted(self, patched):
        rows = [_row(i, f"https://h{i}.org") for i in range(1, 11)]
        patched["get_urls"].return_value = rows
        stop = threading.Event()
        started = threading.Event()

        def fetch(url_id, url, researcher_id):
            if url_id == 1:
                started.set()
                stop.set()          # SIGTERM arrives while URL 1 is in flight
                time.sleep(0.1)     # ...which still finishes and is recorded
            return False

        patched["fetch"].side_effect = fetch
        lock_conn = patched["acquire"].return_value
        with patch.object(scheduler, "SCRAPE_CONCURRENCY", 1):
            status = scheduler.run_scrape_job(stop_event=stop)

        assert status == "interrupted"
        assert patched["fetch"].call_count == 1
        assert _checked_ids(patched) == [1]
        patched["mark_interrupted"].assert_called_once_with(42, 1, 0)
        patched["update_log"].assert_not_called()
        patched["validate"].assert_not_called()
        patched["merge"].assert_not_called()
        patched["release"].assert_called_once_with(lock_conn)

    def test_stop_before_any_fetch(self, patched):
        patched["get_urls"].return_value = [_row(1, "https://a.org")]
        stop = threading.Event()
        stop.set()
        assert scheduler.run_scrape_job(stop_event=stop) == "interrupted"
        patched["fetch"].assert_not_called()

    def test_grace_period_bounds_the_wait(self, patched):
        patched["get_urls"].return_value = [_row(1, "https://a.org"), _row(2, "https://b.org")]
        stop = threading.Event()
        release = threading.Event()

        def fetch(url_id, url, researcher_id):
            stop.set()
            release.wait(5)  # a hung fetch
            return False

        patched["fetch"].side_effect = fetch
        with patch.object(scheduler, "_SCRAPE_STOP_GRACE_SECONDS", 0.2), \
             patch.object(scheduler, "SCRAPE_CONCURRENCY", 2):
            t0 = time.time()
            status = scheduler.run_scrape_job(stop_event=stop)
            elapsed = time.time() - t0
        release.set()
        assert status == "interrupted"
        assert elapsed < 3
        assert _checked_ids(patched) == []  # unfinished URLs are fetched again on resume

    def test_shutdown_scheduler_signals_running_scrape(self):
        scheduler._scrape_stop_event.clear()
        with patch.object(scheduler, "_scheduler", None), \
             patch.object(scheduler, "_scheduler_lock_conn", None):
            scheduler.shutdown_scheduler()
        assert scheduler._scrape_stop_event.is_set()
        scheduler._scrape_stop_event.clear()


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

class TestLock:
    def test_returns_none_when_lock_held_elsewhere(self, patched):
        patched["acquire"].return_value = None
        assert scheduler.run_scrape_job(stop_event=threading.Event()) is None
        patched["create_log"].assert_not_called()
        patched["fetch"].assert_not_called()

    def test_lost_lock_stops_workers_and_fails(self, patched):
        patched["get_urls"].return_value = [_row(i, f"https://h{i}.org") for i in range(1, 20)]

        def lose(url_id, url, researcher_id):
            with scheduler._lock_conn_guard:
                scheduler._lock_conn = None
                scheduler._scrape_lock_lost = True
            return False

        patched["fetch"].side_effect = lose
        with patch.object(scheduler, "SCRAPE_CONCURRENCY", 1):
            assert scheduler.run_scrape_job(stop_event=threading.Event()) == "failed"
        assert patched["fetch"].call_count == 1
        patched["release"].assert_not_called()
        assert "lock was lost" in patched["update_log"].call_args.kwargs["error_message"]


class TestApiScheduling:
    def _start(self, scrape_in_api, on_startup=False):
        with patch.object(scheduler.mysql.connector, "connect") as connect, \
             patch.object(scheduler, "_cleanup_stale_scrape_logs"), \
             patch.object(scheduler, "BackgroundScheduler") as bg, \
             patch.object(scheduler, "_start_lock_keepalive"), \
             patch.object(scheduler, "threading") as threading_mod, \
             patch.object(scheduler.signal, "signal"), \
             patch.object(scheduler, "SCRAPE_IN_API", scrape_in_api), \
             patch.object(scheduler, "SCRAPE_ON_STARTUP", on_startup), \
             patch.object(scheduler, "ENRICHMENT_WORKER_ENABLED", False), \
             patch.object(scheduler, "EXTRACTION_WORKER_ENABLED", False), \
             patch.object(scheduler, "DIGEST_ENABLED", False), \
             patch.object(scheduler, "_scheduler", None), \
             patch.object(scheduler, "_scheduler_lock_conn", None):
            cursor = MagicMock()
            cursor.fetchone.return_value = (1,)
            connect.return_value.cursor.return_value = cursor
            scheduler.start_scheduler()
            job_ids = [c.kwargs.get("id") for c in bg.return_value.add_job.call_args_list]
            threads = [c.kwargs.get("target") for c in threading_mod.Thread.call_args_list]
        return job_ids, threads

    def test_api_schedules_no_scrape_when_service_owns_it(self):
        job_ids, threads = self._start(scrape_in_api=False, on_startup=True)
        assert "scrape_job" not in job_ids
        assert "stale_scrape_cleanup" in job_ids
        assert scheduler._run_scrape_if_due not in threads

    def test_api_startup_resumes_instead_of_new_pass(self):
        job_ids, threads = self._start(scrape_in_api=True, on_startup=True)
        assert "scrape_job" in job_ids
        assert threads == [scheduler._run_scrape_if_due]

    def test_run_if_due_skips_when_not_due(self):
        with patch.object(scheduler, "scrape_due", return_value=(False, 100.0)), \
             patch.object(scheduler, "run_scrape_job") as run:
            scheduler._run_scrape_if_due()
            run.assert_not_called()
        with patch.object(scheduler, "scrape_due", return_value=(True, 0.0)), \
             patch.object(scheduler, "run_scrape_job") as run:
            scheduler._run_scrape_if_due()
            run.assert_called_once()


# ---------------------------------------------------------------------------
# Scraper service loop
# ---------------------------------------------------------------------------

class TestScrapeWorker:
    def _run(self, due_values, statuses, schema=(True,)):
        stop = threading.Event()
        waits = []
        due_iter, status_iter, schema_iter = iter(due_values), iter(statuses), iter(schema)

        def fake_wait(timeout=None):
            waits.append(timeout)
            if len(waits) > 10:
                stop.set()
            return stop.is_set()

        def run_job(stop_event):
            assert stop_event is stop
            status = next(status_iter, "stop")
            if status == "stop":
                stop.set()
                return "interrupted"
            return status

        def due():
            value = next(due_iter, None)
            if value is None:
                stop.set()
                return False, 0.0
            return value

        stop.wait = fake_wait
        with patch.object(scrape_worker, "schema_ready", side_effect=lambda: next(schema_iter, True)), \
             patch.object(scheduler, "scrape_due", side_effect=due), \
             patch.object(scheduler, "run_scrape_job", side_effect=run_job) as run:
            scrape_worker.run_worker(stop, poll_seconds=300)
        return run, waits

    def test_runs_due_pass_then_sleeps_until_next(self):
        run, waits = self._run([(True, 0.0), (False, 7200.0)], ["completed"])
        assert run.call_count == 1
        assert waits[0] == 300  # capped by the poll interval

    def test_waits_when_another_process_holds_the_lock(self):
        run, waits = self._run([(True, 0.0)], [None])
        assert waits[0] == scrape_worker._LOCK_BUSY_WAIT_SECONDS

    def test_backs_off_after_failed_pass(self):
        run, waits = self._run([(True, 0.0)], ["failed"])
        assert waits[0] == scheduler._FAILED_RUN_RETRY_SECONDS

    def test_waits_for_schema_before_scraping(self):
        run, waits = self._run([(True, 0.0)], ["completed"], schema=(False, False, True))
        assert waits[:2] == [scrape_worker._SCHEMA_WAIT_SECONDS] * 2
        assert run.call_count == 1

    def test_stop_signal_interrupts_the_running_pass(self):
        run, waits = self._run([(True, 0.0)], ["stop"])
        assert run.call_count == 1
        assert waits == []


# ---------------------------------------------------------------------------
# Fetcher thread safety
# ---------------------------------------------------------------------------

class TestDnsPinSharing:
    def test_pin_survives_until_last_user_exits(self):
        from backend.pipeline import html_fetcher as hf
        with hf._pin_dns("example.org", "93.184.216.34"):
            with hf._pin_dns("example.org", "93.184.216.34"):
                pass
            assert hf._pin_dns_pins.get("example.org") == "93.184.216.34"
        assert "example.org" not in hf._pin_dns_pins
        assert hf._pin_dns_refcount == 0


# ---------------------------------------------------------------------------
# deploy.sh restarts the scraper when code it runs changes
# ---------------------------------------------------------------------------

class TestDeployScraperPaths:
    def _paths(self):
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh").read_text()
        block = re.search(r"SCRAPER_PATHS=\((.*?)\)", text, re.S).group(1)
        return block.split()

    def test_paths_cover_the_scraper_import_closure(self):
        paths = self._paths()
        missing = sorted(p for p in _scraper_closure()
                         if not any(p == q or p.startswith(q.rstrip("/") + "/") for q in paths))
        assert missing == []

    def test_paths_exist(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        assert [p for p in self._paths() if not (root / p).exists()] == []


def _scraper_closure() -> set[str]:
    """backend files the scraper process imports, measured in a fresh interpreter."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = (
        "import importlib, json, sys\n"
        "for n in ('backend.pipeline.scrape_worker', 'backend.enrichment.paper_merge',\n"
        "          'backend.enrichment.rename_merge', 'backend.enrichment.doi_resolver',\n"
        "          'backend.pipeline.feed_events'):\n"
        "    importlib.import_module(n)\n"
        "print(json.dumps(sorted({m.__file__ for k, m in sys.modules.items()\n"
        "      if k.startswith('backend') and getattr(m, '__file__', None)})))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=root, env=dict(os.environ),
                         capture_output=True, text=True, check=True).stdout
    return {Path(f).resolve().relative_to(root).as_posix() for f in json.loads(out.splitlines()[-1])}
