"""Schema-level safety nets that have silently failed before.

PR #158 discovered the feed_events snapshot-guard trigger had NEVER existed
in production: the migration failed on every startup with MySQL error 1419
(app user lacks SUPER with binlog enabled) and only logged a warning. A test
asserting the trigger exists would have caught months of missing protection.
"""
from conftest import fmt_violations


class TestSnapshotGuardTrigger:
    def test_feed_events_snapshot_guard_trigger_exists(self, db):
        rows = db.fetch_all("SHOW TRIGGERS LIKE 'feed_events'")
        names = {r.get("Trigger") for r in rows}
        assert "trg_feed_events_snapshot_guard" in names, (
            "trg_feed_events_snapshot_guard is missing — the DB-level new_paper "
            "guard does not exist (MySQL error 1419? see PR #158: the container "
            "needs --log-bin-trust-function-creators=1). Triggers present: "
            f"{sorted(n for n in names if n)}"
        )


class TestExpectedTablesExist:
    """Catches half-applied migrations: every table the code queries must exist."""

    EXPECTED = [
        "researchers", "researcher_urls", "papers", "paper_snapshots",
        "html_content", "html_snapshots", "authorship", "feed_events",
        "paper_links", "openalex_coauthors", "batch_jobs", "llm_usage",
        "scrape_log", "researcher_snapshots",
    ]

    def test_all_expected_tables_exist(self, db):
        rows = db.fetch_all("SHOW TABLES")
        existing = {list(r.values())[0] for r in rows}
        missing = [t for t in self.EXPECTED if t not in existing]
        assert not missing, f"missing tables (migrations not applied?): {missing}"


class TestHtmlContentConsistency:
    """html_content drives the extraction queue; inconsistent rows stall it."""

    def test_extracted_hash_implies_extracted_at(self, db):
        rows = db.fetch_all(
            """
            SELECT id, url_id, extracted_hash, extracted_at FROM html_content
            WHERE extracted_hash IS NOT NULL AND extracted_at IS NULL
            LIMIT 50
            """
        )
        assert not rows, "html_content rows extracted without timestamp:\n" + fmt_violations(rows)

    def test_no_content_hash_without_content(self, db):
        rows = db.fetch_all(
            """
            SELECT id, url_id, content_hash FROM html_content
            WHERE content_hash IS NOT NULL AND (content IS NULL OR content = '')
            LIMIT 50
            """
        )
        assert not rows, "html_content rows with hash but no content:\n" + fmt_violations(rows)
