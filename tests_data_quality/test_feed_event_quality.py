"""Feed event invariants against the real database.

These automate the manual spot-checks that found real production incidents:
PR #158 found 6 of 24 recent new_paper events were bogus; FE#3187 violated
the baseline guard; PR #153 found regression status_change events.
"""
import zlib

from conftest import STATUS_ORDER, PROJECT_EPOCH, fmt_violations


class TestNewPaperBaselineGuard:
    """Every new_paper event's source URL must have had >= 2 HTML snapshots.

    Guards the FE#3187 incident: an event inserted for a URL with zero
    snapshots (the DB trigger did not exist in prod for months, PR #158).
    The 1-hour slack covers the snapshot archived by the same fetch that
    triggered the event (event_date is the fetch timestamp since PR #144).
    """

    def test_no_new_paper_events_without_snapshot_baseline(self, db):
        rows = db.fetch_all(
            f"""
            SELECT fe.id, fe.paper_id, fe.created_at, p.source_url
            FROM feed_events fe
            JOIN papers p ON p.id = fe.paper_id
            WHERE fe.event_type = 'new_paper'
              AND NOT EXISTS (
                  SELECT 1
                  FROM researcher_urls ru
                  JOIN html_snapshots hs ON hs.url_id = ru.id
                  WHERE ru.url = p.source_url
                    AND hs.snapshot_at <= fe.created_at + INTERVAL 1 HOUR
                  GROUP BY ru.id
                  HAVING COUNT(*) >= 2
              )
            ORDER BY fe.created_at DESC
            """
        )
        assert not rows, "new_paper events on URLs lacking a 2-snapshot baseline:\n" + fmt_violations(rows)


class TestStatusChangeProgression:
    """status_change events may only record forward progressions (PR #153)."""

    def test_no_regression_status_change_events(self, db):
        rows = db.fetch_all(
            f"""
            SELECT id, paper_id, old_status, new_status, created_at
            FROM feed_events
            WHERE event_type = 'status_change'
              AND old_status IS NOT NULL AND new_status IS NOT NULL
              AND FIELD(old_status, {STATUS_ORDER}) >= FIELD(new_status, {STATUS_ORDER})
            ORDER BY created_at DESC
            """
        )
        assert not rows, "status_change events that are regressions or no-ops:\n" + fmt_violations(rows)

    def test_status_change_events_have_both_statuses(self, db):
        """emit_status_change always writes both; NULLs mean a broken writer."""
        rows = db.fetch_all(
            """
            SELECT id, paper_id, old_status, new_status, created_at
            FROM feed_events
            WHERE event_type = 'status_change'
              AND (old_status IS NULL OR new_status IS NULL)
            """
        )
        assert not rows, "status_change events missing old/new status:\n" + fmt_violations(rows)


class TestNewPaperStatusGuard:
    """new_paper events are suppressed for published papers (issue #89 guard)."""

    def test_no_new_paper_events_with_published_status(self, db):
        rows = db.fetch_all(
            """
            SELECT id, paper_id, new_status, created_at
            FROM feed_events
            WHERE event_type = 'new_paper' AND new_status = 'published'
            """
        )
        assert not rows, "new_paper events for published papers:\n" + fmt_violations(rows)

    def test_no_new_paper_events_for_seed_papers(self, db):
        """is_seed papers are first-ever extractions; events for them are noise."""
        rows = db.fetch_all(
            """
            SELECT fe.id, fe.paper_id, fe.created_at
            FROM feed_events fe
            JOIN papers p ON p.id = fe.paper_id
            WHERE fe.event_type = 'new_paper' AND p.is_seed = TRUE
            """
        )
        assert not rows, "new_paper events for seed papers:\n" + fmt_violations(rows)


class TestEventDateSanity:
    """Event dates must be plausible (PR #144 changed event dating)."""

    def test_no_future_dated_events(self, db):
        rows = db.fetch_all(
            "SELECT id, paper_id, event_type, created_at FROM feed_events "
            "WHERE created_at > NOW() + INTERVAL 1 DAY"
        )
        assert not rows, "future-dated feed events:\n" + fmt_violations(rows)

    def test_no_events_before_project_epoch(self, db):
        rows = db.fetch_all(
            f"SELECT id, paper_id, event_type, created_at FROM feed_events "
            f"WHERE created_at < '{PROJECT_EPOCH}'"
        )
        assert not rows, "events dated before the project existed:\n" + fmt_violations(rows)


