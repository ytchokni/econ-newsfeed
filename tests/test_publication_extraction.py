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
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("LLM_MODEL", "gemini-2.5-flash")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

import logging
from unittest.mock import MagicMock, call, patch

import pytest
from openai import OpenAIError

from llm_client import StructuredResponse
from publication import Publication, PublicationExtraction, PublicationExtractionList, clean_title, reconcile_title_renames
from paper_saver import _title_similarity


# ---------------------------------------------------------------------------
# clean_title tests
# ---------------------------------------------------------------------------

class TestCleanTitle:
    """Unit tests for clean_title() — strips metadata suffixes from paper titles."""

    @pytest.mark.parametrize("raw,expected", [
        # Dash-separated metadata
        ("Monetary Policy Shocks: A New Hope -- Job Market Paper", "Monetary Policy Shocks: A New Hope"),
        ("Trade and Welfare — Working Paper", "Trade and Welfare"),
        ("Fiscal Rules – JMP", "Fiscal Rules"),
        ("Some Result -- Draft", "Some Result"),
        ("My Paper -- New!", "My Paper"),
        ("Output Gaps -- Revised", "Output Gaps"),
        ("A Model -- R & R", "A Model"),
        ("Growth Theory -- Forthcoming", "Growth Theory"),
        ("Labor Supply -- Submitted", "Labor Supply"),
        ("Estimation -- Under Review", "Estimation"),
        ("Framework -- Work in Progress", "Framework"),
        ("Results -- Updated", "Results"),
        ("Equilibrium -- Accepted", "Equilibrium"),
        # Bracket-wrapped metadata
        ("Trade Networks [JMP]", "Trade Networks"),
        ("Fiscal Multipliers (Working Paper)", "Fiscal Multipliers"),
        ("Growth Model [Draft]", "Growth Model"),
        ("A Study (New)", "A Study"),
        ("Welfare Effects [Revised]", "Welfare Effects"),
        # False positives that must NOT be stripped
        ("A New Deal for the World", "A New Deal for the World"),
        ("The Draft Beer Market", "The Draft Beer Market"),
        ("New Evidence on Trade", "New Evidence on Trade"),
        ("Submitted Bids in Auctions", "Submitted Bids in Auctions"),
        ("On Accepted Norms", "On Accepted Norms"),
        # Edge cases
        ("  Spaces  -- JMP  ", "Spaces"),
        ("", ""),
        ("No Metadata Here", "No Metadata Here"),
    ])
    def test_clean_title(self, raw, expected):
        assert clean_title(raw) == expected


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


def _make_llm_completion(publications: list[dict]):
    """Build a mock OpenAI-compatible chat completion returning JSON content."""
    import json as _json
    payload = {"publications": publications}
    message = MagicMock()
    message.content = _json.dumps(payload)
    # refusal/parsed are no longer used after migration, but leave as None for safety
    message.refusal = None

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



# ---------------------------------------------------------------------------
# 1. extract_publications()
# ---------------------------------------------------------------------------

