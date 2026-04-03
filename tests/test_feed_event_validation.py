"""Tests for new_paper feed event validation — title-in-previous-snapshot check."""
import unittest
import zlib
from unittest.mock import MagicMock, patch

from publication import _title_in_previous_snapshot


class TestTitleInPreviousSnapshot(unittest.TestCase):
    """_title_in_previous_snapshot returns True if title appears in the prior HTML."""

    @patch("publication.Database")
    def test_title_found_in_previous_snapshot(self, mock_db):
        """Title present in previous HTML → returns True (suppress event)."""
        html = "<h2>Insult Politics in the Age of Social Media</h2>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Insult Politics in the Age of Social Media", "https://example.com/")
        assert result is True

    @patch("publication.Database")
    def test_title_not_found_in_previous_snapshot(self, mock_db):
        """Title absent from previous HTML → returns False (allow event)."""
        html = "<h2>Some Other Paper Title</h2>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Brand New Paper Title", "https://example.com/")
        assert result is False

    @patch("publication.Database")
    def test_case_insensitive_match(self, mock_db):
        """Match should be case-insensitive."""
        html = "<h2>INSULT POLITICS IN THE AGE OF SOCIAL MEDIA</h2>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Insult Politics in the Age of Social Media", "https://example.com/")
        assert result is True

    @patch("publication.Database")
    def test_no_previous_snapshot(self, mock_db):
        """No previous snapshot exists → returns False (allow event)."""
        mock_db.fetch_one.return_value = None

        result = _title_in_previous_snapshot("Any Title", "https://example.com/")
        assert result is False

    @patch("publication.Database")
    def test_partial_title_match_long_title(self, mock_db):
        """For long titles, first 40 chars should match (handles suffix changes)."""
        html = "<p>Monetary Policy Shocks and Their Macroeconomic Consequences — some extra text</p>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot(
            "Monetary Policy Shocks and Their Macroeconomic Consequences — Job Market Paper",
            "https://example.com/",
        )
        assert result is True

    @patch("publication.Database")
    def test_short_title_uses_full_match(self, mock_db):
        """Titles ≤40 chars use full title for matching."""
        html = "<p>Short Paper</p>"
        compressed = zlib.compress(html.encode("utf-8"))
        mock_db.fetch_one.return_value = {"raw_html_compressed": compressed}

        result = _title_in_previous_snapshot("Short Paper", "https://example.com/")
        assert result is True

    @patch("publication.Database")
    def test_corrupt_compressed_data(self, mock_db):
        """Corrupt zlib data → returns False (allow event, don't crash)."""
        mock_db.fetch_one.return_value = {"raw_html_compressed": b"not-valid-zlib"}

        result = _title_in_previous_snapshot("Any Title", "https://example.com/")
        assert result is False


if __name__ == "__main__":
    unittest.main()
