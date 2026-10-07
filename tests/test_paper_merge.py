"""Tests for post-enrichment duplicate paper merging."""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, call
from backend.enrichment.paper_merge import find_duplicate_groups, find_fuzzy_duplicate_groups, merge_paper_group, _title_similarity


class TestFindDuplicateGroups:
    """find_duplicate_groups returns groups of paper IDs sharing doi or openalex_id."""

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_finds_papers_sharing_doi(self, mock_fetch):
        mock_fetch.side_effect = [
            [{"doi": "10.1234/test", "ids": "1,2"}],
            [],
        ]
        groups = find_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2}

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_finds_papers_sharing_openalex_id(self, mock_fetch):
        mock_fetch.side_effect = [
            [],
            [{"openalex_id": "W123", "ids": "3,4"}],
        ]
        groups = find_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {3, 4}

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_returns_empty_when_no_duplicates(self, mock_fetch):
        mock_fetch.side_effect = [[], []]
        groups = find_duplicate_groups()
        assert groups == []

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_deduplicates_across_doi_and_openalex(self, mock_fetch):
        """If papers 1,2 share a DOI and papers 1,3 share an openalex_id, merge all."""
        mock_fetch.side_effect = [
            [{"doi": "10.1234/test", "ids": "1,2"}],
            [{"openalex_id": "W123", "ids": "1,3"}],
        ]
        groups = find_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2, 3}


