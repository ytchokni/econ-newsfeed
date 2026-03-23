# tests/test_jel_enrichment.py
"""Tests for JEL enrichment from paper topics."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import MagicMock, patch, call


class TestSavePaperTopics:
    """Tests for database.jel.save_paper_topics."""

    def test_inserts_topics_for_paper(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        topics = [
            {
                "openalex_topic_id": "T10001",
                "topic_name": "Labor market dynamics",
                "subfield_name": "Economics and Econometrics",
                "field_name": "Economics, Econometrics and Finance",
                "domain_name": "Social Sciences",
                "score": 0.99,
            },
            {
                "openalex_topic_id": "T10002",
                "topic_name": "Migration and policy",
                "subfield_name": "Sociology and Political Science",
                "field_name": "Social Sciences",
                "domain_name": "Social Sciences",
                "score": 0.85,
            },
        ]

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import save_paper_topics
            save_paper_topics(paper_id=42, topics=topics)

        # Should delete existing + insert each topic
        assert mock_cursor.execute.call_count == 3  # 1 delete + 2 inserts
        delete_sql = mock_cursor.execute.call_args_list[0][0][0]
        assert "DELETE FROM paper_topics" in delete_sql
        mock_conn.commit.assert_called_once()


class TestGetPaperTopicsForResearcher:
    """Tests for database.jel.get_paper_topics_for_researcher."""

    def test_returns_topics_for_researcher_papers(self):
        mock_rows = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "Migration and policy", "score": 0.85},
        ]
        with patch("database.jel.fetch_all", return_value=mock_rows) as mock_fetch:
            from database.jel import get_paper_topics_for_researcher
            result = get_paper_topics_for_researcher(researcher_id=1)

        assert len(result) == 2
        sql = mock_fetch.call_args[0][0]
        assert "paper_topics" in sql
        assert "authorship" in sql


class TestGetPapersNeedingTopics:
    """Tests for database.jel.get_papers_needing_topics."""

    def test_returns_papers_with_openalex_id_but_no_topics(self):
        mock_rows = [
            {"id": 1, "openalex_id": "W123"},
            {"id": 2, "openalex_id": "W456"},
        ]
        with patch("database.jel.fetch_all", return_value=mock_rows) as mock_fetch:
            from database.jel import get_papers_needing_topics
            result = get_papers_needing_topics()

        assert len(result) == 2
        sql = mock_fetch.call_args[0][0]
        assert "openalex_id IS NOT NULL" in sql
        assert "LEFT JOIN paper_topics" in sql


class TestAddResearcherJelCodes:
    """Tests for database.jel.add_researcher_jel_codes (non-destructive)."""

    def test_inserts_without_deleting_existing(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import add_researcher_jel_codes
            add_researcher_jel_codes(researcher_id=1, jel_codes=["J", "F"])

        # Should NOT contain DELETE — non-destructive
        all_sql = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert not any("DELETE" in sql for sql in all_sql)
        # Should use INSERT INTO (relies on IntegrityError catch for duplicates)
        assert any("INSERT INTO researcher_jel_codes" in sql for sql in all_sql)
        assert mock_cursor.execute.call_count == 2  # One per code
        mock_conn.commit.assert_called_once()
