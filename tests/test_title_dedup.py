"""Tests for title normalization and hashing (Database.normalize_title / compute_title_hash).

These methods are the foundation of cross-researcher paper deduplication introduced
in the v2 feature. All tests operate purely on the static methods — no DB connection
is required.
"""
import hashlib
import re

import pytest

from database import Database


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    """Database.normalize_title() produces a deterministic, canonical form."""

    def test_lowercases_input(self):
        assert Database.normalize_title("Trade And Wages") == "trade and wages"

    def test_strips_leading_trailing_whitespace(self):
        assert Database.normalize_title("  trade and wages  ") == "trade and wages"

    def test_collapses_internal_whitespace(self):
        """Multiple consecutive spaces are collapsed to a single space."""
        assert Database.normalize_title("trade  and   wages") == "trade and wages"

    def test_strips_punctuation(self):
        """Punctuation is removed entirely, not replaced."""
        # Commas, hyphens, colons, question marks must be stripped
        assert Database.normalize_title("Trade, Wages, and Growth") == "trade wages and growth"
        assert Database.normalize_title("Self-Employment: Evidence") == "selfemployment evidence"
        assert Database.normalize_title("What Happens?") == "what happens"

    def test_strips_parentheses_and_brackets(self):
        assert Database.normalize_title("Labour Markets (Revisited)") == "labour markets revisited"

    def test_strips_quotes_and_apostrophes(self):
        assert Database.normalize_title("Women's Labour Force") == "womens labour force"

    def test_strips_slashes_and_ampersands(self):
        """& and / are stripped; surrounding spaces collapse to a single space."""
        result = Database.normalize_title("Trade & Growth / Evidence")
        # Punctuation is stripped, whitespace is collapsed — no double spaces
        assert "  " not in result
        # Core words survive
        assert "trade" in result
        assert "growth" in result
        assert "evidence" in result

    def test_preserves_digits(self):
        """Digits are kept — they can be part of years, equations, etc."""
        assert Database.normalize_title("G20 Summit 2024") == "g20 summit 2024"

    def test_empty_string_returns_empty(self):
        assert Database.normalize_title("") == ""

    def test_none_returns_empty(self):
        """None input should return empty string without raising."""
        assert Database.normalize_title(None) == ""

    def test_whitespace_only_returns_empty(self):
        assert Database.normalize_title("   ") == ""

    def test_unicode_letters_outside_az_removed(self):
        """Only a-z, 0-9, and spaces remain after normalization."""
        result = Database.normalize_title("Café Economics")
        # 'é' is not in [a-z0-9] so should be stripped
        assert re.fullmatch(r'[a-z0-9 ]*', result), f"Unexpected chars in: {result!r}"

    def test_idempotent(self):
        """Normalizing an already-normalized string is a no-op."""
        title = "trade and wages"
        assert Database.normalize_title(title) == title

    def test_equivalent_titles_produce_same_normalized_form(self):
        """Titles that differ only in casing and punctuation normalize identically."""
        variants = [
            "Trade and Wages",
            "TRADE AND WAGES",
            "trade and wages",
            "  Trade  And  Wages  ",
            "Trade, and Wages!",
        ]
        expected = Database.normalize_title(variants[0])
        for variant in variants[1:]:
            assert Database.normalize_title(variant) == expected, (
                f"Variant {variant!r} normalized differently"
            )


# ---------------------------------------------------------------------------
# compute_title_hash
# ---------------------------------------------------------------------------

class TestComputeTitleHash:
    """Database.compute_title_hash() is deterministic and collision-resistant."""

    def test_returns_64_char_hex_string(self):
        """SHA-256 digest is always 64 hex characters."""
        h = Database.compute_title_hash("Trade and Wages")
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic_same_input(self):
        """Same title produces the same hash on every call."""
        title = "Immigration Effects on Labor Markets"
        assert Database.compute_title_hash(title) == Database.compute_title_hash(title)

    def test_equivalent_titles_produce_same_hash(self):
        """Titles that normalize identically must hash identically."""
        assert (
            Database.compute_title_hash("Trade and Wages")
            == Database.compute_title_hash("TRADE AND WAGES")
        )
        assert (
            Database.compute_title_hash("Trade, and Wages!")
            == Database.compute_title_hash("  Trade  And  Wages  ")
        )

    def test_different_titles_produce_different_hashes(self):
        """Distinct normalized titles must produce distinct hashes."""
        h1 = Database.compute_title_hash("Trade and Wages")
        h2 = Database.compute_title_hash("Immigration and Growth")
        assert h1 != h2

    def test_hash_matches_manual_sha256_of_normalized(self):
        """Hash must equal SHA-256(normalized title)."""
        title = "Labor Market Effects of Immigration"
        normalized = Database.normalize_title(title)
        expected = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        assert Database.compute_title_hash(title) == expected

    def test_empty_title_returns_hash_of_empty_string(self):
        """compute_title_hash('') should not raise and should hash ''."""
        h = Database.compute_title_hash("")
        expected = hashlib.sha256(b"").hexdigest()
        assert h == expected

    def test_none_title_returns_hash_of_empty_string(self):
        """compute_title_hash(None) delegates to normalize_title which returns ''."""
        h = Database.compute_title_hash(None)
        expected = hashlib.sha256(b"").hexdigest()
        assert h == expected

    def test_punctuation_only_title_hashes_as_empty(self):
        """A title that is purely punctuation normalizes to '' and hashes accordingly."""
        h = Database.compute_title_hash("!!!")
        expected = hashlib.sha256(b"").hexdigest()
        assert h == expected
