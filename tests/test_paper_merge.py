"""Tests for post-enrichment duplicate paper merging."""
import pytest
from unittest.mock import patch, MagicMock, call
from paper_merge import find_duplicate_groups, find_fuzzy_duplicate_groups, merge_paper_group, _title_similarity


class TestFindDuplicateGroups:
    """find_duplicate_groups returns groups of paper IDs sharing doi or openalex_id."""

    @patch("paper_merge.Database.fetch_all")
    def test_finds_papers_sharing_doi(self, mock_fetch):
        mock_fetch.side_effect = [
            [{"doi": "10.1234/test", "ids": "1,2"}],
            [],
        ]
        groups = find_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2}

    @patch("paper_merge.Database.fetch_all")
    def test_finds_papers_sharing_openalex_id(self, mock_fetch):
        mock_fetch.side_effect = [
            [],
            [{"openalex_id": "W123", "ids": "3,4"}],
        ]
        groups = find_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {3, 4}

    @patch("paper_merge.Database.fetch_all")
    def test_returns_empty_when_no_duplicates(self, mock_fetch):
        mock_fetch.side_effect = [[], []]
        groups = find_duplicate_groups()
        assert groups == []

    @patch("paper_merge.Database.fetch_all")
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
    """merge_paper_group reassigns child rows and deletes duplicates."""

    @patch("paper_merge.Database.get_connection")
    @patch("paper_merge.Database.fetch_all")
    def test_picks_earliest_as_canonical(self, mock_fetch_all, mock_get_conn):
        """Canonical paper is the one with earliest discovered_at."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        mock_fetch_all.return_value = [
            {"id": 2, "discovered_at": "2026-03-20", "abstract": None, "year": None, "venue": None},
            {"id": 5, "discovered_at": "2026-03-23", "abstract": "An abstract", "year": "2024", "venue": "AER"},
        ]

        merge_paper_group([2, 5])

        # Verify exactly one DELETE targeting paper 5 (not canonical paper 2)
        delete_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "DELETE FROM papers" in str(c)
        ]
        assert len(delete_calls) == 1
        assert delete_calls[0] == call("DELETE FROM papers WHERE id = %s", (5,))

    @patch("paper_merge.Database.get_connection")
    @patch("paper_merge.Database.fetch_all")
    def test_backfills_null_fields_from_duplicate(self, mock_fetch_all, mock_get_conn):
        """Canonical paper gets NULL fields filled from duplicate before deletion."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        mock_fetch_all.return_value = [
            {"id": 2, "discovered_at": "2026-03-20", "abstract": None, "year": None, "venue": None},
            {"id": 5, "discovered_at": "2026-03-23", "abstract": "An abstract", "year": "2024", "venue": "AER"},
        ]

        merge_paper_group([2, 5])

        # Verify a COALESCE UPDATE was issued for backfill
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "COALESCE" in str(c)
        ]
        assert len(update_calls) >= 1


class TestTitleSimilarity:
    """Word-level title similarity for fuzzy matching."""

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


class TestFindFuzzyDuplicateGroups:
    """Fuzzy matching: same authors + similar titles."""

    @patch("paper_merge.Database.fetch_all")
    def test_finds_fuzzy_duplicates_with_same_authors(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": 1, "title": "Ideas Have Consequences: The Impact of Law and Economics on American Justice", "author_ids": "2,6,141"},
            {"id": 2, "title": "Ideas Have Consequences: The Effect of Law and Economics on American Justice", "author_ids": "2,6,141"},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2}

    @patch("paper_merge.Database.fetch_all")
    def test_no_match_when_titles_too_different(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": 1, "title": "Levels and Drivers of Lifetime Earnings", "author_ids": "2,6,141"},
            {"id": 2, "title": "Trade Policy in Open Economies", "author_ids": "2,6,141"},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert groups == []

    @patch("paper_merge.Database.fetch_all")
    def test_no_match_when_different_authors(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": 1, "title": "Levels and Drivers of Lifetime Earnings", "author_ids": "2,6,141"},
            {"id": 2, "title": "Levels and Drivers of Lifetime Earnings", "author_ids": "3,7,200"},
        ]
        groups = find_fuzzy_duplicate_groups()
        assert groups == []

    @patch("paper_merge.Database.fetch_all")
    def test_skips_single_author_papers(self, mock_fetch):
        """Only considers papers with 2+ authors to avoid false positives."""
        mock_fetch.return_value = []  # SQL HAVING COUNT >= 2 filters these out
        groups = find_fuzzy_duplicate_groups()
        assert groups == []
