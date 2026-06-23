"""Tests for title cleaning, normalization, and year coercion — catches bugs like PR #151, #146.

PR #151: CSS-styled pages yielded lowercase titles stored as-is.
PR #146: 65% of new_paper events had NULL year because the LLM returned non-standard formats.
"""
import pytest
from backend.pipeline.publication import clean_title, PublicationExtraction
from backend.database.papers import normalize_title, compute_title_hash


class TestCleanTitleCapitalization:
    """clean_title() must capitalize the first letter (PR #151)."""

    def test_lowercase_title_gets_capitalized(self):
        assert clean_title("monetary policy in europe") == "Monetary policy in europe"

    def test_already_capitalized_is_unchanged(self):
        assert clean_title("Monetary Policy in Europe") == "Monetary Policy in Europe"

    def test_all_caps_is_unchanged(self):
        assert clean_title("MONETARY POLICY") == "MONETARY POLICY"

    def test_numeric_start_is_unchanged(self):
        assert clean_title("3rd wave of immigration") == "3rd wave of immigration"

    def test_quoted_start_is_unchanged(self):
        assert clean_title('"quoted title" in economics') == '"quoted title" in economics'

    def test_empty_string_returns_empty(self):
        assert clean_title("") == ""

    def test_whitespace_only_returns_empty(self):
        assert clean_title("   ") == ""

    def test_single_char_lowercase(self):
        assert clean_title("x") == "X"


class TestCleanTitleMetadataStripping:
    """clean_title() strips metadata suffixes before capitalizing."""

    @pytest.mark.parametrize("suffix", [
        " — Working Paper",
        " -- JMP",
        " – Draft",
        " — Job Market Paper",
        " — New",
        " — Revised",
        " — Forthcoming",
        " — Under Review",
        " — R&R",
    ])
    def test_dash_suffixes_stripped(self, suffix):
        result = clean_title(f"My Paper Title{suffix}")
        assert result == "My Paper Title"

    @pytest.mark.parametrize("suffix", [
        " [Working Paper]",
        " [JMP]",
        " [Draft]",
        " (Working Paper)",
        " (New!)",
        " [Revised]",
        " [Updated]",
        " (Submitted)",
    ])
    def test_bracket_suffixes_stripped(self, suffix):
        result = clean_title(f"My Paper Title{suffix}")
        assert result == "My Paper Title"

    def test_metadata_stripped_before_capitalization(self):
        result = clean_title("trade and wages — working paper")
        assert result == "Trade and wages"

    def test_no_metadata_preserved(self):
        assert clean_title("Trade and Wages") == "Trade and Wages"


class TestNormalizeTitleDedup:
    """normalize_title() for dedup hashing (PR #151 notes title_hash unaffected)."""

    def test_lowercase_and_strip_punctuation(self):
        assert normalize_title("Trade & Wages: Evidence") == "trade wages evidence"

    def test_collapse_whitespace(self):
        assert normalize_title("Trade   and   Wages") == "trade and wages"

    def test_case_insensitive_dedup(self):
        assert normalize_title("TRADE AND WAGES") == normalize_title("trade and wages")

    def test_punctuation_stripped_for_dedup(self):
        """Punctuation like '&' is stripped — 'Trade & Wages' normalizes to 'trade wages'."""
        assert normalize_title("Trade & Wages") == "trade wages"

    def test_none_returns_empty(self):
        assert normalize_title(None) == ""

    def test_empty_returns_empty(self):
        assert normalize_title("") == ""

    def test_hash_stable_across_case_variants(self):
        h1 = compute_title_hash("My Paper Title")
        h2 = compute_title_hash("my paper title")
        assert h1 == h2

    def test_hash_stable_across_punctuation_variants(self):
        h1 = compute_title_hash("Trade & Wages: Evidence from Germany")
        h2 = compute_title_hash("Trade  Wages Evidence from Germany")
        assert h1 == h2


class TestYearCoercion:
    """PublicationExtraction.coerce_year_to_str handles non-standard year formats (PR #146)."""

    @pytest.mark.parametrize("input_val,expected", [
        ("2024", "2024"),
        (2024, "2024"),
        ("2024a", "2024"),
        ("forthcoming 2025", "2025"),
        ("2023-24", "2023"),
        ("Revised 2024", "2024"),
        (None, None),
        ("", None),
        ("   ", None),
    ])
    def test_year_coercion(self, input_val, expected):
        result = PublicationExtraction.coerce_year_to_str(input_val)
        assert result == expected

    def test_full_model_with_integer_year(self):
        pub = PublicationExtraction(
            title="Test Paper",
            authors=[["Jane", "Doe"]],
            year=2024,
        )
        assert pub.year == "2024"

    def test_full_model_with_forthcoming_year(self):
        pub = PublicationExtraction(
            title="Test Paper",
            authors=[["Jane", "Doe"]],
            year="forthcoming 2025",
        )
        assert pub.year == "2025"

    def test_full_model_with_none_year(self):
        pub = PublicationExtraction(
            title="Test Paper",
            authors=[["Jane", "Doe"]],
            year=None,
        )
        assert pub.year is None