class TestMergePaperGroup:
    """Merging preserves current paper statuses and the first announcement."""

    @pytest.fixture
    def connection(self):
        with patch("backend.enrichment.paper_merge.get_connection") as get_conn:
            conn = MagicMock()
            conn.__enter__.return_value = conn
            get_conn.return_value = conn
            yield conn, conn.cursor.return_value

    @staticmethod
    def papers(canonical_status="working_paper", duplicate_status="accepted"):
        # SQL locks by ID, but canonical selection must use discovery time.
        return [
            {"id": 2, "discovered_at": "2026-03-23", "abstract": "An abstract",
             "year": "2024", "venue": "AER", "status": duplicate_status},
            {"id": 5, "discovered_at": "2026-03-20", "abstract": None,
             "year": None, "venue": None, "status": canonical_status},
        ]

    def test_picks_earliest_and_backfills_metadata(self, connection):
        conn, cursor = connection
        cursor.fetchall.side_effect = [self.papers(), []]
        merge_paper_group([2, 5])
        deletes = [c for c in cursor.execute.call_args_list if "DELETE FROM papers" in c.args[0]]
        assert deletes == [call("DELETE FROM papers WHERE id = %s", (2,))]
        backfill = [c for c in cursor.execute.call_args_list if "COALESCE" in c.args[0]]
        assert backfill[0].args[1] == ("An abstract", "2024", "AER", 5)
        assert "FOR UPDATE" in cursor.execute.call_args_list[0].args[0]
        conn.commit.assert_called_once()

    @pytest.mark.parametrize("canonical,duplicate,expected", [
        ("working_paper", "accepted", "accepted"),
        ("working_paper", "working_paper", None),
        ("accepted", "published", "published"),
        ("published", "accepted", None),
        (None, "work_in_progress", "work_in_progress"),
    ])
    def test_preserves_highest_current_paper_status(self, connection, canonical, duplicate, expected):
        _, cursor = connection
        cursor.fetchall.side_effect = [self.papers(canonical, duplicate), []]
        merge_paper_group([2, 5])
        updates = [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE papers SET status")]
        assert updates == ([call("UPDATE papers SET status = %s WHERE id = %s", (expected, 5))] if expected else [])

    def test_does_not_rehydrate_raw_historical_statuses(self, connection):
        _, cursor = connection
        cursor.fetchall.side_effect = [self.papers("working_paper", "working_paper"), []]
        merge_paper_group([2, 5])
        selects = [c.args[0] for c in cursor.execute.call_args_list if c.args[0].startswith("SELECT")]
        assert not any("paper_snapshots" in sql or "old_status" in sql or "new_status" in sql for sql in selects)
        assert not any(c.args[0].startswith("UPDATE papers SET status") for c in cursor.execute.call_args_list)

    def test_keeps_earliest_announcement_even_when_id_is_larger(self, connection):
        _, cursor = connection
        cursor.fetchall.side_effect = [self.papers(), [{"id": 90}, {"id": 10}, {"id": 91}]]
        merge_paper_group([2, 5])
        event_query = next(c.args[0] for c in cursor.execute.call_args_list if "SELECT id FROM feed_events" in c.args[0])
        assert "ORDER BY created_at, id" in event_query
        deletes = [c for c in cursor.execute.call_args_list if "DELETE FROM feed_events" in c.args[0]]
        assert deletes == [call("DELETE FROM feed_events WHERE id = %s", (10,)),
                           call("DELETE FROM feed_events WHERE id = %s", (91,))]

    @pytest.mark.parametrize("timestamps,canonical,deleted", [
        ([datetime(2026, 3, 23), None], 5, 2),
        ([None, datetime(2026, 3, 20)], 2, 5),
        ([None, None], 2, 5),
    ])
    def test_missing_discovery_dates_preserve_mysql_null_first_order(self, connection, timestamps, canonical, deleted):
        conn, cursor = connection
        papers = self.papers()
        for paper, timestamp in zip(papers, timestamps):
            paper['discovered_at'] = timestamp
        cursor.fetchall.side_effect = [papers, []]
        merge_paper_group([2, 5])
        deletes = [c for c in cursor.execute.call_args_list if "DELETE FROM papers" in c.args[0]]
        assert deletes == [call("DELETE FROM papers WHERE id = %s", (deleted,))]
        event_query = next(c for c in cursor.execute.call_args_list if "SELECT id FROM feed_events" in c.args[0])
        assert event_query.args[1] == (canonical,)
        conn.commit.assert_called_once()

    def test_rolls_back_if_history_write_fails(self, connection):
        conn, cursor = connection
        cursor.fetchall.side_effect = [self.papers(), []]
        def execute(sql, args):
            if sql.startswith("UPDATE papers SET status"):
                raise RuntimeError("write failed")
        cursor.execute.side_effect = execute
        with pytest.raises(RuntimeError, match="write failed"):
            merge_paper_group([2, 5])
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


class TestTitleSimilarity:
    """Title similarity using max(normalized word sequence, content overlap)."""

    def test_similar_titles_high_score(self):
        score = _title_similarity(
            "Levels and Drivers of Lifetime Earnings",
            "Distributions and Drivers of Lifetime Earnings",
        )
        assert score >= 0.7

    def test_different_titles_low_score(self):
        score = _title_similarity(
            "Levels and Drivers of Lifetime Earnings",
            "Trade and Wages in Developing Countries",
        )
        assert score < 0.4

    def test_empty_title_returns_zero(self):
        assert _title_similarity("", "Something") == 0.0
        assert _title_similarity("Something", "") == 0.0

    def test_hyphenated_vs_unhyphenated(self):
        score = _title_similarity(
            "The impact of childhood inter-ethnic contact on managers' hiring decisions",
            "The Impact of Interethnic Contact in Schools on Managers' Hiring Decisions",
        )
        assert score >= 0.85

    def test_subtitle_addition(self):
        score = _title_similarity(
            "Do Natural Disasters Stimulate Individual Saving?",
            "Do Natural Disasters Stimulate Individual Saving? Evidence from a Natural Experiment",
        )
        assert score >= 0.85

    def test_rewording_with_same_core(self):
        score = _title_similarity(
            "Housing Shortage in Germany - An Analysis of Socioeconomic Causes and Effects",
            "Housing Shortage in Germany: Socioeconomic Causes and Effects",
        )
        assert score >= 0.85

    def test_similar_topic_different_paper(self):
        score = _title_similarity(
            "The Impact of Immigration on Wages",
            "The Impact of Immigration on Employment",
        )
        assert score < 0.85

    def test_overlapping_words_different_paper(self):
        score = _title_similarity(
            "The long run impact of childhood interracial contact on residential segregation",
            "The impact of childhood inter-ethnic contact on managers' hiring decisions",
        )
        assert score < 0.85


class TestFindFuzzyDuplicateGroups:
    """Fuzzy matching: same authors + similar titles."""

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_finds_fuzzy_duplicates_with_same_authors(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": 1, "title": "Ideas Have Consequences: The Impact of Law and Economics on American Justice", "author_ids": "2,6,141", "doi": None, "openalex_id": None},
            {"id": 2, "title": "Ideas Have Consequences: The Effect of Law and Economics on American Justice", "author_ids": "2,6,141", "doi": None, "openalex_id": None},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2}

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_no_match_when_titles_too_different(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": 1, "title": "Levels and Drivers of Lifetime Earnings", "author_ids": "2,6,141", "doi": None, "openalex_id": None},
            {"id": 2, "title": "Trade Policy in Open Economies", "author_ids": "2,6,141", "doi": None, "openalex_id": None},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert groups == []

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_no_match_when_different_authors(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": 1, "title": "Levels and Drivers of Lifetime Earnings", "author_ids": "2,6,141", "doi": None, "openalex_id": None},
            {"id": 2, "title": "Levels and Drivers of Lifetime Earnings", "author_ids": "3,7,200", "doi": None, "openalex_id": None},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert groups == []

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_skips_pairs_sharing_doi(self, mock_fetch):
        """Papers with the same DOI are handled by find_duplicate_groups, not fuzzy."""
        mock_fetch.return_value = [
            {"id": 1, "title": "Same Title Here", "author_ids": "2,6", "doi": "10.1234/test", "openalex_id": None},
            {"id": 2, "title": "Same Title Here", "author_ids": "2,6", "doi": "10.1234/test", "openalex_id": None},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert groups == []

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_matches_when_only_one_has_identifier(self, mock_fetch):
        """One paper enriched, the other not — fuzzy should still catch it."""
        mock_fetch.return_value = [
            {"id": 1, "title": "Same Title Here", "author_ids": "2,6", "doi": "10.1234/test", "openalex_id": "W123"},
            {"id": 2, "title": "Same Title Here", "author_ids": "2,6", "doi": None, "openalex_id": None},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2}

    @patch("backend.enrichment.paper_merge.fetch_all")
    def test_skips_single_author_papers(self, mock_fetch):
        """Only considers papers with 2+ authors to avoid false positives."""
        mock_fetch.return_value = []  # SQL HAVING COUNT >= 2 filters these out
        groups = find_fuzzy_duplicate_groups()
        assert groups == []
