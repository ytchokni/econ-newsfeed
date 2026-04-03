"""Tests for researcher and paper snapshot content hashing (Database._compute_*_content_hash).

These static methods drive the append-only change-detection logic introduced in v2.
All tests are pure unit tests — no DB connection required.
"""
import hashlib

import pytest

from database import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(*parts) -> str:
    """Reproduce the hashing logic: join with '||' then SHA-256."""
    joined = "||".join(str(v or "") for v in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# _compute_researcher_content_hash
# ---------------------------------------------------------------------------

class TestResearcherContentHash:
    """Database._compute_researcher_content_hash() for change detection."""

    def test_returns_64_char_hex_string(self):
        h = Database._compute_researcher_content_hash("Professor", "MIT", "Researcher bio")
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic_same_inputs(self):
        """Calling twice with the same args produces the same hash."""
        args = ("Associate Professor", "Harvard", "Studies labor economics.")
        h1 = Database._compute_researcher_content_hash(*args)
        h2 = Database._compute_researcher_content_hash(*args)
        assert h1 == h2

    def test_matches_manual_sha256(self):
        """Must equal SHA-256('position||affiliation||description')."""
        pos, aff, desc = "Professor", "Princeton", "Macro economist."
        expected = _sha256(pos, aff, desc)
        assert Database._compute_researcher_content_hash(pos, aff, desc) == expected

    def test_different_position_produces_different_hash(self):
        h1 = Database._compute_researcher_content_hash("Professor", "MIT", "Bio")
        h2 = Database._compute_researcher_content_hash("Assistant Professor", "MIT", "Bio")
        assert h1 != h2

    def test_different_affiliation_produces_different_hash(self):
        h1 = Database._compute_researcher_content_hash("Professor", "MIT", "Bio")
        h2 = Database._compute_researcher_content_hash("Professor", "Harvard", "Bio")
        assert h1 != h2

    def test_different_description_produces_different_hash(self):
        h1 = Database._compute_researcher_content_hash("Professor", "MIT", "Bio A")
        h2 = Database._compute_researcher_content_hash("Professor", "MIT", "Bio B")
        assert h1 != h2

    def test_none_values_handled(self):
        """None fields must not raise; they are treated as empty strings."""
        h = Database._compute_researcher_content_hash(None, None, None)
        expected = _sha256(None, None, None)
        assert h == expected

    def test_none_and_empty_string_are_equivalent(self):
        """None and '' must produce the same hash (both coerce to '')."""
        h_none = Database._compute_researcher_content_hash(None, "MIT", None)
        h_empty = Database._compute_researcher_content_hash("", "MIT", "")
        assert h_none == h_empty

    def test_field_order_matters(self):
        """Swapping position and affiliation changes the hash."""
        h1 = Database._compute_researcher_content_hash("Professor", "MIT", "Bio")
        h2 = Database._compute_researcher_content_hash("MIT", "Professor", "Bio")
        assert h1 != h2

    def test_all_empty_produces_consistent_hash(self):
        """All-empty input should return a stable, defined hash (not raise)."""
        h1 = Database._compute_researcher_content_hash("", "", "")
        h2 = Database._compute_researcher_content_hash("", "", "")
        assert h1 == h2

    def test_whitespace_differences_change_hash(self):
        """Leading/trailing whitespace in a field is significant — no normalization here."""
        h1 = Database._compute_researcher_content_hash("Professor", "MIT", "Bio")
        h2 = Database._compute_researcher_content_hash("Professor", "MIT", " Bio")
        assert h1 != h2


# ---------------------------------------------------------------------------
# _compute_paper_content_hash
# ---------------------------------------------------------------------------

class TestPaperContentHash:
    """Database._compute_paper_content_hash() for change detection."""

    def test_returns_64_char_hex_string(self):
        h = Database._compute_paper_content_hash(
            "published", "JLE", "Abstract text", "https://ssrn.com/1", "2024"
        )
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic_same_inputs(self):
        args = ("accepted", "QJE", "Abstract", "https://ssrn.com/2", "2023")
        h1 = Database._compute_paper_content_hash(*args)
        h2 = Database._compute_paper_content_hash(*args)
        assert h1 == h2

    def test_matches_manual_sha256(self):
        """Must equal SHA-256('title||status||venue||abstract||draft_url||year')."""
        status, venue, abstract, draft_url, year = (
            "published", "AER", "The abstract.", "https://ssrn.com/3", "2022"
        )
        expected = _sha256(None, status, venue, abstract, draft_url, year)
        assert (
            Database._compute_paper_content_hash(status, venue, abstract, draft_url, year)
            == expected
        )

    def test_different_status_produces_different_hash(self):
        h1 = Database._compute_paper_content_hash("published", "JLE", "Abs", None, "2024")
        h2 = Database._compute_paper_content_hash("accepted", "JLE", "Abs", None, "2024")
        assert h1 != h2

    def test_different_venue_produces_different_hash(self):
        h1 = Database._compute_paper_content_hash("published", "AER", "Abs", None, "2024")
        h2 = Database._compute_paper_content_hash("published", "QJE", "Abs", None, "2024")
        assert h1 != h2

    def test_different_abstract_produces_different_hash(self):
        h1 = Database._compute_paper_content_hash("published", "AER", "Abstract A", None, "2024")
        h2 = Database._compute_paper_content_hash("published", "AER", "Abstract B", None, "2024")
        assert h1 != h2

    def test_different_draft_url_produces_different_hash(self):
        h1 = Database._compute_paper_content_hash("published", "AER", "Abs", "https://a.com", "2024")
        h2 = Database._compute_paper_content_hash("published", "AER", "Abs", "https://b.com", "2024")
        assert h1 != h2

    def test_different_year_produces_different_hash(self):
        h1 = Database._compute_paper_content_hash("published", "AER", "Abs", None, "2023")
        h2 = Database._compute_paper_content_hash("published", "AER", "Abs", None, "2024")
        assert h1 != h2

    def test_working_paper_status_is_valid_input(self):
        """'working_paper' is a new status added in v2; must not raise."""
        h = Database._compute_paper_content_hash("working_paper", None, None, None, None)
        assert isinstance(h, str) and len(h) == 64

    def test_none_values_handled(self):
        """All-None input must not raise."""
        h = Database._compute_paper_content_hash(None, None, None, None, None)
        expected = _sha256(None, None, None, None, None, None)
        assert h == expected

    def test_none_and_empty_string_are_equivalent(self):
        h_none = Database._compute_paper_content_hash(None, "AER", None, None, None)
        h_empty = Database._compute_paper_content_hash("", "AER", "", "", "")
        assert h_none == h_empty

    def test_field_order_matters(self):
        """Transposing any two fields changes the hash."""
        # swap status and venue
        h1 = Database._compute_paper_content_hash("published", "AER", "Abs", None, "2024")
        h2 = Database._compute_paper_content_hash("AER", "published", "Abs", None, "2024")
        assert h1 != h2

    def test_paper_and_researcher_hashes_independent(self):
        """Identical field strings hash differently depending on the method."""
        common = ("x", "y", "z")
        hr = Database._compute_researcher_content_hash(*common)
        hp = Database._compute_paper_content_hash("x", "y", "z", None, None)
        # The paper hash includes extra None fields so the two must differ
        assert hr != hp
