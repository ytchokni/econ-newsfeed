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
