"""Pipeline liveness checks — only meaningful against the LIVE database.

A synced mirror is frozen at dump time, so freshness assertions would always
fail there. These tests skip unless DATA_QUALITY_LIVE=1 (run them on the
server: `make check-data-live` inside /opt/econ-newsfeed).

Each check maps to a real outage class:
- 2026-06-10: extraction silently stalled for hours (fenced-JSON change)
- 2026-06-11: backups had never run (cron) and nobody noticed
- scrape_log zombie 'running' rows from mid-scrape process deaths
"""
import os

import pytest

from conftest import fmt_violations

pytestmark = pytest.mark.skipif(
    os.environ.get("DATA_QUALITY_LIVE") != "1",
    reason="pipeline-health checks need the live DB (set DATA_QUALITY_LIVE=1)",
)


class TestScrapePipelineFreshness:
    def test_scrape_ran_recently(self, db):
        """The scheduler should complete a scrape at least every 48h."""
        row = db.fetch_one(
            "SELECT MAX(started_at) AS last_start FROM scrape_log"
        )
        assert row and row["last_start"], "scrape_log is empty — scheduler never ran"
        age = db.fetch_one(
            "SELECT TIMESTAMPDIFF(HOUR, MAX(started_at), NOW()) AS hours FROM scrape_log"
        )
        assert age["hours"] is not None and age["hours"] <= 48, (
            f"no scrape started in {age['hours']}h — scheduler dead?"
        )

    def test_no_stuck_running_scrapes(self, db):
        """Startup cleanup marks zombies, but a live row >24h means a hang."""
        rows = db.fetch_all(
            """SELECT id, started_at FROM scrape_log
               WHERE status = 'running' AND started_at < NOW() - INTERVAL 24 HOUR"""
        )
        assert not rows, "scrape runs stuck in 'running' >24h:\n" + fmt_violations(rows)


class TestExtractionWorkerLiveness:
    def test_worker_active_when_queue_nonempty(self, db):
        """If extractable work exists (pending hash + stored content), the
        worker must have made an LLM call within the last 6 hours — silence
        here is how the 2026-06-10 stall went unnoticed."""
        queue = db.fetch_one(
            """SELECT COUNT(*) AS n
               FROM html_content hc JOIN researcher_urls ru ON ru.id = hc.url_id
               WHERE ru.is_active = TRUE
                 AND hc.content IS NOT NULL AND hc.content != ''
                 AND hc.content_hash IS NOT NULL
                 AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)"""
        )
        if not queue or queue["n"] == 0:
            return
        last_call = db.fetch_one(
            """SELECT TIMESTAMPDIFF(HOUR, MAX(called_at), NOW()) AS hours
               FROM llm_usage WHERE call_type = 'publication_extraction'"""
        )
        assert last_call and last_call["hours"] is not None and last_call["hours"] <= 6, (
            f"{queue['n']} URLs are extractable but the last extraction LLM call was "
            f"{last_call['hours'] if last_call else 'never'}h ago — worker stalled?"
        )


class TestBatchJobHygiene:
    def test_no_stale_pending_batches(self, db):
        rows = db.fetch_all(
            """SELECT id, openai_batch_id, status, created_at FROM batch_jobs
               WHERE status IN ('submitted', 'validating', 'in_progress', 'finalizing')
                 AND created_at < NOW() - INTERVAL 48 HOUR
               LIMIT 20"""
        )
        assert not rows, "batch jobs stuck >48h:\n" + fmt_violations(rows)
