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


class TestLlmUsageCallTypeEnum:
    """Every call_type the code logs must exist in the DB enum.

    log_llm_usage silences INSERT failures, so a call_type missing from the
    ENUM drops usage rows invisibly (2026-06-12: every diff_extraction call
    failed with MySQL 1265 — ~350 untracked calls in one night).
    """

    # Keep in sync with log_llm_usage call sites (grep: log_llm_usage\()
    USED_CALL_TYPES = {
        "publication_extraction", "description_extraction",
        "researcher_disambiguation", "jel_classification", "diff_extraction",
    }

    def test_enum_covers_all_call_types_used_in_code(self, db):
        row = db.fetch_one(
            """
            SELECT COLUMN_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'llm_usage'
              AND COLUMN_NAME = 'call_type'
            """
        )
        assert row, "llm_usage.call_type column not found"
        enum_def = row["COLUMN_TYPE"]
        missing = [t for t in self.USED_CALL_TYPES if f"'{t}'" not in enum_def]
        assert not missing, (
            f"call_type values used in code but missing from ENUM {enum_def}: "
            f"{missing} — their llm_usage INSERTs fail silently (error 1265)"
        )


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


class TestTimestampSanity:
    def test_no_future_html_snapshots(self, db):
        rows = db.fetch_all(
            "SELECT id, url_id, snapshot_at FROM html_snapshots "
            "WHERE snapshot_at > NOW() + INTERVAL 1 DAY LIMIT 50"
        )
        assert not rows, "future-dated html_snapshots:\n" + fmt_violations(rows)

    def test_no_future_paper_snapshots(self, db):
        rows = db.fetch_all(
            "SELECT id, paper_id, scraped_at FROM paper_snapshots "
            "WHERE scraped_at > NOW() + INTERVAL 1 DAY LIMIT 50"
        )
        assert not rows, "future-dated paper_snapshots:\n" + fmt_violations(rows)


class TestSourceUrlTracking:
    def test_papers_source_url_is_tracked(self, db):
        """A paper whose source_url has no researcher_urls row came from a
        page the system no longer knows — its feed-event guards (snapshot
        baseline, title-in-prior-snapshot) can never run again."""
        rows = db.fetch_all(
            """SELECT p.id, p.source_url FROM papers p
               WHERE p.source_url IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM researcher_urls ru WHERE ru.url = p.source_url)
               LIMIT 50"""
        )
        assert not rows, "papers whose source_url is untracked:\n" + fmt_violations(rows)
