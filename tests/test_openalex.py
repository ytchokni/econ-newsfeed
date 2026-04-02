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

        # UPDATE papers SET doi, openalex_id, abstract, year via COALESCE
        update_call = mock_cursor.execute.call_args_list[0]
        assert "UPDATE papers SET doi" in update_call[0][0]
        assert "COALESCE" in update_call[0][0]
        assert update_call[0][1] == ("10.1257/aer.20181234", "W2741809807", None, None, 1)

        # DELETE old coauthors + executemany for inserts
        assert mock_cursor.execute.call_count == 2  # 1 update + 1 delete
        mock_cursor.executemany.assert_called_once()
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
        assert "COALESCE" in update_call[0][0]
        assert update_call[0][1] == ("10.1234/test", "W123", "This paper studies...", None, 1)


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


class TestEnrichPublication:
    """Tests for openalex.enrich_publication."""

    def test_enriches_new_paper(self):
        """Paper without openalex_id gets enriched with DOI and coauthors."""
        openalex_result = {
            "doi": "10.1257/aer.20181234",
            "openalex_id": "W2741809807",
            "coauthors": [
                {"display_name": "Max Steinhardt", "openalex_author_id": "A111"},
            ],
            "abstract": "OpenAlex abstract.",
        }
        with (
            patch("openalex.search_work", return_value=openalex_result),
            patch("openalex.Database.update_openalex_data") as mock_update,
            patch("openalex._backfill_researcher_openalex_ids"),
        ):
            from openalex import enrich_publication
            result = enrich_publication(
                paper_id=1,
                title="Trade and Wages",
                author_name="Max Steinhardt",
                existing_abstract=None,
            )

        assert result is True
        mock_update.assert_called_once_with(
            paper_id=1,
            doi="10.1257/aer.20181234",
            openalex_id="W2741809807",
            coauthors=[{"display_name": "Max Steinhardt", "openalex_author_id": "A111"}],
            abstract="OpenAlex abstract.",
            year=None,
        )

    def test_skips_abstract_when_existing(self):
        """If paper already has an abstract from scraping, don't overwrite."""
        openalex_result = {
            "doi": "10.1234/test",
            "openalex_id": "W999",
            "coauthors": [],
            "abstract": "OpenAlex abstract.",
        }
        with (
            patch("openalex.search_work", return_value=openalex_result),
            patch("openalex.Database.update_openalex_data") as mock_update,
            patch("openalex._backfill_researcher_openalex_ids"),
        ):
            from openalex import enrich_publication
            enrich_publication(
                paper_id=1,
                title="Test Paper",
                author_name="Author",
                existing_abstract="Already have an abstract.",
            )

        # abstract should NOT be passed when paper already has one
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs.get("abstract") is None

    def test_returns_false_on_no_match(self):
        """Graceful handling when OpenAlex has no match."""
        with patch("openalex.search_work", return_value=None):
            from openalex import enrich_publication
            result = enrich_publication(
                paper_id=1,
                title="Obscure Paper",
                author_name="Unknown",
                existing_abstract=None,
            )

        assert result is False


class TestEnrichNewPublications:
    """Tests for openalex.enrich_new_publications."""

    def test_enriches_unenriched_papers(self):
        unenriched = [
            {"id": 1, "title": "Paper A", "abstract": None, "author_name": "Author A", "link_doi": None},
            {"id": 2, "title": "Paper B", "abstract": "Existing", "author_name": "Author B", "link_doi": None},
        ]
        openalex_result = {
            "doi": "10.1234/test",
            "openalex_id": "W123",
            "coauthors": [],
            "abstract": None,
        }
        with (
            patch("openalex.Database.get_unenriched_papers", return_value=unenriched),
            patch("openalex.search_work", return_value=openalex_result),
            patch("openalex.Database.update_openalex_data"),
            patch("openalex._backfill_researcher_openalex_ids"),
            patch("openalex.time.sleep"),  # skip rate-limit delay in tests
        ):
            from openalex import enrich_new_publications
            count = enrich_new_publications()

        assert count == 2

    def test_returns_zero_when_nothing_to_enrich(self):
        with patch("openalex.Database.get_unenriched_papers", return_value=[]):
            from openalex import enrich_new_publications
            count = enrich_new_publications()

        assert count == 0


SAMPLE_OPENALEX_WORK = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.1257/aer.20181234",
    "title": "Trade and Wages",
    "authorships": [
        {
            "author": {
                "id": "https://openalex.org/A5023888391",
                "display_name": "Max Friedrich Steinhardt",
            },
        },
    ],
    "abstract_inverted_index": {"This": [0], "paper": [1], "studies": [2], "trade.": [3]},
}


