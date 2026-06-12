"""Tests for scheduler.run_scrape_job() and related helpers.

Covers the scrape orchestration logic: locking, log creation/update,
URL processing with change detection, error handling, and lock release.
All external dependencies (DB, network, LLM) are mocked.
"""
from unittest.mock import MagicMock, patch, call

import pytest

import scheduler
from scheduler import (
    run_scrape_job, create_scrape_log, update_scrape_log,
    _cleanup_stale_scrape_logs,
    _enrichment_worker_loop, start_enrichment_worker, stop_enrichment_worker,
    _extraction_worker_loop, start_extraction_worker, stop_extraction_worker,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_url_row(url_id=1, researcher_id=10, url="https://example.com/pubs",
                  page_type="PUBLICATIONS"):
    """Return a dict that mimics Researcher.get_all_researcher_urls() rows."""
    return {
        "id": url_id,
        "researcher_id": researcher_id,
        "url": url,
        "page_type": page_type,
    }


def _base_patches():
    """Return a dict of common patch targets pre-configured with safe defaults."""
    return {
        "acquire": patch("scheduler._acquire_db_lock", return_value=MagicMock(name="lock_conn")),
        "release": patch("scheduler._release_db_lock"),
        "create_log": patch("scheduler.create_scrape_log", return_value=42),
        "update_log": patch("scheduler.update_scrape_log"),
        "update_progress": patch("scheduler._update_progress"),
        "get_urls": patch("scheduler.Researcher.get_all_researcher_urls", return_value=[]),
        "validate": patch("scheduler._validate_draft_urls"),
        "fetch": patch("scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=False),
        "fetch_one": patch("scheduler.fetch_one", return_value=None),
    }


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

class TestRunScrapeJobHappyPath:
    """Full scrape completes with no errors; log status is 'completed'."""

    def test_happy_path_no_urls(self):
        """Scrape with zero URLs still creates log and marks completed."""
        patches = _base_patches()
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["acquire"].assert_called_once()
            mocks["create_log"].assert_called_once()
            mocks["update_log"].assert_called_once_with(42, "completed", 0, 0)
            mocks["validate"].assert_called_once()
            mocks["release"].assert_called_once()
        finally:
            for p in patches.values():
                p.stop()

    def test_scrape_does_not_call_enrichment(self):
        """run_scrape_job no longer triggers OpenAlex enrichment."""
        patches = _base_patches()
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            with patch("openalex.enrich_new_publications") as mock_enrich:
                run_scrape_job()
                mock_enrich.assert_not_called()
        finally:
            for p in patches.values():
                p.stop()

    def test_happy_path_with_changed_url(self):
        """A changed URL increments urls_changed; no extraction happens in the scrape job."""
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls",
            return_value=[_make_url_row()])
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True)
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()
        finally:
            for p in patches.values():
                p.stop()
        args = mocks["update_log"].call_args[0]
        assert args[1] == "completed"
        assert args[2] == 1  # urls_checked
        assert args[3] == 1  # urls_changed


# ---------------------------------------------------------------------------
# 2. Lock already held
# ---------------------------------------------------------------------------

class TestLockAlreadyHeld:
    """When the advisory lock cannot be acquired, scrape is skipped entirely."""

    def test_skips_when_lock_unavailable(self):
        with patch("scheduler._acquire_db_lock", return_value=None) as mock_acq, \
             patch("scheduler.create_scrape_log") as mock_log, \
             patch("scheduler._release_db_lock") as mock_rel:

            run_scrape_job()

            mock_acq.assert_called_once()
            mock_log.assert_not_called()
            mock_rel.assert_not_called()


# ---------------------------------------------------------------------------
# 2b. Lock connection keepalive
# ---------------------------------------------------------------------------

class TestLockConnectionKeepalive:
    """The lock connection idles while held — it must outlive MySQL's default
    8h wait_timeout or the lock drops mid-scrape and the zombie cleanup
    falsely fails the live scrape row."""

    @patch("scheduler.mysql.connector.connect")
    def test_acquire_raises_session_wait_timeout(self, mock_connect):
        cursor = mock_connect.return_value.cursor.return_value
        cursor.fetchone.return_value = (1,)

        conn = scheduler._acquire_db_lock()

        assert conn is mock_connect.return_value
        executed = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("wait_timeout" in q for q in executed)
        # keepalive must be set before the lock is taken
        assert "wait_timeout" in executed[0]
        assert "GET_LOCK" in executed[1]


