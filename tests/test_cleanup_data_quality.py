"""Tests for the pure helpers in scripts/cleanup_data_quality.py.

The DB-touching steps are exercised against the real mirror via
`pytest tests_data_quality` before/after a run; these tests pin the text-repair and
duplicate-detection logic that decides WHAT gets rewritten.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cleanup_data_quality.py"
_spec = importlib.util.spec_from_file_location("cleanup_data_quality", _SCRIPT)
cleanup = importlib.util.module_from_spec(_spec)
sys.modules["cleanup_data_quality"] = cleanup
_spec.loader.exec_module(cleanup)


class TestRepairMojibake:
    def test_round_trip_utf8_as_latin1(self):
        assert cleanup.repair_mojibake("SamfunnsÃ¸konomen") == "Samfunnsøkonomen"

    def test_round_trip_cp1252_smart_punctuation(self):
        assert cleanup.repair_mojibake("worldâ€™s fair") == "world’s fair"

    def test_lossy_fallback_for_eaten_dash_byte(self):
        """'â€\"' (the 0x93/0x94 byte became a straight quote) cannot round-trip
        but the sequence map recovers a dash."""
        assert cleanup.repair_mojibake('Core â€" Periphery') == "Core – Periphery"

    def test_lossy_fallback_for_eaten_continuation_byte(self):
        assert cleanup.repair_mojibake("SedlÃ¡Ä ek") == "Sedláček"

    def test_mixed_clean_and_mojibake_text(self):
        """Strings mixing proper curly quotes with mojibake can't round-trip
        (the quote's cp1252 byte is not valid UTF-8) — map still fixes them."""
        assert cleanup.repair_mojibake("a “diÃ¤ferent” case") == "a “diäferent” case"

    def test_accent_bigrams(self):
        assert cleanup.repair_mojibake("GonzÃ¡lez") == "González"
        assert cleanup.repair_mojibake("Ã©tude") == "étude"


class TestIsMojibake:
    def test_bare_a_tilde_is_legitimate(self):
        """Portuguese all-caps (SÃO) must not be flagged."""
        assert cleanup.is_mojibake("SÃO PAULO") is False

    def test_bigram_flags(self):
        assert cleanup.is_mojibake("Ã©") is True
        assert cleanup.is_mojibake("â€œquoteâ€") is True

    def test_clean_text_passes(self):
        assert cleanup.is_mojibake("Sedláček étude ø å") is False
        assert cleanup.is_mojibake(None) is False
        assert cleanup.is_mojibake("") is False


class TestDistinctMarkers:
    """The guard deciding which near-duplicate pairs are genuinely different
    papers. Must stay in sync with the data-quality check's exclusion list."""

    @pytest.mark.parametrize("word", [
        "2012", "ii", "comment", "reply", "corrigendum", "erratum",
        "appendix", "part", "revisited", "updated",
    ])
    def test_distinct_paper_words_block_merge(self, word):
        assert cleanup._DISTINCT_MARKERS.search(word)

    @pytest.mark.parametrize("word", [
        "the", "and", "wages", "evidence", "germany", "social",
        "variant",  # contains 'i' but is not the roman numeral
    ])
    def test_ordinary_title_words_allow_merge(self, word):
        assert not cleanup._DISTINCT_MARKERS.search(word)

    def test_matches_data_quality_check(self):
        """Literal sync check against tests_data_quality/test_paper_quality.py."""
        dq = (Path(__file__).resolve().parent.parent
              / "tests_data_quality" / "test_paper_quality.py").read_text()
        assert "comment|reply|rejoinder|appendix|corrigendum|erratum" in dq
        assert cleanup._DISTINCT_MARKERS.pattern.count("corrigendum") == 1


class TestRestoreReasoningDescriptions:
    """Issue #258: bios holding <thought> reasoning are salvaged, restored or NULLed,
    with backups that --undo can put back."""

    BAD = "<thought>* Source: Text from a researcher's homepage. * Task: Extract a bio"
    SALVAGEABLE = ("<thought>* Task: Extract a professional description.</thought> "
                   "Jane Doe is a professor at MIT. She studies trade and growth in")
    CLEAN = "Thomas Rieger is a researcher in behavioral finance."

    def _run(self, monkeypatch, researchers, snapshots_by_researcher, bad_snapshots, apply):
        from unittest.mock import MagicMock

        def fake_fetch_all(sql, params=()):
            if sql.startswith("SELECT id, description FROM researchers "):
                return researchers
            if sql.startswith("SELECT id, description FROM researcher_snapshots"):
                return bad_snapshots
            return snapshots_by_researcher.get(params[0], [])

        cursor = MagicMock()
        monkeypatch.setattr(cleanup, "fetch_all", fake_fetch_all)
        monkeypatch.setattr(cleanup, "APPLY", apply)
        monkeypatch.setattr(cleanup, "_run_writes", lambda label, fn: fn(cursor) if apply else None)
        n = cleanup.restore_reasoning_descriptions()
        return n, cursor

    @staticmethod
    def _sql(cursor, prefix):
        return [c.args for c in cursor.execute.call_args_list if c.args[0].lstrip().startswith(prefix)]

    def test_salvage_then_snapshot_then_null(self, monkeypatch):
        researchers = [
            {"id": 2, "description": self.SALVAGEABLE},
            {"id": 3, "description": self.BAD},
            {"id": 4, "description": self.BAD},
            {"id": 5, "description": "A genuine bio about an economist at MIT."},  # SQL over-select
        ]
        snaps = {
            2: [{"description": self.CLEAN}],  # salvage wins over the snapshot
            3: [{"description": self.BAD}, {"description": self.CLEAN}],
            4: [{"description": self.BAD}],
        }
        bad_snaps = [{"id": 11, "description": self.BAD}, {"id": 12, "description": "fine bio"}]
        n, cursor = self._run(monkeypatch, researchers, snaps, bad_snaps, apply=True)
        assert n == 4  # three researchers + one snapshot
        updates = [args[1] for args in self._sql(cursor, "UPDATE researchers")]
        assert updates == [
            ("Jane Doe is a professor at MIT.", 2, self.SALVAGEABLE),
            (self.CLEAN, 3, self.BAD),
            (4, self.BAD),
        ]
        # Restored/salvaged bios bump description_updated_at; NULLed ones do not.
        stmts = [args[0] for args in self._sql(cursor, "UPDATE researchers")]
        assert ["description_updated_at" in s for s in stmts] == [True, True, False]
        assert [args[1] for args in self._sql(cursor, "DELETE FROM researcher_snapshots")] == [(11,)]

    def test_backups_are_written_before_changes(self, monkeypatch):
        n, cursor = self._run(monkeypatch, [{"id": 4, "description": self.BAD}], {},
                              [{"id": 11, "description": self.BAD}], apply=True)
        order = [c.args[0].split()[0:3] for c in cursor.execute.call_args_list]
        first_update = next(i for i, o in enumerate(order) if o[0] == "UPDATE")
        first_delete = next(i for i, o in enumerate(order) if o[0] == "DELETE")
        backup_snap = next(i for i, o in enumerate(order)
                           if o[:3] == ["INSERT", "IGNORE", "INTO"])
        assert cursor.executemany.called  # bio backup rows
        assert backup_snap < first_delete
        assert any("CREATE TABLE IF NOT EXISTS researchers_description_backup" in c.args[0]
                   for c in cursor.execute.call_args_list[:first_update])
        rows = cursor.executemany.call_args.args[1]
        assert rows == [(4, self.BAD, None)]

    def test_dry_run_writes_nothing(self, monkeypatch):
        n, cursor = self._run(monkeypatch, [{"id": 3, "description": self.BAD}], {}, [], apply=False)
        assert n == 1
        cursor.execute.assert_not_called()

    def test_step_is_registered_with_undo(self):
        assert "restore_reasoning_descriptions" in cleanup.STEP_ORDER
        assert "restore_reasoning_descriptions" in cleanup.UNDO_STEPS

    def test_undo_only_reverts_unchanged_bios(self, monkeypatch):
        from unittest.mock import MagicMock
        backup = [
            {"id": 9, "researcher_id": 3, "description": self.BAD, "new_description": self.CLEAN},
            {"id": 5, "researcher_id": 3, "description": "older", "new_description": "x"},
        ]
        cursor = MagicMock()
        monkeypatch.setattr(cleanup, "_table_exists", lambda name: True)
        monkeypatch.setattr(cleanup, "fetch_all", lambda sql, params=(): backup)
        monkeypatch.setattr(cleanup, "fetch_one", lambda sql, params=(): {"n": 0})
        monkeypatch.setattr(cleanup, "_run_writes", lambda label, fn: fn(cursor))
        cleanup.undo_restore_reasoning_descriptions()
        updates = [c.args for c in cursor.execute.call_args_list
                   if c.args[0].startswith("UPDATE researchers")]
        # Newest backup per researcher; guarded on the value the cleanup wrote.
        assert updates == [(
            "UPDATE researchers SET description = %s WHERE id = %s AND description <=> %s",
            (self.BAD, 3, self.CLEAN))]


class TestSalvageDescription:
    def test_answer_after_closed_thought_is_kept(self):
        from backend.pipeline.description import salvage_description
        assert salvage_description(
            "<thought>notes</thought> Jane Doe is a professor of economics at MIT."
        ) == "Jane Doe is a professor of economics at MIT."

    def test_cut_off_answer_is_trimmed_to_last_sentence(self):
        from backend.pipeline.description import salvage_description
        assert salvage_description(
            "<thought>notes</thought> Jane Doe is a professor at MIT. She studies trade in"
        ) == "Jane Doe is a professor at MIT."

    def test_too_short_after_trim_or_unclosed_is_none(self):
        from backend.pipeline.description import salvage_description
        assert salvage_description("<thought>notes</thought> Hi. She studies trade in") is None
        assert salvage_description("<thought>* Task: Extract a bio") is None


@pytest.mark.parametrize("cut, expected", [
    # Prod examples (2026-11-27) that a plain ". " split cut after an abbreviation.
    ("Harry Cooperman is a Ph.D. candidate at Yale. He studies",
     "Harry Cooperman is a Ph.D. candidate at Yale."),
    ("Dimitris Papanikolaou is the John L. and Helen Kellogg Professor of Finance. His research",
     "Dimitris Papanikolaou is the John L. and Helen Kellogg Professor of Finance."),
    ("Adlai Fisher is the A. Smith Chair at UBC. He", "Adlai Fisher is the A. Smith Chair at UBC."),
    ("Jane Doe works at the U.S. Treasury in", None),
    ("Adlai Fisher is the A.", None),
    ("Jane Doe is a professor at MIT. She holds a Ph.D.", "Jane Doe is a professor at MIT."),
])
def test_salvage_does_not_split_on_abbreviations(cut, expected):
    from backend.pipeline.description import salvage_description
    assert salvage_description(f"<thought>notes</thought> {cut}") == expected