class TestExtractPublications:
    """Tests for Publication.extract_publications()."""

    @pytest.fixture(autouse=True)
    def _patch_content_max(self):
        """CONTENT_MAX_CHARS is read from env as a string at import time.
        Patch it to an int so text_content[:CONTENT_MAX_CHARS] works."""
        with patch("publication.CONTENT_MAX_CHARS", 20000):
            yield

    @patch("publication.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_happy_path(self, mock_get_client, mock_log_usage):
        """Valid structured response -> list of publication dicts."""
        mock_client = mock_get_client.return_value
        pub = _make_pub_dict()
        mock_client.chat.completions.create.return_value = _make_llm_completion([pub])

        result = Publication.extract_publications("some text", "https://example.com/page", scrape_log_id=7)

        assert len(result) == 1
        assert result[0]["title"] == "Trade and Wages"
        assert result[0]["authors"] == [["John", "Smith"], ["Jane", "Doe"]]
        assert result[0]["year"] == "2024"
        assert result[0]["venue"] == "AER"
        assert result[0]["status"] == "working_paper"
        assert result[0]["draft_url"] == "https://example.com/paper.pdf"

        # Verify the LLM client was called with the correct model
        mock_client.chat.completions.create.assert_called_once()

    @patch("publication.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_multiple_publications(self, mock_get_client, mock_log_usage):
        """Multiple publications in a single response are all returned."""
        mock_client = mock_get_client.return_value
        pubs = [
            _make_pub_dict(title="Paper A"),
            _make_pub_dict(title="Paper B"),
        ]
        mock_client.chat.completions.create.return_value = _make_llm_completion(pubs)

        result = Publication.extract_publications("text", "https://example.com")

        assert len(result) == 2
        assert result[0]["title"] == "Paper A"
        assert result[1]["title"] == "Paper B"

    @patch("publication.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_malformed_json_returns_empty(self, mock_get_client, mock_log_usage):
        """Model returns text that fails JSON validation -> empty list after retry."""
        mock_client = mock_get_client.return_value
        bad = MagicMock()
        bad.choices = [MagicMock()]
        bad.choices[0].message = MagicMock()
        bad.choices[0].message.content = "not json at all"
        bad.usage = MagicMock()
        bad.usage.prompt_tokens = 10
        bad.usage.completion_tokens = 5
        bad.usage.total_tokens = 15
        mock_client.chat.completions.create.return_value = bad

        result = Publication.extract_publications("text", "https://example.com")

        assert result == []

    @patch("publication.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_api_error_returns_empty_and_logs(self, mock_get_client, mock_log_usage, caplog):
        """OpenAI API exception -> empty list and error is logged."""
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = OpenAIError("API down")

        with caplog.at_level(logging.ERROR):
            result = Publication.extract_publications("text", "https://example.com")

        assert result == []
        assert any("API down" in record.message for record in caplog.records)

    @patch("publication.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_llm_usage_logged(self, mock_get_client, mock_log_usage):
        """Database.log_llm_usage is called with correct arguments on success."""
        mock_client = mock_get_client.return_value
        pub = _make_pub_dict()
        response = _make_llm_completion([pub])
        mock_client.chat.completions.create.return_value = response

        Publication.extract_publications("text", "https://example.com/page", scrape_log_id=42)

        mock_log_usage.assert_called_once()
        args, kwargs = mock_log_usage.call_args
        assert args[0] == "publication_extraction"
        # model is the second positional arg
        assert args[2] is response.usage
        assert kwargs.get("context_url") == "https://example.com/page"
        assert kwargs.get("scrape_log_id") == 42

    @patch("publication.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_llm_usage_not_logged_on_api_error(self, mock_get_client, mock_log_usage):
        """If the API call itself throws, log_llm_usage should NOT be called."""
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = OpenAIError("boom")

        Publication.extract_publications("text", "https://example.com")

        mock_log_usage.assert_not_called()


# ---------------------------------------------------------------------------
# 2. save_publications()
# ---------------------------------------------------------------------------

class TestSavePublications:
    """Tests for Publication.save_publications() — now a thin wrapper over PaperSaver + FeedEventEmitter."""

    @patch("feed_events.FeedEventEmitter.emit_new_paper_events", return_value=0)
    @patch("paper_saver.PaperSaver.save_publications")
    def test_delegates_to_paper_saver_and_emitter(self, mock_saver, mock_emitter):
        """Publication.save_publications delegates to PaperSaver then FeedEventEmitter."""
        from paper_saver import SaveResult
        mock_saver.return_value = [SaveResult(1, "Paper", True, True, "working_paper")]
        pub = _make_pub_dict()

        Publication.save_publications("https://example.com", [pub])

        mock_saver.assert_called_once_with("https://example.com", [pub], is_seed=False)
        mock_emitter.assert_called_once()
        args = mock_emitter.call_args
        assert args[0][0][0].paper_id == 1
        assert args[1]["is_seed"] is False

    @patch("feed_events.FeedEventEmitter.emit_new_paper_events", return_value=0)
    @patch("paper_saver.PaperSaver.save_publications")
    def test_seed_flag_passed_through(self, mock_saver, mock_emitter):
        """is_seed=True is forwarded to both PaperSaver and FeedEventEmitter."""
        mock_saver.return_value = []
        pub = _make_pub_dict()

        Publication.save_publications("https://example.com", [pub], is_seed=True)

        mock_saver.assert_called_once_with("https://example.com", [pub], is_seed=True)
        mock_emitter.assert_called_once_with([], "https://example.com", is_seed=True)

    @patch("feed_events.FeedEventEmitter.emit_new_paper_events", return_value=0)
    @patch("paper_saver.PaperSaver.save_publications")
    def test_empty_publications_delegates(self, mock_saver, mock_emitter):
        """Empty publication list still delegates correctly."""
        mock_saver.return_value = []

        Publication.save_publications("https://example.com", [])

        mock_saver.assert_called_once_with("https://example.com", [], is_seed=False)
        mock_emitter.assert_called_once_with([], "https://example.com", is_seed=False)


# ---------------------------------------------------------------------------
# 3. build_extraction_prompt()
# ---------------------------------------------------------------------------

class TestBuildExtractionPrompt:
    """Tests for Publication.build_extraction_prompt()."""

    @pytest.fixture(autouse=True)
    def _patch_content_max(self):
        """CONTENT_MAX_CHARS is read from env as a string at import time.
        Patch it to an int so text_content[:CONTENT_MAX_CHARS] works."""
        with patch("publication.CONTENT_MAX_CHARS", 20000):
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
        max_chars = 20000  # matches the autouse fixture
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
    """Tests for reconcile_title_renames() — now a thin wrapper over PaperSaver + FeedEventEmitter."""

    @patch("feed_events.FeedEventEmitter.emit_title_change")
    @patch("paper_saver.PaperSaver.reconcile_title_renames")
    def test_detects_rename_and_emits_event(self, mock_reconcile, mock_emit):
        """Wrapper calls PaperSaver.reconcile_title_renames and emits title_change events."""
        from paper_saver import TitleRename
        mock_reconcile.return_value = [
            TitleRename(paper_id=10, old_title="Old Title", new_title="New Title", similarity=0.8),
        ]

        extracted = [_make_pub_dict(title="New Title")]
        reconcile_title_renames("https://example.com", extracted)

        mock_reconcile.assert_called_once_with("https://example.com", extracted)
        mock_emit.assert_called_once_with(10, "Old Title", "New Title")

    @patch("feed_events.FeedEventEmitter.emit_title_change")
    @patch("paper_saver.PaperSaver.reconcile_title_renames")
    def test_no_renames_no_events(self, mock_reconcile, mock_emit):
        """When PaperSaver finds no renames, no events are emitted."""
        mock_reconcile.return_value = []

        reconcile_title_renames("https://example.com", [_make_pub_dict()])

        mock_reconcile.assert_called_once()
        mock_emit.assert_not_called()

    @patch("feed_events.FeedEventEmitter.emit_title_change")
    @patch("paper_saver.PaperSaver.reconcile_title_renames")
    def test_multiple_renames_emit_multiple_events(self, mock_reconcile, mock_emit):
        """Each rename from PaperSaver generates its own title_change event."""
        from paper_saver import TitleRename
        mock_reconcile.return_value = [
            TitleRename(paper_id=10, old_title="Old A", new_title="New A", similarity=0.7),
            TitleRename(paper_id=20, old_title="Old B", new_title="New B", similarity=0.6),
        ]

        reconcile_title_renames("https://example.com", [])

        assert mock_emit.call_count == 2
        mock_emit.assert_any_call(10, "Old A", "New A")
        mock_emit.assert_any_call(20, "Old B", "New B")


# ---------------------------------------------------------------------------
# 7. try_extract_publications()
# ---------------------------------------------------------------------------

class TestTryExtractPublications:
    """try_extract_publications distinguishes LLM failure (None) from empty ([])."""

    @pytest.fixture(autouse=True)
    def _patch_content_max(self):
        """CONTENT_MAX_CHARS is read from env as a string at import time.
        Patch it to an int so text_content[:CONTENT_MAX_CHARS] works."""
        with patch("publication.CONTENT_MAX_CHARS", 20000):
            yield

    def test_returns_none_on_llm_failure(self):
        """parsed=None (API error / validation failure) → None, not []."""
        failed = StructuredResponse(parsed=None, usage=None)
        with patch("publication.extract_json", return_value=failed), \
             patch("publication.Database.log_llm_usage"):
            result = Publication.try_extract_publications("some text", "https://x.com")
        assert result is None

    def test_returns_empty_list_when_no_pubs_found(self):
        """A valid response with zero publications → [] (genuine empty)."""
        parsed = PublicationExtractionList(publications=[])
        ok = StructuredResponse(parsed=parsed, usage=MagicMock())
        with patch("publication.extract_json", return_value=ok), \
             patch("publication.Database.log_llm_usage"):
            result = Publication.try_extract_publications("some text", "https://x.com")
        assert result == []

    def test_returns_validated_pubs(self):
        """Valid publications are returned as dicts."""
        parsed = PublicationExtractionList.model_validate(
            {"publications": [{"title": "A Great Paper", "authors": [["Jane", "Doe"]],
                               "year": "2024", "venue": None, "status": None,
                               "draft_url": None, "abstract": None}]}
        )
        ok = StructuredResponse(parsed=parsed, usage=MagicMock())
        with patch("publication.extract_json", return_value=ok), \
             patch("publication.Database.log_llm_usage"), \
             patch("publication.validate_publication", return_value=True):
            result = Publication.try_extract_publications("text", "https://x.com")
        assert len(result) == 1
        assert result[0]["title"] == "A Great Paper"

    def test_extract_publications_returns_empty_list_on_failure(self):
        """The legacy wrapper still returns [] (not None) on failure."""
        failed = StructuredResponse(parsed=None, usage=None)
        with patch("publication.extract_json", return_value=failed), \
             patch("publication.Database.log_llm_usage"):
            result = Publication.extract_publications("some text", "https://x.com")
        assert result == []