class TestNoDuplicateNewPaperEvents:
    """A paper gets at most one new_paper event (dedup guard in emitter)."""

    def test_no_paper_has_multiple_new_paper_events(self, db):
        rows = db.fetch_all(
            """
            SELECT paper_id, COUNT(*) AS event_count
            FROM feed_events
            WHERE event_type = 'new_paper'
            GROUP BY paper_id
            HAVING COUNT(*) > 1
            ORDER BY event_count DESC
            """
        )
        assert not rows, "papers with duplicate new_paper events:\n" + fmt_violations(rows)


class TestRecentNewPaperEventsAgainstSnapshots:
    """Automated version of the PR #158 spot check that found 6/24 bogus events.

    For the most recent new_paper events, decompress the HTML snapshot that
    was current just before the event and check whether the paper title was
    already on the page — using the same normalization as production
    (feed_events._normalize_html_for_matching / _normalize_for_matching).
    """

    SAMPLE_SIZE = 25

    def test_recent_new_paper_titles_absent_from_prior_snapshot(self, db):
        from feed_events import _normalize_for_matching, _normalize_html_for_matching

        events = db.fetch_all(
            f"""
            SELECT fe.id AS event_id, fe.paper_id, fe.created_at, p.title, p.source_url
            FROM feed_events fe
            JOIN papers p ON p.id = fe.paper_id
            WHERE fe.event_type = 'new_paper' AND p.source_url IS NOT NULL
            ORDER BY fe.created_at DESC
            LIMIT {self.SAMPLE_SIZE}
            """
        )
        bogus = []
        for ev in events:
            snap = db.fetch_one(
                """
                SELECT hs.raw_html_compressed
                FROM html_snapshots hs
                JOIN researcher_urls ru ON ru.id = hs.url_id
                WHERE ru.url = %s AND hs.snapshot_at < %s
                ORDER BY hs.snapshot_at DESC
                LIMIT 1
                """,
                (ev["source_url"], ev["created_at"]),
            )
            if not snap or not snap["raw_html_compressed"]:
                continue
            try:
                raw = zlib.decompress(snap["raw_html_compressed"]).decode("utf-8", errors="replace")
            except Exception:
                continue
            if _normalize_for_matching(ev["title"]) in _normalize_html_for_matching(raw):
                bogus.append(
                    {"event_id": ev["event_id"], "paper_id": ev["paper_id"],
                     "created_at": ev["created_at"], "title": ev["title"][:80]}
                )
        assert not bogus, (
            f"{len(bogus)}/{len(events)} recent new_paper events have titles that were "
            "already on the page in the prior snapshot (bogus events):\n" + fmt_violations(bogus)
        )


class TestEventsFromDeadUrls:
    """Events created after their source URL was deactivated mean a zombie
    pipeline path kept writing (PR #137 lifecycle violation)."""

    def test_no_events_after_url_deactivation(self, db):
        rows = db.fetch_all(
            """SELECT fe.id, fe.event_type, fe.created_at, ru.deactivated_at, ru.url
               FROM feed_events fe
               JOIN papers p ON p.id = fe.paper_id
               JOIN researcher_urls ru ON ru.url = p.source_url
               WHERE ru.is_active = FALSE AND ru.deactivated_at IS NOT NULL
                 AND fe.created_at > ru.deactivated_at + INTERVAL 1 HOUR
               LIMIT 50"""
        )
        assert not rows, "events created after URL deactivation:\n" + fmt_violations(rows)


class TestEventEvidence:
    """Events must be backed by observable history."""

    def test_status_change_requires_snapshots(self, db):
        """A status change can only be OBSERVED between two extractions —
        a paper with zero snapshots cannot have one."""
        rows = db.fetch_all(
            """SELECT fe.id, fe.paper_id, fe.old_status, fe.new_status
               FROM feed_events fe
               WHERE fe.event_type = 'status_change'
                 AND NOT EXISTS (SELECT 1 FROM paper_snapshots ps WHERE ps.paper_id = fe.paper_id)
               LIMIT 50"""
        )
        assert not rows, "status_change events with zero snapshots:\n" + fmt_violations(rows)

    def test_no_noop_title_changes(self, db):
        rows = db.fetch_all(
            """SELECT id, paper_id, old_title FROM feed_events
               WHERE event_type = 'title_change' AND old_title = new_title
               LIMIT 50"""
        )
        assert not rows, "no-op title_change events:\n" + fmt_violations(rows)