# ---------------------------------------------------------------------------
# 3. Error propagation — log updated to "failed"
# ---------------------------------------------------------------------------

class TestErrorPropagation:
    """If an unhandled exception occurs, the scrape log is marked 'failed'."""

    def test_exception_in_get_urls_marks_failed(self):
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls",
            side_effect=RuntimeError("DB connection lost"),
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["update_log"].assert_called_once_with(
                42, "failed", error_message="RuntimeError: DB connection lost"
            )
        finally:
            for p in patches.values():
                p.stop()

    def test_exception_before_log_created(self):
        """If create_scrape_log itself fails, update_scrape_log is NOT called
        (because log_id is None)."""
        patches = _base_patches()
        patches["create_log"] = patch(
            "scheduler.create_scrape_log", side_effect=RuntimeError("insert failed"),
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["update_log"].assert_not_called()
            # Lock should still be released
            mocks["release"].assert_called_once()
        finally:
            for p in patches.values():
                p.stop()


# ---------------------------------------------------------------------------
# 4. Lock release on error
# ---------------------------------------------------------------------------

class TestLockReleaseOnError:
    """The advisory lock must always be released, even when the scrape fails."""

    def test_lock_released_on_exception(self):
        lock_conn = MagicMock(name="lock_conn")
        patches = _base_patches()
        patches["acquire"] = patch("scheduler._acquire_db_lock", return_value=lock_conn)
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls",
            side_effect=RuntimeError("boom"),
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["release"].assert_called_once_with(lock_conn)
        finally:
            for p in patches.values():
                p.stop()

    def test_lock_released_on_success(self):
        lock_conn = MagicMock(name="lock_conn")
        patches = _base_patches()
        patches["acquire"] = patch("scheduler._acquire_db_lock", return_value=lock_conn)
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["release"].assert_called_once_with(lock_conn)
        finally:
            for p in patches.values():
                p.stop()

    def test_lock_released_on_validate_error(self):
        """Even if _validate_draft_urls raises, lock is still released."""
        lock_conn = MagicMock(name="lock_conn")
        patches = _base_patches()
        patches["acquire"] = patch("scheduler._acquire_db_lock", return_value=lock_conn)
        patches["validate"] = patch(
            "scheduler._validate_draft_urls", side_effect=RuntimeError("validation exploded"),
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["release"].assert_called_once_with(lock_conn)
            # Should have been marked failed because the exception bubbles up
            mocks["update_log"].assert_called_once()
            args = mocks["update_log"].call_args
            assert args[0][1] == "failed"
        finally:
            for p in patches.values():
                p.stop()


# ---------------------------------------------------------------------------
# 5. URL processing — changed vs unchanged
# ---------------------------------------------------------------------------

class TestURLProcessing:
    """Changed URLs trigger extraction; unchanged URLs do not."""

    def test_per_url_exception_continues_to_next(self):
        """An exception processing one URL should not abort the whole scrape."""
        url1 = _make_url_row(url_id=1, url="https://example.com/a")
        url2 = _make_url_row(url_id=2, url="https://example.com/b")

        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url1, url2]
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed",
            side_effect=[RuntimeError("timeout"), False],
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            # fetch was attempted for both urls; url1 raised, url2 succeeded
            assert mocks["fetch"].call_count == 2
            # Log should show completed (per-URL errors are caught)
            args = mocks["update_log"].call_args[0]
            assert args[1] == "completed"
            assert args[2] == 2  # urls_checked
        finally:
            for p in patches.values():
                p.stop()


# ---------------------------------------------------------------------------
# 6. create_scrape_log / update_scrape_log
# ---------------------------------------------------------------------------

class TestCreateScrapeLog:
    """Basic coverage for the create_scrape_log helper."""

    @patch("scheduler.execute_query", return_value=7)
    def test_returns_inserted_id(self, mock_exec):
        result = create_scrape_log()
        assert result == 7
        mock_exec.assert_called_once()
        query_arg = mock_exec.call_args[0][0]
        assert "INSERT INTO scrape_log" in query_arg


class TestUpdateScrapeLog:
    """Basic coverage for the update_scrape_log helper."""

    @patch("scheduler.execute_query")
    @patch("scheduler.fetch_one", return_value={
        "prompt_total": 1500, "completion_total": 300,
    })
    def test_updates_with_token_totals(self, mock_fetch, mock_exec):
        update_scrape_log(42, "completed", urls_checked=5, urls_changed=2, pubs_extracted=10)

        mock_fetch.assert_called_once()
        assert "llm_usage" in mock_fetch.call_args[0][0]

        mock_exec.assert_called_once()
        params = mock_exec.call_args[0][1]
        # prompt_tokens_total and completion_tokens_total should be in the params
        assert 1500 in params
        assert 300 in params
        assert 42 in params  # log_id

    @patch("scheduler.execute_query")
    @patch("scheduler.fetch_one", return_value=None)
    def test_handles_no_token_row(self, mock_fetch, mock_exec):
        """When fetch_one returns None (no llm_usage rows), totals default to 0."""
        update_scrape_log(42, "failed", error_message="kaboom")

        mock_exec.assert_called_once()
        params = mock_exec.call_args[0][1]
        # prompt_tokens_total=0, completion_tokens_total=0
        assert 0 in params
        assert "kaboom" in params

    @patch("scheduler.execute_query")
    @patch("scheduler.fetch_one", return_value={
        "prompt_total": 0, "completion_total": 0,
    })
    def test_extraction_errors_parameter(self, mock_fetch, mock_exec):
        """extraction_errors is written to the scrape_log query."""
        update_scrape_log(42, "completed", extraction_errors=5)
        params = mock_exec.call_args[0][1]
        assert 5 in params


# ---------------------------------------------------------------------------
# 7. Scrape job is fetch-only
# ---------------------------------------------------------------------------

class TestScrapeJobIsFetchOnly:
    def test_scrape_never_calls_llm_extraction(self):
        """run_scrape_job must not extract — the worker owns extraction."""
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls",
            return_value=[_make_url_row()])
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True)
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            with patch("publication.Publication.try_extract_publications") as mock_llm:
                run_scrape_job()
                mock_llm.assert_not_called()
        finally:
            for p in patches.values():
                p.stop()


