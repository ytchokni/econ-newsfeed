"""Tests for the LLM extraction pipeline in publication.py.

Covers extract_publications(), save_publications(), build_extraction_prompt(),
and the PublicationExtraction Pydantic model.  All external dependencies
(OpenAI client, Database) are mocked — no network or DB connections required.
"""

import os

# Ensure required env vars are present before any app-level imports.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("CONTENT_MAX_CHARS", "4000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest

from publication import Publication, PublicationExtraction, PublicationExtractionList, _title_similarity, reconcile_title_renames


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pub_dict(**overrides):
    """Return a minimal valid publication dict (as returned by extract_publications)."""
    base = {
        "title": "Trade and Wages",
        "authors": [["John", "Smith"], ["Jane", "Doe"]],
        "year": "2024",
        "venue": "AER",
        "status": "working_paper",
        "draft_url": "https://example.com/paper.pdf",
        "abstract": "We study trade.",
    }
    base.update(overrides)
    return base


def _make_openai_response(publications: list[dict], refusal=None):
    """Build a mock OpenAI chat completion that mimics structured output parsing."""
    parsed_pubs = [PublicationExtraction(**p) for p in publications]
    parsed_result = PublicationExtractionList(publications=parsed_pubs)

    message = MagicMock()
    message.refusal = refusal
    message.parsed = parsed_result

    choice = MagicMock()
    choice.message = message

    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    usage.total_tokens = 150

    completion = MagicMock()
    completion.choices = [choice]
    completion.usage = usage
    return completion


def _make_connection_context(cursor):
    """Return a context-manager mock that yields a connection whose .cursor() returns *cursor*."""
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def _ctx():
        yield conn

    return _ctx, conn


# ---------------------------------------------------------------------------
# 1. extract_publications()
# ---------------------------------------------------------------------------

class TestExtractPublications:
    """Tests for Publication.extract_publications()."""

    @pytest.fixture(autouse=True)
    def _patch_content_max(self):
        """CONTENT_MAX_CHARS is read from env as a string at import time.
        Patch it to an int so text_content[:CONTENT_MAX_CHARS] works."""
        with patch("publication.CONTENT_MAX_CHARS", 4000):
            yield

    @patch("publication.Database.log_llm_usage")
    @patch("publication._openai_client")
    def test_happy_path(self, mock_client, mock_log_usage):
        """Valid structured response -> list of publication dicts."""
        pub = _make_pub_dict()
        mock_client.beta.chat.completions.parse.return_value = _make_openai_response([pub])

        result = Publication.extract_publications("some text", "https://example.com/page", scrape_log_id=7)

        assert len(result) == 1
        assert result[0]["title"] == "Trade and Wages"
        assert result[0]["authors"] == [["John", "Smith"], ["Jane", "Doe"]]
        assert result[0]["year"] == "2024"
        assert result[0]["venue"] == "AER"
        assert result[0]["status"] == "working_paper"
        assert result[0]["draft_url"] == "https://example.com/paper.pdf"

        # Verify the OpenAI client was called with the correct model
        mock_client.beta.chat.completions.parse.assert_called_once()

    @patch("publication.Database.log_llm_usage")
    @patch("publication._openai_client")
    def test_multiple_publications(self, mock_client, mock_log_usage):
        """Multiple publications in a single response are all returned."""
        pubs = [
            _make_pub_dict(title="Paper A"),
            _make_pub_dict(title="Paper B"),
        ]
        mock_client.beta.chat.completions.parse.return_value = _make_openai_response(pubs)

        result = Publication.extract_publications("text", "https://example.com")

        assert len(result) == 2
        assert result[0]["title"] == "Paper A"
        assert result[1]["title"] == "Paper B"

    @patch("publication.Database.log_llm_usage")
    @patch("publication._openai_client")
    def test_refusal_returns_empty(self, mock_client, mock_log_usage):
        """Model refusal -> empty list, no crash."""
        response = _make_openai_response([_make_pub_dict()])
        response.choices[0].message.refusal = "I cannot process this content."
        mock_client.beta.chat.completions.parse.return_value = response

        result = Publication.extract_publications("text", "https://example.com")

        assert result == []

    @patch("publication.Database.log_llm_usage")
    @patch("publication._openai_client")
    def test_parsed_none_returns_empty(self, mock_client, mock_log_usage):
        """Parsed result is None -> empty list."""
        response = _make_openai_response([_make_pub_dict()])
        response.choices[0].message.refusal = None
        response.choices[0].message.parsed = None
        mock_client.beta.chat.completions.parse.return_value = response

        result = Publication.extract_publications("text", "https://example.com")

        assert result == []

    @patch("publication.Database.log_llm_usage")
    @patch("publication._openai_client")
    def test_api_error_returns_empty_and_logs(self, mock_client, mock_log_usage, caplog):
        """OpenAI API exception -> empty list and error is logged."""
        mock_client.beta.chat.completions.parse.side_effect = RuntimeError("API down")

        with caplog.at_level(logging.ERROR):
            result = Publication.extract_publications("text", "https://example.com")

        assert result == []
        assert any("API down" in record.message for record in caplog.records)

    @patch("publication.Database.log_llm_usage")
    @patch("publication._openai_client")
    def test_llm_usage_logged(self, mock_client, mock_log_usage):
        """Database.log_llm_usage is called with correct arguments on success."""
        pub = _make_pub_dict()
        response = _make_openai_response([pub])
        mock_client.beta.chat.completions.parse.return_value = response

        Publication.extract_publications("text", "https://example.com/page", scrape_log_id=42)

        mock_log_usage.assert_called_once()
        args, kwargs = mock_log_usage.call_args
        assert args[0] == "publication_extraction"
        # model is the second positional arg
        assert args[2] is response.usage
        assert kwargs.get("context_url") == "https://example.com/page"
        assert kwargs.get("scrape_log_id") == 42

    @patch("publication.Database.log_llm_usage")
    @patch("publication._openai_client")
    def test_llm_usage_not_logged_on_api_error(self, mock_client, mock_log_usage):
        """If the API call itself throws, log_llm_usage should NOT be called."""
        mock_client.beta.chat.completions.parse.side_effect = RuntimeError("boom")

        Publication.extract_publications("text", "https://example.com")

        mock_log_usage.assert_not_called()