class TestLookupByDoi:
    """Tests for openalex.lookup_by_doi — exact DOI lookup."""

    def test_returns_parsed_result(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = SAMPLE_OPENALEX_WORK

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import lookup_by_doi
            result = lookup_by_doi("10.1257/aer.20181234")

        assert result is not None
        assert result["doi"] == "10.1257/aer.20181234"
        assert result["openalex_id"] == "W2741809807"
        assert result["title"] == "Trade and Wages"
        assert len(result["coauthors"]) == 1

    def test_returns_none_on_404(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import lookup_by_doi
            result = lookup_by_doi("10.9999/nonexistent")

        assert result is None

    def test_returns_none_on_network_error(self):
        import requests as req
        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.side_effect = req.RequestException("timeout")
            from openalex import lookup_by_doi
            result = lookup_by_doi("10.1257/aer.20181234")

        assert result is None


class TestEnrichWithDoiFirst:
    """enrich_publication should try DOI lookup before title search."""

    def test_uses_doi_from_paper_links(self):
        """If paper has a DOI in paper_links, use it instead of title search."""
        openalex_result = {
            "doi": "10.1007/s40641-016-0032-z",
            "openalex_id": "W123",
            "title": "Extreme Air Pollution",
            "coauthors": [{"display_name": "A. Author", "openalex_author_id": "A111"}],
            "abstract": "Abstract text",
        }
        with (
            patch("openalex.lookup_by_doi", return_value=openalex_result) as mock_lookup,
            patch("openalex.search_work") as mock_search,
            patch("openalex.Database.update_openalex_data") as mock_update,
            patch("openalex._backfill_researcher_openalex_ids"),
        ):
            from openalex import enrich_publication
            result = enrich_publication(
                paper_id=1,
                title="Extreme Air Pollution",
                author_name="Author",
                existing_abstract=None,
                doi="10.1007/s40641-016-0032-z",
            )

        assert result is True
        mock_lookup.assert_called_once_with("10.1007/s40641-016-0032-z")
        mock_search.assert_not_called()


class TestBackfillResearcherOpenalexIds:
    """_backfill_researcher_openalex_ids populates openalex_author_id on researchers."""

    def test_updates_researcher_openalex_id(self):
        coauthors = [
            {"display_name": "Max Steinhardt", "openalex_author_id": "A5023888391"},
            {"display_name": "Jane Doe", "openalex_author_id": "A5000000001"},
        ]
        with (
            patch("openalex.Database.fetch_all", return_value=[
                {"id": 1, "first_name": "Max", "last_name": "Steinhardt", "openalex_author_id": None},
            ]),
            patch("openalex.Database.execute_query") as mock_exec,
        ):
            from openalex import _backfill_researcher_openalex_ids
            _backfill_researcher_openalex_ids(paper_id=10, coauthors=coauthors)

        mock_exec.assert_called_once()
        assert mock_exec.call_args[0][1] == ("A5023888391", 1)

    def test_skips_when_no_openalex_ids(self):
        coauthors = [{"display_name": "Author", "openalex_author_id": None}]
        with patch("openalex.Database.fetch_all") as mock_fetch:
            from openalex import _backfill_researcher_openalex_ids
            _backfill_researcher_openalex_ids(paper_id=10, coauthors=coauthors)
        mock_fetch.assert_not_called()


SAMPLE_TOPICS = [
    {
        "id": "https://openalex.org/T10001",
        "display_name": "Labor market dynamics and wage inequality",
        "score": 0.99,
        "subfield": {"id": "https://openalex.org/subfields/2002", "display_name": "Economics and Econometrics"},
        "field": {"id": "https://openalex.org/fields/20", "display_name": "Economics, Econometrics and Finance"},
        "domain": {"id": "https://openalex.org/domains/2", "display_name": "Social Sciences"},
    },
    {
        "id": "https://openalex.org/T10002",
        "display_name": "Migration and Labor Dynamics",
        "score": 0.85,
        "subfield": {"id": "https://openalex.org/subfields/3312", "display_name": "Sociology and Political Science"},
        "field": {"id": "https://openalex.org/fields/33", "display_name": "Social Sciences"},
        "domain": {"id": "https://openalex.org/domains/2", "display_name": "Social Sciences"},
    },
]


class TestParseWorkTopics:
    """Tests for topic extraction in _parse_work."""

    def test_parse_work_includes_topics(self):
        work = {
            **SAMPLE_OPENALEX_RESPONSE["results"][0],
            "topics": SAMPLE_TOPICS,
        }
        from openalex import _parse_work
        result = _parse_work(work)
        assert "topics" in result
        assert len(result["topics"]) == 2
        assert result["topics"][0]["openalex_topic_id"] == "T10001"
        assert result["topics"][0]["topic_name"] == "Labor market dynamics and wage inequality"
        assert result["topics"][0]["subfield_name"] == "Economics and Econometrics"
        assert result["topics"][0]["score"] == 0.99

    def test_parse_work_handles_no_topics(self):
        work = SAMPLE_OPENALEX_RESPONSE["results"][0]  # No topics key
        from openalex import _parse_work
        result = _parse_work(work)
        assert result["topics"] == []


class TestFetchTopicsBatch:
    """Tests for openalex.fetch_topics_batch."""

    def test_fetches_topics_for_multiple_works(self):
        api_response = {
            "results": [
                {"id": "https://openalex.org/W123", "topics": SAMPLE_TOPICS},
                {"id": "https://openalex.org/W456", "topics": SAMPLE_TOPICS[:1]},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200

        with patch("openalex._get_session") as mock_session, \
             patch("openalex._check_budget", return_value=True):
            mock_session.return_value.get.return_value = mock_resp
            from openalex import fetch_topics_batch
            result = fetch_topics_batch(["W123", "W456"])

        assert "W123" in result
        assert "W456" in result
        assert len(result["W123"]) == 2
        assert len(result["W456"]) == 1

    def test_returns_empty_on_api_error(self):
        import requests as req
        with patch("openalex._get_session") as mock_session, \
             patch("openalex._check_budget", return_value=True):
            mock_session.return_value.get.side_effect = req.RequestException("timeout")
            from openalex import fetch_topics_batch
            result = fetch_topics_batch(["W123"])

        assert result == {}

    def test_handles_empty_input(self):
        from openalex import fetch_topics_batch
        result = fetch_topics_batch([])
        assert result == {}


class TestParseWorkCoauthorFiltering:
    """_parse_work skips coauthors with bad display_names."""

    def test_skips_empty_display_name(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "", "id": "https://openalex.org/A1"}},
                {"author": {"display_name": "John Smith", "id": "https://openalex.org/A2"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 1
        assert result["coauthors"][0]["display_name"] == "John Smith"

    def test_skips_initial_only_first_name(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "A. Smith", "id": "https://openalex.org/A1"}},
                {"author": {"display_name": "John Doe", "id": "https://openalex.org/A2"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 1
        assert result["coauthors"][0]["display_name"] == "John Doe"

    def test_keeps_valid_coauthors(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "Jane Doe", "id": "https://openalex.org/A1"}},
                {"author": {"display_name": "John Smith", "id": "https://openalex.org/A2"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 2

    def test_skips_whitespace_only_name(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "   ", "id": "https://openalex.org/A1"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 0

    def test_keeps_single_word_name(self):
        """Some authors have mononyms (e.g. 'Sukarno'). These are valid."""
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [
                {"author": {"display_name": "Sukarno", "id": "https://openalex.org/A1"}},
            ],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert len(result["coauthors"]) == 1


class TestParseWorkYearExtraction:
    """_parse_work extracts publication_year from OpenAlex work objects."""

    def test_extracts_year_as_string(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [],
            "abstract_inverted_index": None,
            "topics": [],
            "publication_year": 2024,
        }
        result = _parse_work(work)
        assert result["year"] == "2024"

    def test_missing_year_returns_none(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [],
            "abstract_inverted_index": None,
            "topics": [],
        }
        result = _parse_work(work)
        assert result["year"] is None

    def test_null_year_returns_none(self):
        from openalex import _parse_work
        work = {
            "doi": "https://doi.org/10.1234/test",
            "id": "https://openalex.org/W123",
            "authorships": [],
            "abstract_inverted_index": None,
            "topics": [],
            "publication_year": None,
        }
        result = _parse_work(work)
        assert result["year"] is None


class TestEnrichPublicationYear:
    """enrich_publication passes year from OpenAlex to update_openalex_data."""

    @patch("openalex.lookup_by_doi")
    @patch("openalex.Database")
    def test_passes_year_to_update(self, mock_db, mock_lookup):
        mock_lookup.return_value = {
            "doi": "10.1234/test",
            "openalex_id": "W123",
            "coauthors": [],
            "abstract": None,
            "topics": [],
            "year": "2024",
        }

        from openalex import enrich_publication
        enrich_publication(
            paper_id=1,
            title="Test Paper",
            author_name="Smith",
            doi="10.1234/test",
        )

        mock_db.update_openalex_data.assert_called_once()
        call_kwargs = mock_db.update_openalex_data.call_args
        # Check year was passed (either as kwarg or positional)
        if call_kwargs.kwargs:
            assert call_kwargs.kwargs.get("year") == "2024"
        else:
            # positional: paper_id, doi, openalex_id, coauthors, abstract, year
            assert "2024" in call_kwargs.args or any("2024" in str(a) for a in call_kwargs.args)
