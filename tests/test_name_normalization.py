"""Tests for researcher name capitalization normalization."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

import pytest
from backend.database.researchers import normalize_name_case


class TestNormalizeNameCase:
    """Pure-function tests — no DB needed."""

    @pytest.mark.parametrize("input_name,expected", [
        ("JOHN", "John"),
        ("john", "John"),
        ("John", "John"),
    ])
    def test_basic_capitalization(self, input_name, expected):
        assert normalize_name_case(input_name) == expected

    @pytest.mark.parametrize("input_name,expected", [
        ("MCDONALD", "McDonald"),
        ("mcdonald", "McDonald"),
        ("MACDONALD", "MacDonald"),
        ("macdonald", "MacDonald"),
    ])
    def test_mc_mac_prefixes(self, input_name, expected):
        assert normalize_name_case(input_name) == expected

    @pytest.mark.parametrize("input_name,expected", [
        ("O'BRIEN", "O'Brien"),
        ("o'brien", "O'Brien"),
    ])
    def test_apostrophe_prefixes(self, input_name, expected):
        assert normalize_name_case(input_name) == expected

    @pytest.mark.parametrize("input_name,expected", [
        ("VAN DER BERG", "Van der Berg"),
        ("van der berg", "Van der Berg"),
        ("DE LA CRUZ", "De la Cruz"),
        ("de la cruz", "De la Cruz"),
        ("VON NEUMANN", "Von Neumann"),
    ])
    def test_particles_lowercase_after_first(self, input_name, expected):
        assert normalize_name_case(input_name) == expected

    @pytest.mark.parametrize("input_name,expected", [
        ("JEAN-PIERRE", "Jean-Pierre"),
        ("jean-pierre", "Jean-Pierre"),
    ])
    def test_hyphenated_names(self, input_name, expected):
        assert normalize_name_case(input_name) == expected

    @pytest.mark.parametrize("input_name", [
        "LeBlanc",
        "DeGroot",
        "DiNardo",
    ])
    def test_mixed_case_left_unchanged(self, input_name):
        assert normalize_name_case(input_name) == input_name

    def test_empty_and_whitespace(self):
        assert normalize_name_case("") == ""
        assert normalize_name_case("  ") == "  "

    def test_single_initial(self):
        assert normalize_name_case("j.") == "J."
        assert normalize_name_case("J.") == "J."

    @pytest.mark.parametrize("input_name,expected", [
        ("A.B.", "A.B."),
        ("a.b.", "A.B."),
        ("J.L.", "J.L."),
        ("j.l.", "J.L."),
        ("A.S.S.", "A.S.S."),
    ])
    def test_multi_initials_uppercased(self, input_name, expected):
        assert normalize_name_case(input_name) == expected

    def test_cjk_names_untouched(self):
        assert normalize_name_case("俊能") == "俊能"
        assert normalize_name_case("博宇") == "博宇"

    def test_accented_all_caps(self):
        assert normalize_name_case("JOSÉ") == "José"
        assert normalize_name_case("MÜLLER") == "Müller"
        assert normalize_name_case("FRÉDÉRIC") == "Frédéric"


class TestNormalizationInGetResearcherId:
    """Verify normalization is applied during researcher insertion."""

    def test_all_caps_normalized_on_insert(self):
        from unittest.mock import patch
        with (
            patch("backend.database.researchers.fetch_one", return_value=None),
            patch("backend.database.researchers.fetch_all", return_value=[]),
            patch("backend.database.researchers.execute_query", return_value=99) as mock_exec,
        ):
            from backend.database.researchers import get_researcher_id
            result = get_researcher_id(first_name="JOHN", last_name="SMITH")

        assert result == 99
        params = mock_exec.call_args[0][1]
        assert params[0] == "John"
        assert params[1] == "Smith"

    def test_all_lowercase_normalized_on_insert(self):
        from unittest.mock import patch
        with (
            patch("backend.database.researchers.fetch_one", return_value=None),
            patch("backend.database.researchers.fetch_all", return_value=[]),
            patch("backend.database.researchers.execute_query", return_value=99) as mock_exec,
        ):
            from backend.database.researchers import get_researcher_id
            result = get_researcher_id(first_name="jean-pierre", last_name="van der berg")

        assert result == 99
        params = mock_exec.call_args[0][1]
        assert params[0] == "Jean-Pierre"
        assert params[1] == "Van der Berg"
