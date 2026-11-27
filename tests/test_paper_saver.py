"""Tests for PaperSaver — persistence logic separated from event creation."""
import pytest
from unittest.mock import patch, MagicMock
from backend.pipeline.paper_saver import PaperSaver, SaveResult, _author_id_cache


@pytest.fixture(autouse=True)
def clear_author_cache():
    _author_id_cache.clear()
    yield
    _author_id_cache.clear()


def _mock_conn(lastrowid=1):
    """Create a mock DB connection."""
    cursor = MagicMock()
    cursor.lastrowid = lastrowid
    cursor.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cursor


class TestSavePublicationsReturnsResults:
    """save_publications returns SaveResult objects instead of creating events."""

    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_new_paper_returns_is_new_true(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn(lastrowid=1)
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None

        results = PaperSaver.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
            "status": "working_paper",
        }])

        assert len(results) == 1
        assert results[0].is_new is True
        assert results[0].new_to_this_url is True
        assert results[0].status == "working_paper"
        assert results[0].paper_id == 1

    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_duplicate_paper_returns_is_new_false(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn(lastrowid=0)
        mock_get_conn.return_value = conn
        cursor.fetchone.side_effect = [
            (10, "Existing Paper"),
            (None, None, None, None),
            None,
        ]
        cursor.rowcount = 1

        results = PaperSaver.save_publications("http://example.com", [{
            "title": "Existing Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
            "status": "working_paper",
        }])

        assert len(results) == 1
        assert results[0].is_new is False
        assert results[0].new_to_this_url is True
        assert results[0].paper_id == 10

    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_no_feed_events_inserted(self, mock_hash, mock_get_conn, mock_rid):
        """PaperSaver must never INSERT INTO feed_events."""
        conn, cursor = _mock_conn(lastrowid=1)
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None

        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
            "status": "working_paper",
        }])

        feed_inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO feed_events" in str(c)]
        assert len(feed_inserts) == 0, "PaperSaver must not create feed events"


class TestAuthorNormalization:
    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_three_element_author(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None

        PaperSaver.save_publications("http://example.com", [{
            "title": "Test", "authors": [["Jose", "Luis", "Garcia"]], "year": "2024",
        }])

        mock_rid.assert_called_once_with("Jose Luis", "Garcia", conn=conn)

    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_single_element_author(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None

        PaperSaver.save_publications("http://example.com", [{
            "title": "Test", "authors": [["Garcia"]], "year": "2024",
        }])

        mock_rid.assert_called_once_with("", "Garcia", conn=conn)


class TestPageOwnerAuthorship:
    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_verified_complete_byline_does_not_attach_unrelated_owner(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = (55, "Simeon", "Djankov")
        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper", "authors": [["John", "Doe"]], "_authors_complete": True,
        }])
        author_ids = [call.args[1][0] for call in cursor.execute.call_args_list
                      if "INSERT IGNORE INTO authorship" in call.args[0]]
        assert author_ids == [42]

    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_owner_added_with_order_zero(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = (55, "Jane", "Smith")

        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper", "authors": [["John", "Doe"]], "year": "2024",
        }])

        authorship_inserts = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO authorship" in str(c)]
        assert len(authorship_inserts) == 2
        owner_args = authorship_inserts[1][0][1]
        assert owner_args[0] == 55
        assert owner_args[2] == 0


class TestBackfill:
    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_backfills_when_existing_has_nulls(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn(lastrowid=0)
        mock_get_conn.return_value = conn
        cursor.fetchone.side_effect = [(10, "Paper"), (None, None, None, None), None]
        cursor.rowcount = 1

        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper", "authors": [["John", "Doe"]],
            "abstract": "Abstract.", "year": "2024", "venue": "AER",
        }])

        backfill_calls = [c for c in cursor.execute.call_args_list if "COALESCE" in str(c)]
        assert len(backfill_calls) == 1

    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_skips_backfill_when_all_fields_present(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn(lastrowid=0)
        mock_get_conn.return_value = conn
        cursor.fetchone.side_effect = [(10, "Paper"), ("Existing", "2023", "QJE", "published"), None]
        cursor.rowcount = 1

        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper", "authors": [["John", "Doe"]],
            "abstract": "New", "year": "2024", "venue": "AER",
        }])

        backfill_calls = [c for c in cursor.execute.call_args_list if "COALESCE" in str(c)]
        assert len(backfill_calls) == 0

    @pytest.mark.parametrize('status,venue', [
        ('working_paper', 'QJE'), ('revise_and_resubmit', 'NBER Working Paper 1'),
    ])
    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_stale_or_series_venue_cannot_backfill_editorial_venue(
        self, mock_hash, mock_get_conn, mock_rid, status, venue,
    ):
        conn, cursor = _mock_conn(lastrowid=0)
        mock_get_conn.return_value = conn
        cursor.fetchone.side_effect = [(10, 'Paper'), (None, '2025', None, 'revise_and_resubmit'), None]
        PaperSaver.save_publications('http://example.com', [{
            'title': 'Paper', 'authors': [['John', 'Doe']],
            'abstract': 'Missing abstract', 'status': status, 'venue': venue,
        }])
        backfill = [c for c in cursor.execute.call_args_list if 'COALESCE' in str(c)]
        assert len(backfill) == 1
        assert backfill[0].args[1] == ('Missing abstract', None, None, 10)


