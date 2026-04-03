"""Tests for researcher_fields derivation from JEL codes."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import MagicMock, patch


def _make_mock_conn():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestJelToFieldMapping:
    """Tests for _JEL_TO_FIELD_SLUGS mapping."""

    def test_macro_maps_to_macroeconomics(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        assert _JEL_TO_FIELD_SLUGS["E"] == "macroeconomics"

    def test_labour_maps_to_labour_economics(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        assert _JEL_TO_FIELD_SLUGS["J"] == "labour-economics"

    def test_finance_maps_to_finance(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        assert _JEL_TO_FIELD_SLUGS["G"] == "finance"

    def test_all_11_mapped_codes_present(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        expected_codes = {"C", "E", "F", "G", "H", "I", "J", "L", "O", "P", "Z"}
        assert set(_JEL_TO_FIELD_SLUGS.keys()) == expected_codes

    def test_unmapped_codes_not_present(self):
        from database.jel import _JEL_TO_FIELD_SLUGS
        for code in ["A", "B", "D", "K", "M", "N", "Q", "R", "Y"]:
            assert code not in _JEL_TO_FIELD_SLUGS


class TestSyncResearcherFieldsFromJel:
    """Tests for sync_researcher_fields_from_jel."""

    def test_deletes_existing_and_inserts_new_fields(self):
        mock_conn, mock_cursor = _make_mock_conn()
        # fetchone returns description, fetchall returns field IDs
        mock_cursor.fetchone.return_value = ("studies labour markets",)
        mock_cursor.fetchall.return_value = [(1,), (2,)]

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=["J", "E"])

        all_sql = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("SELECT description FROM researchers" in sql for sql in all_sql)
        assert any("DELETE FROM researcher_fields" in sql for sql in all_sql)
        assert any("SELECT id FROM research_fields" in sql for sql in all_sql)
        assert any("INSERT IGNORE INTO researcher_fields" in sql for sql in all_sql)
        mock_conn.commit.assert_called_once()

    def test_empty_jel_codes_still_clears_fields(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = (None,)

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=[])

        all_sql = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("DELETE FROM researcher_fields" in sql for sql in all_sql)
        assert not any("INSERT" in sql for sql in all_sql)

    def test_migration_keyword_matching(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = ("studies international migration patterns",)
        mock_cursor.fetchall.return_value = [(1,), (2,)]

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=["J"])

        select_call = [c for c in mock_cursor.execute.call_args_list if "SELECT id FROM research_fields" in c[0][0]]
        assert len(select_call) == 1
        slugs_param = select_call[0][0][1]
        assert "migration" in slugs_param
        assert "labour-economics" in slugs_param

    def test_no_description_skips_migration_check(self):
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = None
        mock_cursor.fetchall.return_value = [(1,)]

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import sync_researcher_fields_from_jel
            sync_researcher_fields_from_jel(researcher_id=1, jel_codes=["E"])

        select_call = [c for c in mock_cursor.execute.call_args_list if "SELECT id FROM research_fields" in c[0][0]]
        assert len(select_call) == 1
        slugs_param = select_call[0][0][1]
        assert "macroeconomics" in slugs_param


class TestSaveResearcherJelCodesCallsSync:
    """Verify save_researcher_jel_codes triggers field sync."""

    def test_calls_sync_after_saving_codes(self):
        mock_conn, mock_cursor = _make_mock_conn()

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel.sync_researcher_fields_from_jel") as mock_sync,
        ):
            from database.jel import save_researcher_jel_codes
            save_researcher_jel_codes(researcher_id=1, jel_codes=["J", "E"])

        mock_sync.assert_called_once_with(1, ["J", "E"])


class TestAddResearcherJelCodesCallsSync:
    """Verify add_researcher_jel_codes syncs with the full code set."""

    def test_calls_sync_with_all_codes(self):
        mock_conn, mock_cursor = _make_mock_conn()

        with (
            patch("database.jel.get_connection", return_value=mock_conn),
            patch("database.jel._get_all_jel_codes_for_researcher", return_value=["F", "G", "J"]) as mock_get_all,
            patch("database.jel.sync_researcher_fields_from_jel") as mock_sync,
        ):
            from database.jel import add_researcher_jel_codes
            add_researcher_jel_codes(researcher_id=1, jel_codes=["F", "G"])

        mock_get_all.assert_called_once_with(1)
        # Sync should receive the full set (F, G, J), not just the new codes (F, G)
        mock_sync.assert_called_once_with(1, ["F", "G", "J"])