# ---------------------------------------------------------------------------
# 8. Stale scrape_log cleanup
# ---------------------------------------------------------------------------

class TestStaleLogCleanup:
    """Verify zombie running scrape_log entries get cleaned up."""

    @patch("scheduler.is_scrape_running", return_value=False)
    @patch("scheduler.execute_query")
    def test_lock_free_marks_running_entries_as_failed(self, mock_exec, mock_lock):
        mock_exec.return_value = 2

        _cleanup_stale_scrape_logs()

        mock_exec.assert_called_once()
        query = mock_exec.call_args[0][0]
        assert "UPDATE scrape_log" in query
        assert "status = 'failed'" in query
        assert "INTERVAL 5 MINUTE" in query

    @patch("scheduler.is_scrape_running", return_value=True)
    @patch("scheduler.execute_query")
    def test_lock_held_spares_newest_running_entry(self, mock_exec, mock_lock):
        mock_exec.return_value = 1

        _cleanup_stale_scrape_logs()

        mock_exec.assert_called_once()
        query = mock_exec.call_args[0][0]
        assert "MAX(id)" in query
        assert "status = 'failed'" in query

    @patch("scheduler.is_scrape_running", return_value=False)
    @patch("scheduler.execute_query")
    def test_skips_when_none_stale(self, mock_exec, mock_lock):
        mock_exec.return_value = 0

        _cleanup_stale_scrape_logs()

        mock_exec.assert_called_once()


# ---------------------------------------------------------------------------
# 9. Enrichment worker
# ---------------------------------------------------------------------------

