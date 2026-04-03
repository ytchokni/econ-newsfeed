"""Tests for new_paper feed event validation — title-in-previous-snapshot check."""
import unittest
import zlib
from unittest.mock import MagicMock, patch

from publication import _title_in_previous_snapshot, _get_previous_snapshot_html


class TestTitleInPreviousSnapshot(unittest.TestCase):
    """_title_in_previous_snapshot returns True if title appears in the prior HTML."""

    def test_title_found_in_previous_snapshot(self):
        """Title present in previous HTML → returns True (suppress event)."""
        html_lower = "<h2>insult politics in the age of social media</h2>"
        result = _title_in_previous_snapshot("Insult Politics in the Age of Social Media", html_lower)
        assert result is True

    def test_title_not_found_in_previous_snapshot(self):
        """Title absent from previous HTML → returns False (allow event)."""
        html_lower = "<h2>some other paper title</h2>"
        result = _title_in_previous_snapshot("Brand New Paper Title", html_lower)
        assert result is False

    def test_case_insensitive_match(self):
        """Match should be case-insensitive (html is pre-lowered)."""
        html_lower = "<h2>insult politics in the age of social media</h2>"
        result = _title_in_previous_snapshot("Insult Politics in the Age of Social Media", html_lower)
        assert result is True

    def test_no_previous_snapshot(self):
        """No previous snapshot (None) → returns False (allow event)."""
        result = _title_in_previous_snapshot("Any Title", None)
        assert result is False

    def test_partial_title_match_long_title(self):
        """For long titles, first 40 chars should match (handles suffix changes)."""
        html_lower = "<p>monetary policy shocks and their macroeconomic consequences — some extra text</p>"
        result = _title_in_previous_snapshot(
            "Monetary Policy Shocks and Their Macroeconomic Consequences — Job Market Paper",
            html_lower,
        )
        assert result is True

    def test_short_title_uses_full_match(self):
        """Titles ≤40 chars use full title for matching."""
        html_lower = "<p>short paper</p>"
        result = _title_in_previous_snapshot("Short Paper", html_lower)
        assert result is True


class TestGetPreviousSnapshotHtml(unittest.TestCase):
    """_get_previous_snapshot_html fetches and decompresses the previous snapshot."""

    def _make_cursor(self, row):
        """Create a mock cursor that returns the given row."""
        cursor = MagicMock()
        cursor.fetchone.return_value = row
        return cursor

    def test_returns_lowered_html(self):
        """Returns decompressed, lowercased HTML text."""
        html = "<h2>Some Title</h2>"
        compressed = zlib.compress(html.encode("utf-8"))
        cursor = self._make_cursor((compressed,))

        result = _get_previous_snapshot_html(cursor, "https://example.com/")
        assert result == "<h2>some title</h2>"

    def test_returns_none_when_no_snapshot(self):
        """No previous snapshot → returns None."""
        cursor = self._make_cursor(None)
        result = _get_previous_snapshot_html(cursor, "https://example.com/")
        assert result is None

    def test_returns_none_on_corrupt_data(self):
        """Corrupt zlib data → returns None (don't crash)."""
        cursor = self._make_cursor((b"not-valid-zlib",))
        result = _get_previous_snapshot_html(cursor, "https://example.com/")
        assert result is None

    def test_returns_none_when_blob_is_none(self):
        """Row exists but blob is None → returns None."""
        cursor = self._make_cursor((None,))
        result = _get_previous_snapshot_html(cursor, "https://example.com/")
        assert result is None


if __name__ == "__main__":
    unittest.main()
