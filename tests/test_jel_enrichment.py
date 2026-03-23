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


from topic_jel_map import map_topic_to_jel


class TestAggregateJelForResearcher:
    """Tests for jel_enrichment.aggregate_jel_for_researcher."""

    def test_aggregates_jel_from_paper_topics(self):
        mock_topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "Labor market dynamics", "score": 0.95},
            {"topic_name": "Migration and policy", "score": 0.85},
            {"topic_name": "International trade flows", "score": 0.80},
        ]
        with patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=mock_topics):
            from jel_enrichment import aggregate_jel_for_researcher
            codes = aggregate_jel_for_researcher(researcher_id=1)

        assert "J" in codes
        assert "F" in codes
        assert len(codes) <= 5

    def test_returns_empty_for_no_topics(self):
        with patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=[]):
            from jel_enrichment import aggregate_jel_for_researcher
            codes = aggregate_jel_for_researcher(researcher_id=1)

        assert codes == []

    def test_limits_to_top_5(self):
        mock_topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "International trade flows", "score": 0.95},
            {"topic_name": "Monetary Policy impact", "score": 0.90},
            {"topic_name": "Financial market risk", "score": 0.85},
            {"topic_name": "Public finance and tax", "score": 0.80},
            {"topic_name": "Environmental regulation", "score": 0.75},
            {"topic_name": "Urban housing markets", "score": 0.70},
        ]
        with patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=mock_topics):
            from jel_enrichment import aggregate_jel_for_researcher
            codes = aggregate_jel_for_researcher(researcher_id=1)

        assert len(codes) <= 5


class TestEnrichJelFromPapers:
    """Tests for jel_enrichment.enrich_jel_from_papers."""

    def test_full_pipeline(self):
        papers_needing = [
            {"id": 1, "openalex_id": "W123"},
            {"id": 2, "openalex_id": "W456"},
        ]
        topics_by_id = {
            "W123": [
                {"openalex_topic_id": "T1", "topic_name": "Labor market dynamics",
                 "subfield_name": "Econ", "field_name": "Econ", "domain_name": "SS", "score": 0.99},
            ],
            "W456": [
                {"openalex_topic_id": "T2", "topic_name": "International trade",
                 "subfield_name": "Econ", "field_name": "Econ", "domain_name": "SS", "score": 0.90},
            ],
        }
        researchers = [
            {"id": 10, "first_name": "Jane", "last_name": "Doe"},
        ]
        researcher_topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "International trade", "score": 0.90},
        ]

        with (
            patch("jel_enrichment.Database.get_papers_needing_topics", return_value=papers_needing),
            patch("jel_enrichment.fetch_topics_batch", return_value=topics_by_id),
            patch("jel_enrichment.Database.save_paper_topics") as mock_save_topics,
            patch("jel_enrichment.Database.fetch_all", return_value=researchers),
            patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=researcher_topics),
            patch("jel_enrichment.Database.add_researcher_jel_codes") as mock_add_jel,
        ):
            from jel_enrichment import enrich_jel_from_papers
            count = enrich_jel_from_papers()

        assert count == 1
        assert mock_save_topics.call_count == 2
        mock_add_jel.assert_called_once()
        jel_codes_arg = mock_add_jel.call_args[0][1]
        assert "J" in jel_codes_arg
        assert "F" in jel_codes_arg

    def test_returns_zero_when_no_papers(self):
        with (
            patch("jel_enrichment.Database.get_papers_needing_topics", return_value=[]),
            patch("jel_enrichment.Database.fetch_all", return_value=[]),
        ):
            from jel_enrichment import enrich_jel_from_papers
            count = enrich_jel_from_papers()

        assert count == 0


class TestCliIntegration:
    """Verify the CLI command is registered."""

    def test_enrich_jel_command_registered(self):
        """The 'enrich-jel' subcommand should be in main.py source."""
        import inspect
        import main as main_mod
        source = inspect.getsource(main_mod)
        assert "enrich-jel" in source