class TestWithinPaperDedup:
    """Layer 1: compatible-name authors on the same paper share one researcher ID."""

    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_compatible_name_authors_deduplicated(self, mock_hash, mock_get_conn, mock_rid):
        """'M. Steinhardt' and 'Max Steinhardt' on same paper => one get_researcher_id call."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None  # no page owner

        PaperSaver.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["Max", "Steinhardt"], ["M.", "Steinhardt"]],
            "year": "2024",
        }])

        # Layer 1: "M. Steinhardt" should reuse the ID from "Max Steinhardt"
        mock_rid.assert_called_once_with("Max", "Steinhardt", conn=conn)
        # Two authorship inserts (same researcher_id, different author_order)
        authorship_inserts = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO authorship" in str(c)]
        assert len(authorship_inserts) == 2


class TestAuthorLookupCache:
    @patch("backend.pipeline.paper_saver.get_researcher_id", return_value=42)
    @patch("backend.pipeline.paper_saver.get_connection")
    @patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
    def test_same_author_across_pubs_looked_up_once(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None

        pubs = [
            {"title": f"Paper {i}", "authors": [["John", "Doe"], ["Jane", "Smith"]], "year": "2024"}
            for i in range(3)
        ]

        PaperSaver.save_publications("http://example.com", pubs)
        assert mock_rid.call_count == 2


class TestApplyTitleRenameCollision:
    """A rename onto another paper's title merges one work or keeps both; never raises."""

    def _run(self, dup_id=None, merge=False):
        """Run apply_title_rename with a mocked connection; return (outcome, SQL list, conn, merge mock)."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (dup_id,) if dup_id else None
        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        merge_kwargs = {"side_effect": merge} if isinstance(merge, Exception) else {"return_value": merge}
        with (
            patch("backend.pipeline.paper_saver.get_connection", return_value=conn),
            patch("backend.pipeline.paper_saver.append_paper_snapshot"),
            patch("backend.pipeline.paper_saver.compute_title_hash", return_value="newhash"),
            patch("backend.enrichment.paper_merge.merge_paper_group", **merge_kwargs) as merge_mock,
        ):
            outcome = PaperSaver.apply_title_rename(
                1, "Old Title", "New Title",
                {"status": "working_paper"}, "http://example.com",
            )
        return outcome, [str(c[0][0]) for c in cursor.execute.call_args_list], conn, merge_mock

    def test_same_work_collision_merges_under_new_title(self):
        outcome, sqls, conn, merge = self._run(dup_id=77, merge=True)
        assert outcome == PaperSaver.MERGED
        merge.assert_called_once_with([1, 77], as_title="New Title")
        assert not any("UPDATE papers SET title" in s for s in sqls)
        conn.commit.assert_not_called()

    def test_different_work_collision_skips_rename_and_warns_once(self, caplog):
        with caplog.at_level("WARNING"):
            outcome, sqls, conn, merge = self._run(dup_id=77, merge=False)
        assert outcome == PaperSaver.SKIPPED
        assert not any("UPDATE papers" in s or "DELETE" in s for s in sqls)
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1 and "paper 1" in warnings[0] and "paper 77" in warnings[0]

    def test_failed_merge_is_a_skip_not_an_error(self):
        outcome, _, _, _ = self._run(dup_id=77, merge=RuntimeError("deadlock"))
        assert outcome == PaperSaver.SKIPPED

    def test_no_collision_single_update_no_deletes(self):
        outcome, sqls, conn, merge = self._run(dup_id=None)
        assert outcome == PaperSaver.RENAMED
        assert any("UPDATE papers SET title" in s for s in sqls)
        assert not any("DELETE FROM papers" in s for s in sqls)
        merge.assert_not_called()
        conn.commit.assert_called_once()

    def test_reconcile_reports_only_applied_renames(self):
        existing = [{"id": 1, "title": "Cash Transfers and Role Models in Rural Kenya", "title_hash": "h"}]
        pubs = [{"title": "Cash Transfers, Role Models and Women in Rural Kenya"}]
        with (
            patch("backend.pipeline.paper_saver.fetch_all", return_value=existing),
            patch.object(PaperSaver, "apply_title_rename", return_value=PaperSaver.MERGED) as apply,
        ):
            assert PaperSaver.reconcile_title_renames("http://example.com", pubs) == []
        apply.assert_called_once()
