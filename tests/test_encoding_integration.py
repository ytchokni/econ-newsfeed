"""Integration tests: encoding guard is called during database writes."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")

from unittest.mock import patch, MagicMock

import pytest


class TestPublicationEncodingGuard:
    """Verify save_publications passes text through encoding guard."""

    @patch("publication.Database")
    def test_mojibake_title_is_fixed_before_insert(self, mock_db):
        """A paper with mojibake in title should be cleaned before DB insert."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        # fetchone() returns a tuple so row[0] works (used by _url_has_baseline etc.)
        mock_cursor.fetchone.return_value = (0,)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn

        from publication import Publication

        publications = [{
            "title": "Ergebnisse fÃ¼r die Wirtschaft",
            "year": "2024",
            "venue": "Ã–konometrie Journal",
            "abstract": "Eine Analyse der GÃ¼terpreise",
            "status": "published",
            "draft_url": None,
            "authors": [],
        }]

        Publication.save_publications("http://example.com", publications)

        # Find the INSERT INTO papers call
        insert_calls = [
            call for call in mock_cursor.execute.call_args_list
            if call[0][0].strip().startswith("INSERT IGNORE INTO papers")
        ]
        assert len(insert_calls) >= 1

        params = insert_calls[0][0][1]
        # params order: (url, title, title_hash, year, venue, abstract, ...)
        title_param = params[1]
        venue_param = params[4]
        abstract_param = params[5]

        assert title_param == "Ergebnisse für die Wirtschaft"
        assert venue_param == "Ökonometrie Journal"
        assert abstract_param == "Eine Analyse der Güterpreise"
