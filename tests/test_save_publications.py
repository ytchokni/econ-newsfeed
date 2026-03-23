# tests/test_save_publications.py
"""Tests for Publication.save_publications edge cases."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

import pytest
from unittest.mock import patch, MagicMock
from publication import Publication


def _mock_conn():
    """Create a mock DB connection that simulates INSERT IGNORE with new row."""
    mock_cursor = MagicMock()
    mock_cursor.lastrowid = 1  # Simulate new paper inserted
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestAuthorNormalization:
    """Author lists with != 2 elements should not crash save_publications."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_three_element_author_joins_first_names(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['Jose', 'Luis', 'Garcia'] -> first_name='Jose Luis', last_name='Garcia'."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["Jose", "Luis", "Garcia"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("Jose Luis", "Garcia", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_single_element_author_uses_empty_first_name(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['Garcia'] -> first_name='', last_name='Garcia'."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["Garcia"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("", "Garcia", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_empty_author_list_is_skipped(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """[] -> skip, don't crash."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [[]],
            "year": "2024",
        }])

        mock_get_researcher.assert_not_called()

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_normal_two_element_author_unchanged(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['John', 'Doe'] -> first_name='John', last_name='Doe' (normal case)."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("John", "Doe", conn=conn)
