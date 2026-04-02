"""Tests for encoding_guard module — mojibake detection, fixing, and field guarding."""
import logging

import pytest


# Known mojibake → correct pairs from real data
MOJIBAKE_PAIRS = [
    ("fÃ¼r", "für"),
    ("Ã©conomie", "économie"),
    ("Ã¶konometrie", "ökonometrie"),
    ("seÃ±or", "señor"),
    ("schÃ¤tzen", "schätzen"),
    ("UniversitÃ¤t", "Universität"),
    ("FrÃ©dÃ©ric", "Frédéric"),
    ("GÃ¶ttingen", "Göttingen"),
]

CLEAN_UNICODE = [
    "München",
    "café",
    "señor",
    "naïve",
    "Zürich",
    "François",
    "Ströbele",
]


class TestHasMojibake:
    @pytest.mark.parametrize("garbled,_", MOJIBAKE_PAIRS)
    def test_detects_mojibake(self, garbled, _):
        from encoding_guard import has_mojibake
        assert has_mojibake(garbled) is True

    @pytest.mark.parametrize("clean", CLEAN_UNICODE)
    def test_clean_unicode_not_flagged(self, clean):
        from encoding_guard import has_mojibake
        assert has_mojibake(clean) is False

    def test_empty_string(self):
        from encoding_guard import has_mojibake
        assert has_mojibake("") is False

    def test_ascii_only(self):
        from encoding_guard import has_mojibake
        assert has_mojibake("hello world") is False


class TestFixEncoding:
    @pytest.mark.parametrize("garbled,expected", MOJIBAKE_PAIRS)
    def test_fixes_mojibake(self, garbled, expected):
        from encoding_guard import fix_encoding
        fixed, was_changed = fix_encoding(garbled)
        assert fixed == expected
        assert was_changed is True

    @pytest.mark.parametrize("clean", CLEAN_UNICODE)
    def test_clean_unicode_unchanged(self, clean):
        from encoding_guard import fix_encoding
        fixed, was_changed = fix_encoding(clean)
        assert fixed == clean
        assert was_changed is False

    def test_empty_string(self):
        from encoding_guard import fix_encoding
        fixed, was_changed = fix_encoding("")
        assert fixed == ""
        assert was_changed is False


class TestGuardTextFields:
    def test_fixes_mojibake_fields(self):
        from encoding_guard import guard_text_fields
        row = {"title": "fÃ¼r die Wirtschaft", "year": "2024", "venue": None}
        result = guard_text_fields(row, ["title", "venue"], context="papers (id=1)")
        assert result["title"] == "für die Wirtschaft"
        assert result["year"] == "2024"  # untouched — not in fields list
        assert result["venue"] is None   # None skipped

    def test_clean_unicode_unchanged(self):
        from encoding_guard import guard_text_fields
        row = {"title": "Universität München", "abstract": "café society"}
        result = guard_text_fields(row, ["title", "abstract"], context="papers (id=2)")
        assert result["title"] == "Universität München"
        assert result["abstract"] == "café society"

    def test_missing_field_skipped(self):
        from encoding_guard import guard_text_fields
        row = {"title": "Hello"}
        result = guard_text_fields(row, ["title", "abstract"], context="papers (id=3)")
        assert result["title"] == "Hello"
        assert "abstract" not in result

    def test_logs_warning_on_fix(self, caplog):
        from encoding_guard import guard_text_fields
        row = {"title": "fÃ¼r"}
        with caplog.at_level(logging.WARNING, logger="encoding_guard"):
            guard_text_fields(row, ["title"], context="papers (id=42)")
        assert "Mojibake fixed" in caplog.text
        assert "fÃ¼r" in caplog.text
        assert "für" in caplog.text
        assert "papers (id=42)" in caplog.text

    def test_no_warning_for_clean_text(self, caplog):
        from encoding_guard import guard_text_fields
        row = {"title": "München"}
        with caplog.at_level(logging.WARNING, logger="encoding_guard"):
            guard_text_fields(row, ["title"], context="papers (id=1)")
        assert "Mojibake fixed" not in caplog.text
