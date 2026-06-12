"""Tests for PaperSaver — persistence logic separated from event creation."""
import pytest
from unittest.mock import patch, MagicMock
from paper_saver import PaperSaver, SaveResult, _author_id_cache


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

    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
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

    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
    def test_duplicate_paper_returns_is_new_false(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn(lastrowid=0)
        mock_get_conn.return_value = conn
        cursor.fetchone.side_effect = [
            (10,),
            (None, None, None),
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

    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
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
    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
    def test_three_element_author(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None

        PaperSaver.save_publications("http://example.com", [{
            "title": "Test", "authors": [["Jose", "Luis", "Garcia"]], "year": "2024",
        }])

        mock_rid.assert_called_once_with("Jose Luis", "Garcia", conn=conn)

    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
    def test_single_element_author(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None

        PaperSaver.save_publications("http://example.com", [{
            "title": "Test", "authors": [["Garcia"]], "year": "2024",
        }])

        mock_rid.assert_called_once_with("", "Garcia", conn=conn)


class TestPageOwnerAuthorship:
    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
    def test_owner_added_with_order_zero(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = (55,)

        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper", "authors": [["John", "Doe"]], "year": "2024",
        }])

        authorship_inserts = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO authorship" in str(c)]
        assert len(authorship_inserts) == 2
        owner_args = authorship_inserts[1][0][1]
        assert owner_args[0] == 55
        assert owner_args[2] == 0


class TestBackfill:
    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
    def test_backfills_when_existing_has_nulls(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn(lastrowid=0)
        mock_get_conn.return_value = conn
        cursor.fetchone.side_effect = [(10,), (None, None, None), None]
        cursor.rowcount = 1

        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper", "authors": [["John", "Doe"]],
            "abstract": "Abstract.", "year": "2024", "venue": "AER",
        }])

        backfill_calls = [c for c in cursor.execute.call_args_list if "COALESCE" in str(c)]
        assert len(backfill_calls) == 1

    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
    def test_skips_backfill_when_all_fields_present(self, mock_hash, mock_get_conn, mock_rid):
        conn, cursor = _mock_conn(lastrowid=0)
        mock_get_conn.return_value = conn
        cursor.fetchone.side_effect = [(10,), ("Existing", "2023", "QJE"), None]
        cursor.rowcount = 1

        PaperSaver.save_publications("http://example.com", [{
            "title": "Paper", "authors": [["John", "Doe"]],
            "abstract": "New", "year": "2024", "venue": "AER",
        }])

        backfill_calls = [c for c in cursor.execute.call_args_list if "COALESCE" in str(c)]
        assert len(backfill_calls) == 0


class TestAuthorLookupCache:
    @patch("paper_saver.get_researcher_id", return_value=42)
    @patch("paper_saver.get_connection")
    @patch("paper_saver.compute_title_hash", return_value="abc123")
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
    """apply_title_rename must absorb an existing paper with the target
    title_hash BEFORE updating the title — updating first violates
    uq_title_hash and crashes the extraction worker (#177)."""

    def _run(self, dup_id=None):
        """Run apply_title_rename with a mocked connection; return executed SQL list."""
        cursor = MagicMock()
        cursor.fetchone.return_value = (dup_id,) if dup_id else None
        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        with (
            patch("paper_saver.get_connection", return_value=conn),
            patch("paper_saver.append_paper_snapshot"),
            patch("paper_saver.compute_title_hash", return_value="newhash"),
        ):
            PaperSaver.apply_title_rename(
                1, "Old Title", "New Title",
                {"status": "working_paper"}, "http://example.com",
            )
        return [str(c[0][0]) for c in cursor.execute.call_args_list], conn

    def test_collision_absorbed_before_title_update(self):
        sqls, _ = self._run(dup_id=77)
        update_idx = next(i for i, s in enumerate(sqls) if "UPDATE papers SET title" in s)
        delete_idx = next(i for i, s in enumerate(sqls) if "DELETE FROM papers" in s)
        assert delete_idx < update_idx, (
            "duplicate paper must be removed BEFORE the title UPDATE, "
            f"or the UPDATE violates uq_title_hash; order was: {sqls}"
        )

    def test_collision_moves_authorship_to_survivor(self):
        sqls, _ = self._run(dup_id=77)
        assert any("authorship" in s and "UPDATE IGNORE" in s for s in sqls), (
            "dup paper's authorship rows must be reassigned, not dropped"
        )

    def test_collision_moves_links_and_snapshots(self):
        sqls, _ = self._run(dup_id=77)
        assert any("paper_links" in s for s in sqls)
        assert any("paper_snapshots" in s for s in sqls)

    def test_collision_deletes_dup_feed_events(self):
        """Dup's feed events are deleted (not reassigned) to avoid duplicate
        new_paper events on the surviving paper."""
        sqls, _ = self._run(dup_id=77)
        assert any("DELETE FROM feed_events" in s for s in sqls)

    def test_no_collision_single_update_no_deletes(self):
        sqls, conn = self._run(dup_id=None)
        assert any("UPDATE papers SET title" in s for s in sqls)
        assert not any("DELETE FROM papers" in s for s in sqls)
        conn.commit.assert_called_once()
