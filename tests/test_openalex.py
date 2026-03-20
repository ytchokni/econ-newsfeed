"""Tests for OpenAlex API client and enrichment logic."""
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import patch, MagicMock


class TestUpdateOpenalexData:
    """Tests for database.papers.update_openalex_data."""

    def test_stores_doi_and_openalex_id(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("database.papers.get_connection", return_value=mock_conn):
            from database.papers import update_openalex_data
            update_openalex_data(
                paper_id=1,
                doi="10.1257/aer.20181234",
                openalex_id="W2741809807",
                coauthors=[
                    {"display_name": "A. Smith", "openalex_author_id": "A111"},
                    {"display_name": "B. Jones", "openalex_author_id": "A222"},
                ],
            )

        # UPDATE papers SET doi, openalex_id
        update_call = mock_cursor.execute.call_args_list[0]
        assert "UPDATE papers SET doi" in update_call[0][0]
        assert update_call[0][1] == ("10.1257/aer.20181234", "W2741809807", 1)

        # DELETE old coauthors + INSERT coauthors (2 calls)
        assert mock_cursor.execute.call_count == 4  # 1 update + 1 delete + 2 inserts
        mock_conn.commit.assert_called_once()

    def test_stores_abstract_when_provided(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("database.papers.get_connection", return_value=mock_conn):
            from database.papers import update_openalex_data
            update_openalex_data(
                paper_id=1,
                doi="10.1234/test",
                openalex_id="W123",
                coauthors=[],
                abstract="This paper studies...",
            )

        update_call = mock_cursor.execute.call_args_list[0]
        assert "abstract" in update_call[0][0]
        assert "This paper studies..." in update_call[0][1]


class TestGetUnenrichedPapers:
    """Tests for database.papers.get_unenriched_papers."""

    def test_returns_papers_without_openalex_id(self):
        mock_rows = [
            {"id": 1, "title": "Trade and Wages", "abstract": None,
             "author_name": "Max Steinhardt"},
            {"id": 2, "title": "Immigration Effects", "abstract": "Existing abstract",
             "author_name": "Jane Doe"},
        ]
        with patch("database.papers.fetch_all", return_value=mock_rows) as mock_fetch:
            from database.papers import get_unenriched_papers
            result = get_unenriched_papers(limit=50)

        assert len(result) == 2
        assert result[0]["id"] == 1
        # Verify SQL filters on openalex_id IS NULL
        sql = mock_fetch.call_args[0][0]
        assert "openalex_id IS NULL" in sql


SAMPLE_OPENALEX_RESPONSE = {
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.1257/aer.20181234",
            "title": "Trade and Wages",
            "authorships": [
                {
                    "author": {
                        "id": "https://openalex.org/A5023888391",
                        "display_name": "Max Friedrich Steinhardt",
                    },
                    "author_position": "first",
                },
                {
                    "author": {
                        "id": "https://openalex.org/A5000000001",
                        "display_name": "Jane Doe",
                    },
                    "author_position": "last",
                },
            ],
            "abstract_inverted_index": {
                "This": [0],
                "paper": [1],
                "studies": [2],
                "trade.": [3],
            },
        }
    ]
}


class TestSearchWork:
    """Tests for openalex.search_work."""

    def test_returns_parsed_result_on_match(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_OPENALEX_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import search_work
            result = search_work("Trade and Wages", "Steinhardt")

        assert result is not None
        assert result["doi"] == "10.1257/aer.20181234"
        assert result["openalex_id"] == "W2741809807"
        assert len(result["coauthors"]) == 2
        assert result["coauthors"][0]["display_name"] == "Max Friedrich Steinhardt"
        assert result["coauthors"][1]["display_name"] == "Jane Doe"
        assert result["abstract"] == "This paper studies trade."

    def test_returns_none_when_no_results(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import search_work
            result = search_work("Nonexistent Paper", "Nobody")

        assert result is None

    def test_returns_none_when_author_not_found(self):
        """If the search returns results but none match the author, return None."""
        response = {
            "results": [
                {
                    "id": "https://openalex.org/W999",
                    "doi": None,
                    "title": "Similar Title",
                    "authorships": [
                        {"author": {"id": "https://openalex.org/A999", "display_name": "Completely Different Person"}},
                    ],
                    "abstract_inverted_index": None,
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_resp.raise_for_status = MagicMock()

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import search_work
            result = search_work("Similar Title", "Steinhardt")

        assert result is None

    def test_returns_none_on_request_error(self):
        import requests as req
        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.side_effect = req.RequestException("timeout")
            from openalex import search_work
            result = search_work("Any Paper", "Anyone")

        assert result is None


class TestReconstructAbstract:
    """Tests for openalex.reconstruct_abstract."""

    def test_reconstructs_from_inverted_index(self):
        from openalex import reconstruct_abstract
        inverted_index = {
            "This": [0],
            "paper": [1],
            "studies": [2],
            "the": [3, 6],
            "effect": [4],
            "of": [5],
            "trade.": [7],
        }
        result = reconstruct_abstract(inverted_index)
        assert result == "This paper studies the effect of the trade."

    def test_empty_index_returns_empty_string(self):
        from openalex import reconstruct_abstract
        assert reconstruct_abstract({}) == ""