# ---------------------------------------------------------------------------
# 2. save_publications()
# ---------------------------------------------------------------------------

class TestSavePublications:
    """Tests for Publication.save_publications()."""

    def _setup_cursor(self, lastrowid=1):
        """Create a mock cursor and wired-up connection context."""
        cursor = MagicMock()
        cursor.lastrowid = lastrowid
        cursor.fetchone.return_value = None
        ctx_factory, conn = _make_connection_context(cursor)
        return cursor, conn, ctx_factory

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_happy_path_insert(self, mock_get_conn, mock_hash, mock_get_rid):
        """New publication is inserted, paper_urls row added, authorship rows created."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=5)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict()
        Publication.save_publications("https://example.com", [pub])

        # The first execute is the INSERT IGNORE INTO papers
        first_call_sql = cursor.execute.call_args_list[0][0][0]
        assert "INSERT IGNORE INTO papers" in first_call_sql

        # paper_urls INSERT
        second_call_sql = cursor.execute.call_args_list[1][0][0]
        assert "INSERT IGNORE INTO paper_urls" in second_call_sql

        # feed_event INSERT (status=working_paper, is_seed=False)
        third_call_sql = cursor.execute.call_args_list[2][0][0]
        assert "INSERT INTO feed_events" in third_call_sql

        # Two authors -> two authorship INSERTs
        authorship_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT IGNORE INTO authorship" in c[0][0]
        ]
        assert len(authorship_calls) == 2

        conn.commit.assert_called_once()

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_dedup_lookup_existing(self, mock_get_conn, mock_hash, mock_get_rid):
        """When INSERT IGNORE hits a duplicate (lastrowid=0), existing paper is looked up."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=0)
        # cursor.fetchone() returns a tuple (not dict) inside save_publications
        cursor.fetchone.return_value = (42,)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict()
        Publication.save_publications("https://example.com", [pub])

        # Should execute SELECT id FROM papers WHERE title_hash = %s
        select_calls = [
            c for c in cursor.execute.call_args_list
            if "SELECT id FROM papers WHERE title_hash" in c[0][0]
        ]
        assert len(select_calls) == 1

        # Should still insert paper_urls for the existing paper
        paper_url_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT IGNORE INTO paper_urls" in c[0][0]
        ]
        assert len(paper_url_calls) == 1
        # Verify paper_id=42 is passed
        assert paper_url_calls[0][0][1][0] == 42

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_dedup_not_found_skips(self, mock_get_conn, mock_hash, mock_get_rid, caplog):
        """When INSERT IGNORE duplicate but SELECT returns nothing, log error and skip."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=0)
        cursor.fetchone.return_value = None
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict()
        with caplog.at_level(logging.ERROR):
            Publication.save_publications("https://example.com", [pub])

        assert any("Could not find publication" in r.message for r in caplog.records)

        # No authorship inserts should have happened since we continued
        authorship_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT IGNORE INTO authorship" in c[0][0]
        ]
        assert len(authorship_calls) == 0

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_feed_event_created_for_non_seed_non_published(self, mock_get_conn, mock_hash, mock_get_rid):
        """Non-seed paper with status != 'published' creates a feed_event."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=10)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status="accepted")
        Publication.save_publications("https://example.com", [pub], is_seed=False)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 1
        # new_status param should be 'accepted'
        params = feed_calls[0][0][1]
        assert params[1] == "accepted"

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_seed_papers_no_feed_event(self, mock_get_conn, mock_hash, mock_get_rid):
        """is_seed=True should NOT create feed_events."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=10)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status="working_paper")
        Publication.save_publications("https://example.com", [pub], is_seed=True)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 0

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_published_papers_no_feed_event(self, mock_get_conn, mock_hash, mock_get_rid):
        """status='published' should NOT create feed_events even for non-seed."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=10)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status="published")
        Publication.save_publications("https://example.com", [pub], is_seed=False)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 0

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_null_status_no_feed_event(self, mock_get_conn, mock_hash, mock_get_rid):
        """status=None should NOT create feed_events (guard on `pub_status` being truthy)."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=10)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status=None)
        Publication.save_publications("https://example.com", [pub], is_seed=False)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 0

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_author_processing(self, mock_get_conn, mock_hash, mock_get_rid):
        """Each author generates an authorship INSERT with correct author_order."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=5)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(authors=[["Alice", "Wu"], ["Bob", "Chen"], ["Carol", "Li"]])
        Publication.save_publications("https://example.com", [pub])

        authorship_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT IGNORE INTO authorship" in c[0][0]
        ]
        assert len(authorship_calls) == 3

        # Verify author_order: 1, 2, 3
        orders = [c[0][1][2] for c in authorship_calls]
        assert orders == [1, 2, 3]

        # All should use researcher_id=99
        researcher_ids = [c[0][1][0] for c in authorship_calls]
        assert researcher_ids == [99, 99, 99]

        # get_researcher_id should be called for each author
        assert mock_get_rid.call_count == 3
        mock_get_rid.assert_any_call("Alice", "Wu", conn=conn)
        mock_get_rid.assert_any_call("Bob", "Chen", conn=conn)
        mock_get_rid.assert_any_call("Carol", "Li", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_error_handling_rollback_and_log(self, mock_get_conn, mock_hash, mock_get_rid, caplog):
        """Exception during save triggers rollback and logs the publication title."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=5)

        # Make cursor.execute raise on the second call (paper_urls INSERT)
        call_count = 0
        original_execute = cursor.execute

        def _exploding_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("disk full")

        cursor.execute.side_effect = _exploding_execute
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(title="My Important Paper")
        with caplog.at_level(logging.ERROR):
            Publication.save_publications("https://example.com", [pub])

        # Should log the publication title, not just the class name
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("My Important Paper" in r.message for r in error_records)
        conn.rollback.assert_called_once()

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_multiple_publications_processed(self, mock_get_conn, mock_hash, mock_get_rid, caplog):
        """Multiple pubs each get their own connection context and are committed."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=5)
        # Need a fresh context each call
        mock_get_conn.side_effect = lambda: ctx_factory()

        pubs = [_make_pub_dict(title="Paper A"), _make_pub_dict(title="Paper B")]
        with caplog.at_level(logging.INFO):
            Publication.save_publications("https://example.com", pubs)

        assert any("2 publications processed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 3. build_extraction_prompt()
# ---------------------------------------------------------------------------

class TestBuildExtractionPrompt:
    """Tests for Publication.build_extraction_prompt()."""

    @pytest.fixture(autouse=True)
    def _patch_content_max(self):
        """CONTENT_MAX_CHARS is read from env as a string at import time.
        Patch it to an int so text_content[:CONTENT_MAX_CHARS] works."""
        with patch("publication.CONTENT_MAX_CHARS", 4000):
            yield

    def test_contains_url(self):
        """Prompt includes the page URL."""
        prompt = Publication.build_extraction_prompt("some text", "https://econ.example.com")
        assert "https://econ.example.com" in prompt

    def test_contains_text_content(self):
        """Prompt includes the supplied text content."""
        prompt = Publication.build_extraction_prompt("Publications listed here", "https://example.com")
        assert "Publications listed here" in prompt

    def test_content_truncated_to_max_chars(self):
        """Content in prompt is truncated to CONTENT_MAX_CHARS."""
        max_chars = 4000  # matches the autouse fixture
        long_text = "A" * (max_chars + 5000)

        prompt = Publication.build_extraction_prompt(long_text, "https://example.com")

        # The prompt should contain at most max_chars 'A's
        a_count = prompt.count("A")
        assert a_count == max_chars

    def test_prompt_includes_extraction_instructions(self):
        """Prompt asks for title, authors, year, venue, status, draft_url, abstract."""
        prompt = Publication.build_extraction_prompt("content", "https://example.com")
        for field in ["title", "authors", "year", "venue", "status", "draft_url", "abstract"]:
            assert field in prompt


# ---------------------------------------------------------------------------
# 4. PublicationExtraction model
# ---------------------------------------------------------------------------

class TestPublicationExtractionModel:
    """Tests for the PublicationExtraction Pydantic model."""

    def test_valid_data(self):
        """Fully valid data passes validation."""
        data = {
            "title": "Trade and Wages",
            "authors": [["John", "Smith"]],
            "year": "2024",
            "venue": "AER",
            "status": "published",
            "draft_url": "https://example.com/paper.pdf",
            "abstract": "We study trade.",
        }
        pub = PublicationExtraction(**data)
        assert pub.title == "Trade and Wages"
        assert pub.year == "2024"
        assert pub.draft_url == "https://example.com/paper.pdf"

    def test_year_coerced_from_int(self):
        """Integer year is coerced to string."""
        pub = PublicationExtraction(
            title="Paper", authors=[["A", "B"]], year=2024
        )
        assert pub.year == "2024"
        assert isinstance(pub.year, str)

    def test_year_none_stays_none(self):
        """None year stays None."""
        pub = PublicationExtraction(title="Paper", authors=[["A", "B"]], year=None)
        assert pub.year is None

    def test_draft_url_invalid_scheme_returns_none(self):
        """Non-http(s) scheme (ftp, javascript, data) is rejected -> None."""
        pub = PublicationExtraction(
            title="Paper", authors=[["A", "B"]], draft_url="ftp://example.com/paper.pdf"
        )
        assert pub.draft_url is None

    def test_draft_url_javascript_scheme_returns_none(self):
        pub = PublicationExtraction(
            title="Paper", authors=[["A", "B"]], draft_url="javascript:alert(1)"
        )
        assert pub.draft_url is None

    def test_draft_url_valid_http_preserved(self):
        pub = PublicationExtraction(
            title="Paper", authors=[["A", "B"]], draft_url="http://example.com/paper.pdf"
        )
        assert pub.draft_url == "http://example.com/paper.pdf"

    def test_draft_url_valid_https_preserved(self):
        pub = PublicationExtraction(
            title="Paper", authors=[["A", "B"]], draft_url="https://ssrn.com/12345"
        )
        assert pub.draft_url == "https://ssrn.com/12345"

    def test_draft_url_none_preserved(self):
        pub = PublicationExtraction(
            title="Paper", authors=[["A", "B"]], draft_url=None
        )
        assert pub.draft_url is None

    def test_draft_url_empty_string_returns_none(self):
        """Empty string has no scheme -> None."""
        pub = PublicationExtraction(
            title="Paper", authors=[["A", "B"]], draft_url=""
        )
        assert pub.draft_url is None

    def test_optional_fields_default_none(self):
        """Only title and authors are required; everything else defaults to None."""
        pub = PublicationExtraction(title="Paper", authors=[["A", "B"]])
        assert pub.year is None
        assert pub.venue is None
        assert pub.status is None
        assert pub.draft_url is None
        assert pub.abstract is None

    def test_valid_statuses(self):
        """All valid status literals are accepted."""
        for status in ["published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper"]:
            pub = PublicationExtraction(title="P", authors=[["A", "B"]], status=status)
            assert pub.status == status

    def test_invalid_status_raises(self):
        """An invalid status string raises a validation error."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PublicationExtraction(title="P", authors=[["A", "B"]], status="under_review")

    def test_model_dump_roundtrip(self):
        """model_dump() output can be used to reconstruct the model."""
        original = PublicationExtraction(
            title="Paper",
            authors=[["John", "Smith"]],
            year="2024",
            venue="QJE",
            status="accepted",
            draft_url="https://example.com/draft.pdf",
            abstract="Abstract here.",
        )
        dumped = original.model_dump()
        reconstructed = PublicationExtraction(**dumped)
        assert reconstructed == original


# ---------------------------------------------------------------------------
# 5. _title_similarity()
# ---------------------------------------------------------------------------

class TestTitleSimilarity:
    """Tests for _title_similarity() Jaccard word overlap."""

    def test_identical_titles(self):
        assert _title_similarity("Trade and Wages", "Trade and Wages") == 1.0

    def test_completely_different(self):
        assert _title_similarity("Trade and Wages", "Climate Change Effects") == 0.0

    def test_partial_overlap_above_threshold(self):
        old = "Policies, Prejudice, and the Residual Wage Gap between Refugees and Natives"
        new = "Market Structures, Prejudice, and the Residual Wage Gap between Refugees and Natives"
        sim = _title_similarity(old, new)
        assert sim >= 0.5

    def test_empty_title(self):
        assert _title_similarity("", "Trade and Wages") == 0.0
        assert _title_similarity("Trade and Wages", "") == 0.0

    def test_none_title(self):
        assert _title_similarity(None, "Trade and Wages") == 0.0

    def test_low_overlap_below_threshold(self):
        sim = _title_similarity("International Trade Theory", "Domestic Labor Markets")
        assert sim < 0.5


# ---------------------------------------------------------------------------
# 6. reconcile_title_renames()
# ---------------------------------------------------------------------------

class TestReconcileTitleRenames:
    """Tests for reconcile_title_renames() post-extraction reconciliation."""

    @patch("publication.Database.append_paper_snapshot")
    @patch("publication.Database.get_connection")
    @patch("publication.Database.fetch_all")
    def test_detects_rename(self, mock_fetch_all, mock_get_conn, mock_snapshot):
        """A disappeared title + appeared title with high overlap -> rename."""
        mock_fetch_all.return_value = [
            {"id": 10, "title": "Policies, Prejudice, and the Wage Gap", "title_hash": "old_hash"},
        ]
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None  # No duplicate paper found

        @contextmanager
        def _ctx():
            yield mock_conn
        mock_get_conn.side_effect = lambda: _ctx()

        extracted = [
            _make_pub_dict(title="Market Structures, Prejudice, and the Wage Gap"),
        ]

        reconcile_title_renames("https://example.com", extracted)

        # Should UPDATE the paper title and title_hash
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE papers SET title" in str(c)
        ]
        assert len(update_calls) == 1

        # Should INSERT a title_change feed event
        feed_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "title_change" in str(c)
        ]
        assert len(feed_calls) == 1

    @patch("publication.Database.append_paper_snapshot")
    @patch("publication.Database.get_connection")
    @patch("publication.Database.fetch_all")
    def test_no_rename_when_low_similarity(self, mock_fetch_all, mock_get_conn, mock_snapshot):
        """Titles with < 0.5 Jaccard similarity are NOT treated as renames."""
        mock_fetch_all.return_value = [
            {"id": 10, "title": "International Trade Theory", "title_hash": "old_hash"},
        ]
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        @contextmanager
        def _ctx():
            yield mock_conn
        mock_get_conn.side_effect = lambda: _ctx()

        extracted = [_make_pub_dict(title="Domestic Labor Markets and Employment")]

        reconcile_title_renames("https://example.com", extracted)

        # Should NOT update any paper -- low similarity returns early before get_connection
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE papers SET title" in str(c)
        ]
        assert len(update_calls) == 0

    @patch("publication.Database.append_paper_snapshot")
    @patch("publication.Database.get_connection")
    @patch("publication.Database.fetch_all")
    def test_no_op_when_all_titles_match(self, mock_fetch_all, mock_get_conn, mock_snapshot):
        """When all extracted titles match DB titles, no reconciliation needed."""
        mock_fetch_all.return_value = [
            {"id": 10, "title": "Trade and Wages", "title_hash": "hash1"},
        ]

        extracted = [_make_pub_dict(title="Trade and Wages")]

        reconcile_title_renames("https://example.com", extracted)

        # get_connection should NOT be called since appeared/disappeared are empty
        mock_get_conn.assert_not_called()

    @patch("publication.Database.append_paper_snapshot")
    @patch("publication.Database.get_connection")
    @patch("publication.Database.fetch_all")
    def test_cleans_up_duplicate_paper(self, mock_fetch_all, mock_get_conn, mock_snapshot):
        """If save_publications already inserted a duplicate, it should be deleted."""
        mock_fetch_all.return_value = [
            {"id": 10, "title": "Policies and the Wage Gap", "title_hash": "old_hash"},
        ]
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (99,)  # Duplicate paper exists

        @contextmanager
        def _ctx():
            yield mock_conn
        mock_get_conn.side_effect = lambda: _ctx()

        extracted = [_make_pub_dict(title="Market Structures and the Wage Gap")]

        reconcile_title_renames("https://example.com", extracted)

        # Should DELETE the duplicate paper
        delete_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "DELETE FROM papers" in str(c)
        ]
        assert len(delete_calls) == 1
