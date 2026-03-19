"""Tests for scheduler.run_scrape_job() and related helpers.

Covers the scrape orchestration logic: locking, log creation/update,
URL processing with change detection, error handling, and lock release.
All external dependencies (DB, network, LLM) are mocked.
"""
import os

# Env vars required at import time by transitive dependencies
os.environ.setdefault("CONTENT_MAX_CHARS", "500000")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")

from unittest.mock import MagicMock, patch, call

import pytest

import scheduler
from scheduler import run_scrape_job, create_scrape_log, update_scrape_log


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
        "get_urls": patch("scheduler.Researcher.get_all_researcher_urls", return_value=[]),
        "validate": patch("scheduler._validate_draft_urls"),
        "get_prev": patch("scheduler.HTMLFetcher.get_previous_text", return_value=None),
        "fetch": patch("scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=False),
        "get_latest": patch("scheduler.HTMLFetcher.get_latest_text", return_value=""),
        "compute_diff": patch("scheduler.HTMLFetcher.compute_diff", return_value="diff text"),
        "extract_desc": patch("scheduler.HTMLFetcher.extract_description", return_value=None),
        "extract_pubs": patch("scheduler.Publication.extract_publications", return_value=[]),
        "save_pubs": patch("scheduler.Publication.save_publications"),
        "title_hash": patch("scheduler.Database.compute_title_hash", return_value="abc123"),
        "fetch_one": patch("scheduler.Database.fetch_one", return_value=None),
        "paper_snap": patch("scheduler.Database.append_paper_snapshot"),
        "researcher_snap": patch("scheduler.Database.append_researcher_snapshot"),
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
            mocks["update_log"].assert_called_once_with(42, "completed", 0, 0, 0)
            mocks["validate"].assert_called_once()
            mocks["release"].assert_called_once()
        finally:
            for p in patches.values():
                p.stop()

    def test_happy_path_with_changed_url(self):
        """One URL changes, publications extracted and saved, log reflects counts."""
        url_row = _make_url_row()
        pubs = [{"title": "My Paper", "status": "published", "venue": "AER",
                 "abstract": "...", "draft_url": None, "year": "2025"}]

        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True
        )
        patches["get_latest"] = patch(
            "scheduler.HTMLFetcher.get_latest_text", return_value="page content"
        )
        patches["extract_pubs"] = patch(
            "scheduler.Publication.extract_publications", return_value=pubs
        )
        patches["fetch_one"] = patch(
            "scheduler.Database.fetch_one", return_value={"id": 99}
        )

        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["extract_pubs"].assert_called_once()
            mocks["save_pubs"].assert_called_once_with(
                url_row["url"], pubs, is_seed=True,
            )
            mocks["paper_snap"].assert_called_once()
            mocks["update_log"].assert_called_once_with(42, "completed", 1, 1, 1)
        finally:
            for p in patches.values():
                p.stop()


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

    def test_unchanged_url_skips_extraction(self):
        url_row = _make_url_row()
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        # fetch returns False => unchanged
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=False
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["get_prev"].assert_called_once_with(url_row["id"])
            mocks["fetch"].assert_called_once()
            mocks["get_latest"].assert_not_called()
            mocks["extract_pubs"].assert_not_called()
            mocks["update_log"].assert_called_once_with(42, "completed", 1, 0, 0)
        finally:
            for p in patches.values():
                p.stop()

    def test_changed_url_with_old_text_uses_diff(self):
        """When old content exists, compute_diff is called and its result passed to extraction."""
        url_row = _make_url_row()
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        patches["get_prev"] = patch(
            "scheduler.HTMLFetcher.get_previous_text", return_value="old content"
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True
        )
        patches["get_latest"] = patch(
            "scheduler.HTMLFetcher.get_latest_text", return_value="new content"
        )
        patches["compute_diff"] = patch(
            "scheduler.HTMLFetcher.compute_diff", return_value="the diff"
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["compute_diff"].assert_called_once_with("old content", "new content")
            mocks["extract_pubs"].assert_called_once()
            # First positional arg to extract_publications should be the diff
            assert mocks["extract_pubs"].call_args[0][0] == "the diff"
        finally:
            for p in patches.values():
                p.stop()

    def test_first_scrape_uses_full_text_not_diff(self):
        """On first scrape (old_text is None), full new text is used directly."""
        url_row = _make_url_row()
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        patches["get_prev"] = patch(
            "scheduler.HTMLFetcher.get_previous_text", return_value=None
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True
        )
        patches["get_latest"] = patch(
            "scheduler.HTMLFetcher.get_latest_text", return_value="full page text"
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["compute_diff"].assert_not_called()
            mocks["extract_pubs"].assert_called_once()
            assert mocks["extract_pubs"].call_args[0][0] == "full page text"
        finally:
            for p in patches.values():
                p.stop()

    def test_per_url_exception_continues_to_next(self):
        """An exception processing one URL should not abort the whole scrape."""
        url1 = _make_url_row(url_id=1, url="https://example.com/a")
        url2 = _make_url_row(url_id=2, url="https://example.com/b")

        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url1, url2]
        )
        # First URL's get_previous_text raises, second one succeeds
        patches["get_prev"] = patch(
            "scheduler.HTMLFetcher.get_previous_text",
            side_effect=[RuntimeError("timeout"), None],
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=False
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            # fetch should be called for url2 even though url1 failed
            assert mocks["fetch"].call_count == 1
            # Log should show completed (per-URL errors are caught)
            mocks["update_log"].assert_called_once_with(42, "completed", 2, 0, 0)
        finally:
            for p in patches.values():
                p.stop()

    def test_home_page_extracts_description_on_change(self):
        """HOME page type triggers description extraction when content changed."""
        url_row = _make_url_row(page_type="HOME")
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True
        )
        patches["get_latest"] = patch(
            "scheduler.HTMLFetcher.get_latest_text", return_value="homepage text"
        )
        patches["extract_desc"] = patch(
            "scheduler.HTMLFetcher.extract_description", return_value="A labor economist."
        )
        patches["fetch_one"] = patch(
            "scheduler.Database.fetch_one",
            return_value={"position": "Professor", "affiliation": "MIT"},
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["extract_desc"].assert_called_once()
            mocks["researcher_snap"].assert_called_once_with(
                url_row["researcher_id"], "Professor", "MIT",
                "A labor economist.", source_url=url_row["url"],
            )
        finally:
            for p in patches.values():
                p.stop()

    def test_home_page_no_description_skips_snapshot(self):
        """HOME page where extract_description returns None should not append snapshot."""
        url_row = _make_url_row(page_type="HOME")
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True
        )
        patches["get_latest"] = patch(
            "scheduler.HTMLFetcher.get_latest_text", return_value="homepage text"
        )
        patches["extract_desc"] = patch(
            "scheduler.HTMLFetcher.extract_description", return_value=None
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["extract_desc"].assert_called_once()
            mocks["researcher_snap"].assert_not_called()
        finally:
            for p in patches.values():
                p.stop()

    def test_unchanged_home_page_skips_description(self):
        """HOME page that didn't change should NOT re-extract description."""
        url_row = _make_url_row(page_type="HOME")
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=False
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["extract_desc"].assert_not_called()
        finally:
            for p in patches.values():
                p.stop()

    def test_paper_not_found_skips_snapshot(self):
        """If paper lookup returns None, paper snapshot is skipped."""
        url_row = _make_url_row()
        pubs = [{"title": "Ghost Paper", "status": "published"}]
        patches = _base_patches()
        patches["get_urls"] = patch(
            "scheduler.Researcher.get_all_researcher_urls", return_value=[url_row]
        )
        patches["fetch"] = patch(
            "scheduler.HTMLFetcher.fetch_and_save_if_changed", return_value=True
        )
        patches["get_latest"] = patch(
            "scheduler.HTMLFetcher.get_latest_text", return_value="text"
        )
        patches["extract_pubs"] = patch(
            "scheduler.Publication.extract_publications", return_value=pubs
        )
        # Paper not found in DB
        patches["fetch_one"] = patch(
            "scheduler.Database.fetch_one", return_value=None
        )
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            run_scrape_job()

            mocks["title_hash"].assert_called_once_with("Ghost Paper")
            mocks["paper_snap"].assert_not_called()
        finally:
            for p in patches.values():
                p.stop()


# ---------------------------------------------------------------------------
# 6. create_scrape_log / update_scrape_log
# ---------------------------------------------------------------------------

class TestCreateScrapeLog:
    """Basic coverage for the create_scrape_log helper."""

    @patch("scheduler.Database.execute_query", return_value=7)
    def test_returns_inserted_id(self, mock_exec):
        result = create_scrape_log()
        assert result == 7
        mock_exec.assert_called_once()
        query_arg = mock_exec.call_args[0][0]
        assert "INSERT INTO scrape_log" in query_arg


class TestUpdateScrapeLog:
    """Basic coverage for the update_scrape_log helper."""

    @patch("scheduler.Database.execute_query")
    @patch("scheduler.Database.fetch_one", return_value={
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

    @patch("scheduler.Database.execute_query")
    @patch("scheduler.Database.fetch_one", return_value=None)
    def test_handles_no_token_row(self, mock_fetch, mock_exec):
        """When fetch_one returns None (no llm_usage rows), totals default to 0."""
        update_scrape_log(42, "failed", error_message="kaboom")

        mock_exec.assert_called_once()
        params = mock_exec.call_args[0][1]
        # prompt_tokens_total=0, completion_tokens_total=0
        assert 0 in params
        assert "kaboom" in params