class TestEnrichmentWorkerLoop:
    """Tests for the continuous enrichment background worker."""

    def setup_method(self):
        """Clear the stop event before each test."""
        import scheduler
        scheduler._enrichment_stop_event.clear()

    def test_enriches_and_sleeps_when_papers_found(self):
        """Worker calls enrich_new_publications, then sleeps IDLE interval."""
        call_count = [0]
        def enrich_then_stop(limit):
            call_count[0] += 1
            if call_count[0] >= 2:
                import scheduler
                scheduler._enrichment_stop_event.set()
            return 5

        with patch("openalex.enrich_new_publications", side_effect=enrich_then_stop) as mock_enrich, \
             patch("scheduler._ENRICHMENT_IDLE_SECONDS", 0), \
             patch("scheduler._ENRICHMENT_BACKOFF_SECONDS", 0):
            _enrichment_worker_loop()

        assert mock_enrich.call_count == 2

    def test_sleeps_idle_interval_when_no_papers(self):
        """Worker sleeps the idle interval when no papers to enrich."""
        call_count = [0]
        def enrich_then_stop(limit):
            call_count[0] += 1
            if call_count[0] >= 1:
                import scheduler
                scheduler._enrichment_stop_event.set()
            return 0

        with patch("openalex.enrich_new_publications", side_effect=enrich_then_stop), \
             patch("scheduler._ENRICHMENT_IDLE_SECONDS", 0), \
             patch("scheduler._ENRICHMENT_BACKOFF_SECONDS", 0):
            _enrichment_worker_loop()

    def test_backs_off_on_consecutive_errors(self):
        """Worker extends sleep after consecutive failures."""
        call_count = [0]
        def fail_then_stop(limit):
            call_count[0] += 1
            if call_count[0] >= 6:
                import scheduler
                scheduler._enrichment_stop_event.set()
                return 0
            raise RuntimeError("OpenAlex down")

        with patch("openalex.enrich_new_publications", side_effect=fail_then_stop) as mock_enrich, \
             patch("scheduler._ENRICHMENT_BACKOFF_THRESHOLD", 3), \
             patch("scheduler._ENRICHMENT_IDLE_SECONDS", 0), \
             patch("scheduler._ENRICHMENT_BACKOFF_SECONDS", 0):
            _enrichment_worker_loop()

        assert mock_enrich.call_count == 6

    def test_resets_backoff_on_success(self):
        """Consecutive failure count resets after a successful enrichment."""
        call_count = [0]
        def fail_succeed_stop(limit):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("transient error")
            if call_count[0] == 3:
                return 5
            if call_count[0] <= 5:
                raise RuntimeError("transient error again")
            import scheduler
            scheduler._enrichment_stop_event.set()
            return 0

        with patch("openalex.enrich_new_publications", side_effect=fail_succeed_stop), \
             patch("scheduler._ENRICHMENT_IDLE_SECONDS", 0), \
             patch("scheduler._ENRICHMENT_BACKOFF_SECONDS", 0):
            _enrichment_worker_loop()


