"""Feed events must be real news: issues #261 (stale new_paper) and #264 (bogus "published")."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.publication_rules import (
    is_journal_venue,
    is_stale_year,
    new_paper_suppression_reason,
    parse_year,
    status_change_suppression_reason,
)

EVENT_AT = datetime(2026, 11, 26, 14, 0, tzinfo=timezone.utc)


class TestParseYear:
    @pytest.mark.parametrize("value,expected", [
        ("2024", 2024), (2019, 2019), ("2023-24", 2023), ("forthcoming", None),
        ("", None), (None, None), ("null", None), ("12345", None),
    ])
    def test_parse_year(self, value, expected):
        assert parse_year(value) == expected

    def test_stale_year_unknown_is_not_stale(self):
        assert not is_stale_year("forthcoming", 2026, 2)
        assert not is_stale_year(None, 2026, 2)
        assert is_stale_year("2023", 2026, 2)
        assert not is_stale_year("2024", 2026, 2)


class TestIsJournalVenue:
    @pytest.mark.parametrize("venue", [
        "American Economic Review", "Journal of Political Economy", "Econometrica",
        "Labour Economics", "Management Science", "Nature", "AEJ: Applied Economics",
        "Brookings Papers on Economic Activity", "Oxford Economic Papers",
        "Review, Federal Reserve Bank of St. Louis, Vol. 10",
        "Journal of Finance (NBER Working Paper 31885)",
        "Handbook of Labor Economics", "The Sowell Legacy: Ideas, Impact, and Intellectual Freedom",
    ])
    def test_journals(self, venue):
        assert is_journal_venue(venue)

    @pytest.mark.parametrize("venue", [
        None, "", "   ", "NBER Working Paper 31885", "CEPR DP 20944", "BIS WP",
        "Marche Polytechnic University, Working Paper No. 445",
        "World Bank Research and Policy Brief", "SUERF Policy Brief No. 1604",
        "ZEW policy brief No. 26-12", "VoxEU column", "VoxEU", "Hiring Lab blog", "mimeo",
        "SSRN", "arxiv Working Paper 2508.05340", "MSc Thesis, University of Genoa",
        "ECB Occasional Paper Series No 396", "Discussion Papers (REL - Recherches Economiques de Louvain)",
        "IMF Working Paper WP/16/192", "FEDS Notes",
    ])
    def test_non_journals(self, venue):
        assert not is_journal_venue(venue)


class TestStatusChangeSuppression:
    def test_published_blog_post_without_venue(self):
        assert status_change_suppression_reason("published", None, "2026", 2026) == "no journal venue"

    def test_published_policy_brief(self):
        assert status_change_suppression_reason(
            "published", "SUERF Policy Brief", "2026", 2026) == "no journal venue"

    def test_accepted_requires_journal(self):
        assert status_change_suppression_reason("accepted", "NBER Working Paper", None, 2026)
        assert status_change_suppression_reason("accepted", "Econometrica", None, 2026) is None

    def test_published_long_ago_is_not_news(self):
        assert status_change_suppression_reason("published", "Journal of Politics", "2023", 2026)
        assert status_change_suppression_reason("published", "Journal of Politics", "2024", 2026)

    def test_published_this_or_last_year_is_news(self):
        assert status_change_suppression_reason("published", "Journal of Politics", "2025", 2026) is None
        assert status_change_suppression_reason("published", "Journal of Politics", "2027", 2026) is None
        assert status_change_suppression_reason("published", "Journal of Politics", None, 2026) is None

    def test_accepted_keeps_working_paper_year_window(self):
        # Accepted papers carry the working-paper year; a 2020 paper accepted in 2026 is news.
        assert status_change_suppression_reason("accepted", "Review of Financial Studies", "2020", 2026) is None
        assert status_change_suppression_reason("accepted", "Management Science", "2012", 2026)

    def test_revise_and_resubmit_needs_no_venue(self):
        assert status_change_suppression_reason("revise_and_resubmit", None, "2024", 2026) is None


class TestNewPaperSuppression:
    def _reason(self, **kw):
        args = dict(is_new=True, is_seed=False, discovered_at=EVENT_AT.replace(tzinfo=None),
                    year="2026", already_announced=False, event_at=EVENT_AT)
        args.update(kw)
        return new_paper_suppression_reason(**args)

    def test_genuinely_new_paper(self):
        assert self._reason() is None

    def test_seed_paper_on_another_url(self):
        # Sakaguchi (#261): seed papers re-listed by a mined URL.
        assert self._reason(is_new=False, is_seed=True,
                            discovered_at=datetime(2026, 3, 19, 22, 31)) == "seed paper"

    def test_paper_known_for_months(self):
        reason = self._reason(is_new=False, discovered_at=datetime(2026, 6, 12))
        assert reason and reason.startswith("known since")

    def test_paper_discovered_days_ago_on_coauthor_page(self):
        assert self._reason(is_new=False, discovered_at=EVENT_AT - timedelta(days=3)) is None

    def test_new_row_for_old_paper(self):
        # Niebel (#261): 2002 ZEW papers created by a new URL's extraction.
        assert self._reason(year="2002") == "year 2002 older than 2y"

    def test_recent_year_kept(self):
        assert self._reason(year="2024") is None
        assert self._reason(year=None) is None

    def test_already_announced(self):
        assert self._reason(is_new=False, already_announced=True,
                            discovered_at=EVENT_AT - timedelta(days=1)) == "already announced"


@dataclass
class FakeSaveResult:
    paper_id: int
    title: str
    is_new: bool
    new_to_this_url: bool
    status: str | None


def _conn_with_baseline():
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    cursor = MagicMock()
    cursor.fetchone.return_value = (3,)
    conn.cursor.return_value = cursor
    return conn, cursor


def _inserts(cursor):
    return [c for c in cursor.execute.call_args_list if "INSERT INTO feed_events" in str(c)]


class TestEmitterAppliesRules:
    def _emit(self, result, facts):
        from backend.pipeline.feed_events import FeedEventEmitter
        conn, cursor = _conn_with_baseline()
        with patch("backend.pipeline.feed_events.get_connection", return_value=conn), \
                patch("backend.pipeline.feed_events._get_previous_snapshot_html", return_value=None), \
                patch("backend.pipeline.feed_events._load_paper_facts", return_value=facts):
            n = FeedEventEmitter.emit_new_paper_events([result], url="https://example.com/new",
                                                       event_date=EVENT_AT)
        return n, cursor

    def test_seed_paper_new_to_url_gets_no_event(self):
        n, cursor = self._emit(
            FakeSaveResult(1, "Constrained Classification and Policy Learning", False, True, "working_paper"),
            {"is_seed": True, "discovered_at": datetime(2026, 3, 19), "year": "2023", "already_announced": False},
        )
        assert n == 0 and not _inserts(cursor)

    def test_new_paper_row_dated_2002_gets_no_event(self):
        n, cursor = self._emit(
            FakeSaveResult(2, "Competition in the German Broadband Access Market", True, True, "working_paper"),
            {"is_seed": False, "discovered_at": EVENT_AT, "year": "2002", "already_announced": False},
        )
        assert n == 0 and not _inserts(cursor)

    def test_known_paper_new_to_url_gets_no_event(self):
        n, _ = self._emit(
            FakeSaveResult(3, "Old Known Paper", False, True, "working_paper"),
            {"is_seed": False, "discovered_at": datetime(2026, 6, 1), "year": None, "already_announced": False},
        )
        assert n == 0

    def test_genuinely_new_paper_gets_event(self):
        n, cursor = self._emit(
            FakeSaveResult(4, "A Brand New Paper", True, True, "working_paper"),
            {"is_seed": False, "discovered_at": EVENT_AT, "year": "2026", "already_announced": False},
        )
        assert n == 1 and len(_inserts(cursor)) == 1

    def test_paper_already_on_url_is_skipped_without_lookup(self):
        from backend.pipeline.feed_events import FeedEventEmitter
        conn, _ = _conn_with_baseline()
        with patch("backend.pipeline.feed_events.get_connection", return_value=conn), \
                patch("backend.pipeline.feed_events._get_previous_snapshot_html", return_value=None), \
                patch("backend.pipeline.feed_events._load_paper_facts") as facts:
            n = FeedEventEmitter.emit_new_paper_events(
                [FakeSaveResult(5, "Same Url Paper", False, False, "working_paper")], url="https://x.org")
        assert n == 0
        facts.assert_not_called()


@dataclass
class FakeSnapshotResult:
    status_changed: bool
    old_status: str | None
    new_status: str | None
    venue: str | None = None
    year: str | None = None


class TestAppendSnapshotsAppliesRules:
    def _run(self, pub, snapshot, event_date=EVENT_AT):
        from backend.pipeline.extraction import _append_snapshots
        with patch("backend.pipeline.extraction.find_papers_by_titles",
                   return_value={pub["title"]: {"id": 7}}), \
                patch("backend.pipeline.extraction.append_paper_snapshot", return_value=snapshot), \
                patch("backend.pipeline.extraction.FeedEventEmitter") as emitter:
            _append_snapshots([pub], "https://example.com/pubs", event_date=event_date)
        return emitter.emit_status_change

    def _pub(self, **kw):
        pub = {"title": "Some Paper Title Here", "status": "published", "year": "2026",
               "venue": None, "abstract": None, "draft_url": None}
        pub.update(kw)
        return pub

    def test_blog_post_marked_published_is_suppressed(self):
        emit = self._run(self._pub(title="Will Cambodia Commit to Protecting Its Forests?", year="2021"),
                         FakeSnapshotResult(True, "working_paper", "published"))
        emit.assert_not_called()

    def test_working_paper_series_venue_is_suppressed(self):
        emit = self._run(self._pub(venue="Marche Polytechnic University, Working Paper"),
                         FakeSnapshotResult(True, "working_paper", "published",
                                            venue="Marche Polytechnic University, Working Paper"))
        emit.assert_not_called()

    def test_long_published_article_is_suppressed(self):
        emit = self._run(self._pub(year="2019", venue="Journal of Political Economy"),
                         FakeSnapshotResult(True, "revise_and_resubmit", "published",
                                            venue="Journal of Political Economy"))
        emit.assert_not_called()

    def test_stored_year_used_when_page_omits_it(self):
        emit = self._run(self._pub(year=None, venue="Journal of Political Economy"),
                         FakeSnapshotResult(True, "working_paper", "published",
                                            venue="Journal of Political Economy", year="2018"))
        emit.assert_not_called()

    def test_fresh_journal_publication_is_emitted(self):
        emit = self._run(self._pub(venue="Journal of Applied Econometrics"),
                         FakeSnapshotResult(True, "working_paper", "published",
                                            venue="Journal of Applied Econometrics"))
        emit.assert_called_once()

    def test_reference_year_is_event_date(self):
        # A 2024 article seen in a fetch dated 2025 is news; seen in 2026 it is not.
        pub = self._pub(year="2024", venue="Economics Letters")
        snap = FakeSnapshotResult(True, "working_paper", "published", venue="Economics Letters")
        self._run(pub, snap, event_date=datetime(2025, 5, 1, tzinfo=timezone.utc)).assert_called_once()
        self._run(pub, snap).assert_not_called()

    def test_accepted_without_venue_is_suppressed(self):
        emit = self._run(self._pub(status="accepted"),
                         FakeSnapshotResult(True, "working_paper", "accepted"))
        emit.assert_not_called()


class TestCleanupScript:
    def _reason(self, row):
        from scripts.cleanup_spurious_feed_events import spurious_reason
        return spurious_reason(row)

    def test_flags_seed_new_paper(self):
        assert self._reason({"event_type": "new_paper", "created_at": EVENT_AT.replace(tzinfo=None),
                             "is_seed": 1, "discovered_at": datetime(2026, 3, 19), "year": "2023"})

    def test_keeps_genuine_new_paper(self):
        assert self._reason({"event_type": "new_paper", "created_at": datetime(2026, 11, 26, 14),
                             "is_seed": 0, "discovered_at": datetime(2026, 11, 26, 20), "year": None}) is None

    def test_flags_published_policy_brief(self):
        assert self._reason({"event_type": "status_change", "created_at": datetime(2026, 11, 5),
                             "new_status": "published", "venue": "World Bank Research and Policy Brief",
                             "year": "2017"}) == "no journal venue"

    def test_keeps_journal_publication(self):
        assert self._reason({"event_type": "status_change", "created_at": datetime(2026, 11, 26),
                             "new_status": "published", "venue": "Journal of Applied Econometrics",
                             "year": "2026"}) is None

    def test_ignores_title_changes(self):
        assert self._reason({"event_type": "title_change", "created_at": datetime(2026, 11, 26)}) is None

    def test_backup_copies_every_source_column(self):
        from scripts.cleanup_spurious_feed_events import copied_columns
        cols = ["id", "paper_id", "created_at", "recorded_at"]
        assert copied_columns(cols, cols + ["cleanup_run", "cleanup_reason", "backed_up_at"]) == cols

    def test_backup_refuses_schema_drift(self):
        from scripts.cleanup_spurious_feed_events import copied_columns
        with pytest.raises(RuntimeError, match="new_col"):
            copied_columns(["id", "new_col"], ["id", "cleanup_run"])

    def test_backup_tables_cover_cascaded_children(self):
        from scripts.cleanup_spurious_feed_events import BACKED_UP_TABLES, _RESTORE_GUARDS
        assert [t for t, _ in BACKED_UP_TABLES] == ["feed_events", "digest_deliveries", "feed_event_reviews"]
        assert set(_RESTORE_GUARDS) == {t for t, _ in BACKED_UP_TABLES}