class TestStartStopEnrichmentWorker:
    """Tests for starting and stopping the enrichment worker thread."""

    def teardown_method(self):
        import scheduler
        scheduler._enrichment_thread = None
        scheduler._enrichment_stop_event.clear()

    def test_start_creates_daemon_thread(self):
        """start_enrichment_worker creates a daemon thread."""
        with patch("scheduler.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            start_enrichment_worker()

            mock_thread_cls.assert_called_once()
            assert mock_thread_cls.call_args[1]["daemon"] is True
            mock_thread.start.assert_called_once()

    def test_start_is_idempotent(self):
        """Calling start twice doesn't create a second thread."""
        import scheduler
        scheduler._enrichment_thread = MagicMock(is_alive=MagicMock(return_value=True))
        with patch("scheduler.threading.Thread") as mock_thread_cls:
            start_enrichment_worker()
            mock_thread_cls.assert_not_called()

    def test_stop_sets_event_and_joins(self):
        """stop_enrichment_worker signals the thread and waits for it."""
        import scheduler
        mock_thread = MagicMock()
        scheduler._enrichment_thread = mock_thread

        stop_enrichment_worker()

        assert scheduler._enrichment_stop_event.is_set()
        mock_thread.join.assert_called_once_with(timeout=30)
        assert scheduler._enrichment_thread is None

    def test_stop_when_not_running_is_noop(self):
        """stop_enrichment_worker does nothing if no thread is running."""
        import scheduler
        scheduler._enrichment_thread = None
        stop_enrichment_worker()  # should not raise


class TestSchedulerStartsEnrichmentWorker:
    """Enrichment worker starts/stops with the scheduler when enabled."""

    def teardown_method(self):
        import scheduler
        scheduler._scheduler = None
        scheduler._scheduler_lock_conn = None

    def test_starts_enrichment_worker_when_enabled(self):
        """start_scheduler starts the enrichment worker if ENRICHMENT_WORKER_ENABLED=true."""
        import scheduler

        with patch("scheduler.mysql.connector.connect") as mock_connect, \
             patch("scheduler._cleanup_stale_scrape_logs"), \
             patch("scheduler.BackgroundScheduler") as mock_bg, \
             patch("scheduler.start_enrichment_worker") as mock_start_worker, \
             patch("scheduler.signal.signal"), \
             patch.object(scheduler, 'ENRICHMENT_WORKER_ENABLED', True), \
             patch.object(scheduler, '_scheduler', None), \
             patch.object(scheduler, '_scheduler_lock_conn', None):

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from scheduler import start_scheduler
            start_scheduler()

            mock_start_worker.assert_called_once()

    def test_skips_enrichment_worker_when_disabled(self):
        """start_scheduler does NOT start enrichment worker if ENRICHMENT_WORKER_ENABLED=false."""
        import scheduler

        with patch("scheduler.mysql.connector.connect") as mock_connect, \
             patch("scheduler._cleanup_stale_scrape_logs"), \
             patch("scheduler.BackgroundScheduler") as mock_bg, \
             patch("scheduler.start_enrichment_worker") as mock_start_worker, \
             patch("scheduler.signal.signal"), \
             patch.object(scheduler, 'ENRICHMENT_WORKER_ENABLED', False), \
             patch.object(scheduler, '_scheduler', None), \
             patch.object(scheduler, '_scheduler_lock_conn', None):

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from scheduler import start_scheduler
            start_scheduler()

            mock_start_worker.assert_not_called()

    def test_shutdown_stops_enrichment_worker(self):
        """shutdown_scheduler also stops the enrichment worker."""
        import scheduler

        with patch("scheduler.stop_enrichment_worker") as mock_stop_worker:
            scheduler._scheduler = MagicMock()
            scheduler._scheduler_lock_conn = MagicMock()

            from scheduler import shutdown_scheduler
            shutdown_scheduler()

            mock_stop_worker.assert_called_once()


# ---------------------------------------------------------------------------
# 11. Extraction worker
# ---------------------------------------------------------------------------

def _outcome(status, pubs_count=0):
    from extraction import ExtractionOutcome
    return ExtractionOutcome(status, pubs_count=pubs_count)


class TestExtractionWorkerLoop:
    """Tests for the continuous extraction background worker."""

    def setup_method(self):
        scheduler._extraction_stop_event.clear()

    def teardown_method(self):
        scheduler._extraction_stop_event.clear()

    def test_processes_pending_urls(self):
        rows = [_make_url_row(url_id=1), _make_url_row(url_id=2)]
        processed = []

        def fake_extract(row, scrape_log_id=None):
            processed.append(row["id"])
            if len(processed) >= 2:
                scheduler._extraction_stop_event.set()
            return _outcome("extracted", pubs_count=3)

        with patch("scheduler.get_urls_needing_extraction", return_value=rows), \
             patch("extraction.extract_one_url", side_effect=fake_extract), \
             patch.object(scheduler, "_EXTRACTION_DELAY_SECONDS", 0):
            _extraction_worker_loop()

        assert processed == [1, 2]

    def test_idles_when_queue_empty(self):
        calls = [0]

        def empty_then_stop():
            calls[0] += 1
            if calls[0] >= 2:
                scheduler._extraction_stop_event.set()
            return []

        waits = []

        def record_wait(timeout=None):
            waits.append(timeout)
            return scheduler._extraction_stop_event.is_set()

        with patch("scheduler.get_urls_needing_extraction", side_effect=empty_then_stop), \
             patch.object(scheduler._extraction_stop_event, "wait", side_effect=record_wait):
            _extraction_worker_loop()

        assert scheduler._EXTRACTION_IDLE_SECONDS in waits

    def test_skips_url_after_max_failures(self):
        """A URL that fails 3 times is excluded until restart (poison-pill guard)."""
        rows = [_make_url_row(url_id=1)]
        attempts = [0]
        scans = [0]

        def failing(row, scrape_log_id=None):
            attempts[0] += 1
            return _outcome("failed")

        def get_queue():
            scans[0] += 1
            if scans[0] >= 6:
                scheduler._extraction_stop_event.set()
            return rows

        with patch("scheduler.get_urls_needing_extraction", side_effect=get_queue), \
             patch("extraction.extract_one_url", side_effect=failing), \
             patch.object(scheduler, "_EXTRACTION_DELAY_SECONDS", 0), \
             patch.object(scheduler, "_EXTRACTION_IDLE_SECONDS", 0), \
             patch.object(scheduler, "_EXTRACTION_BACKOFF_THRESHOLD", 99):
            _extraction_worker_loop()

        assert attempts[0] == 3  # 4th+ scans filter the URL out

    def test_backs_off_after_threshold_consecutive_failures(self):
        rows = [_make_url_row(url_id=i) for i in range(1, 5)]
        count = [0]

        def failing(row, scrape_log_id=None):
            count[0] += 1
            if count[0] >= 4:
                scheduler._extraction_stop_event.set()
            return _outcome("failed")

        waits = []

        def record_wait(timeout=None):
            waits.append(timeout)
            return scheduler._extraction_stop_event.is_set()

        with patch("scheduler.get_urls_needing_extraction", return_value=rows), \
             patch("extraction.extract_one_url", side_effect=failing), \
             patch.object(scheduler, "_EXTRACTION_BACKOFF_THRESHOLD", 3), \
             patch.object(scheduler, "_EXTRACTION_MAX_URL_FAILURES", 99), \
             patch.object(scheduler._extraction_stop_event, "wait", side_effect=record_wait):
            _extraction_worker_loop()

        assert scheduler._EXTRACTION_BACKOFF_SECONDS in waits

    def test_success_resets_consecutive_failures(self):
        rows = [_make_url_row(url_id=i) for i in range(1, 5)]
        count = [0]

        def fail_fail_succeed(row, scrape_log_id=None):
            count[0] += 1
            if count[0] >= 4:
                scheduler._extraction_stop_event.set()
            if count[0] == 3:
                return _outcome("extracted", pubs_count=1)
            return _outcome("failed")

        waits = []

        def record_wait(timeout=None):
            waits.append(timeout)
            return scheduler._extraction_stop_event.is_set()

        with patch("scheduler.get_urls_needing_extraction", return_value=rows), \
             patch("extraction.extract_one_url", side_effect=fail_fail_succeed), \
             patch.object(scheduler, "_EXTRACTION_BACKOFF_THRESHOLD", 3), \
             patch.object(scheduler, "_EXTRACTION_MAX_URL_FAILURES", 99), \
             patch.object(scheduler._extraction_stop_event, "wait", side_effect=record_wait):
            _extraction_worker_loop()

        # Success at call 3 reset the counter, so the threshold (3) was never
        # reached and no backoff wait happened.
        assert scheduler._EXTRACTION_BACKOFF_SECONDS not in waits

    def test_unexpected_exception_counts_as_failure(self):
        rows = [_make_url_row(url_id=1)]
        count = [0]

        def exploding(row, scrape_log_id=None):
            count[0] += 1
            if count[0] >= 2:
                scheduler._extraction_stop_event.set()
            raise RuntimeError("DB down")

        with patch("scheduler.get_urls_needing_extraction", return_value=rows), \
             patch("extraction.extract_one_url", side_effect=exploding), \
             patch.object(scheduler, "_EXTRACTION_DELAY_SECONDS", 0), \
             patch.object(scheduler, "_EXTRACTION_IDLE_SECONDS", 0), \
             patch.object(scheduler, "_EXTRACTION_MAX_URL_FAILURES", 99), \
             patch.object(scheduler, "_EXTRACTION_BACKOFF_THRESHOLD", 99):
            _extraction_worker_loop()  # must not raise

        assert count[0] == 2


class TestStartStopExtractionWorker:
    def test_start_creates_daemon_thread(self):
        with patch("scheduler.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            start_extraction_worker()

            mock_thread_cls.assert_called_once()
            assert mock_thread_cls.call_args[1]["daemon"] is True
            assert mock_thread_cls.call_args[1]["name"] == "extraction-worker"
            mock_thread.start.assert_called_once()

            scheduler._extraction_thread = None
            scheduler._extraction_stop_event.clear()

    def test_start_is_idempotent(self):
        scheduler._extraction_thread = MagicMock(is_alive=MagicMock(return_value=True))
        try:
            with patch("scheduler.threading.Thread") as mock_thread_cls:
                start_extraction_worker()
                mock_thread_cls.assert_not_called()
        finally:
            scheduler._extraction_thread = None

    def test_stop_sets_event_and_joins(self):
        mock_thread = MagicMock()
        scheduler._extraction_thread = mock_thread
        scheduler._extraction_stop_event.clear()

        stop_extraction_worker()

        assert scheduler._extraction_stop_event.is_set()
        mock_thread.join.assert_called_once_with(timeout=30)
        assert scheduler._extraction_thread is None
        scheduler._extraction_stop_event.clear()

    def test_stop_when_not_running_is_noop(self):
        scheduler._extraction_thread = None
        stop_extraction_worker()  # should not raise


# ---------------------------------------------------------------------------
# 12. Scheduler lifecycle — extraction worker
# ---------------------------------------------------------------------------

class TestSchedulerStartsExtractionWorker:
    """Extraction worker starts/stops with the scheduler when enabled."""

    def teardown_method(self):
        import scheduler
        scheduler._scheduler = None
        scheduler._scheduler_lock_conn = None

    def test_starts_extraction_worker_when_enabled(self):
        with patch("scheduler.mysql.connector.connect") as mock_connect, \
             patch("scheduler._cleanup_stale_scrape_logs"), \
             patch("scheduler.BackgroundScheduler"), \
             patch("scheduler.start_extraction_worker") as mock_start_worker, \
             patch("scheduler.signal.signal"), \
             patch.object(scheduler, 'EXTRACTION_WORKER_ENABLED', True), \
             patch.object(scheduler, 'ENRICHMENT_WORKER_ENABLED', False), \
             patch.object(scheduler, '_scheduler', None), \
             patch.object(scheduler, '_scheduler_lock_conn', None):

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from scheduler import start_scheduler
            start_scheduler()

            mock_start_worker.assert_called_once()

            scheduler._scheduler = None
            scheduler._scheduler_lock_conn = None

    def test_skips_extraction_worker_when_disabled(self):
        with patch("scheduler.mysql.connector.connect") as mock_connect, \
             patch("scheduler._cleanup_stale_scrape_logs"), \
             patch("scheduler.BackgroundScheduler"), \
             patch("scheduler.start_extraction_worker") as mock_start_worker, \
             patch("scheduler.signal.signal"), \
             patch.object(scheduler, 'EXTRACTION_WORKER_ENABLED', False), \
             patch.object(scheduler, 'ENRICHMENT_WORKER_ENABLED', False), \
             patch.object(scheduler, '_scheduler', None), \
             patch.object(scheduler, '_scheduler_lock_conn', None):

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from scheduler import start_scheduler
            start_scheduler()

            mock_start_worker.assert_not_called()

            scheduler._scheduler = None
            scheduler._scheduler_lock_conn = None

    def test_shutdown_stops_extraction_worker(self):
        with patch("scheduler.stop_extraction_worker") as mock_stop_worker, \
             patch("scheduler.stop_enrichment_worker"):
            scheduler._scheduler = MagicMock()
            scheduler._scheduler_lock_conn = MagicMock()

            from scheduler import shutdown_scheduler
            shutdown_scheduler()

            mock_stop_worker.assert_called_once()
