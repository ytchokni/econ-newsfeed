"""Tests for verify_title_in_html — catches LLM-fabricated titles not in source HTML."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")


class TestExactTitleMatch:
    """Title appears verbatim in the HTML text."""

    def test_exact_match_returns_true(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "Working Papers\nTrade and Growth in Africa\nJohn Smith, 2024"
        assert verify_title_in_html("Trade and Growth in Africa", html) is True

    def test_case_insensitive_match(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "TRADE AND GROWTH IN AFRICA"
        assert verify_title_in_html("Trade and Growth in Africa", html) is True

    def test_punctuation_insensitive_match(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "Trade, Growth & Poverty: A Study"
        assert verify_title_in_html("Trade Growth and Poverty A Study", html) is True


class TestFabricatedTitles:
    """Titles the LLM invented from parametric knowledge (fe_id 3137, 3353 patterns)."""

    def test_rejects_paraphrased_title(self):
        """fe_id 3137: LLM produced 'How Regulation-Driven Financial Flows Impact...'
        but the HTML says 'Financial Regulation, Pension Investment, and Economic Growth'."""
        from backend.pipeline.publication import verify_title_in_html
        html = """Research
        Financial Regulation, Pension Investment, and Economic Growth
        This paper examines how regulation-driven financial flows impact asset prices."""
        assert verify_title_in_html(
            "How Regulation-Driven Financial Flows Impact Asset Prices and Economic Growth",
            html,
        ) is False

    def test_rejects_augmented_title_with_arxiv_subtitle(self):
        """fe_id 3353: HTML says 'Stable matching as transportation' but LLM added subtitle."""
        from backend.pipeline.publication import verify_title_in_html
        html = "Working Papers\nStable matching as transportation\nFederico Echenique"
        assert verify_title_in_html(
            "Stable Matching as Transport: A Welfarist Perspective on Market Design",
            html,
        ) is False

    def test_accepts_title_present_in_html(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "Publications\nStable Matching as Transport: A Welfarist Perspective\nJournal of Econ"
        assert verify_title_in_html(
            "Stable Matching as Transport: A Welfarist Perspective",
            html,
        ) is True


class TestPartialTokenMatch:
    """Handles minor word-form differences (e.g., & vs and) while still catching fabrication."""

    def test_accepts_ampersand_vs_and(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "Trade & Growth in East Africa — AER 2024"
        assert verify_title_in_html("Trade and Growth in East Africa", html) is True

    def test_accepts_quotes_stripped(self):
        from backend.pipeline.publication import verify_title_in_html
        html = '"How Assignments Shape Careers" — working paper'
        assert verify_title_in_html("How Assignments Shape Careers", html) is True

    def test_rejects_low_overlap_title(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "The Impact of Trade Policy on Developing Nations\nJohn Doe, 2025"
        assert verify_title_in_html(
            "Monetary Policy Transmission in Advanced Economies",
            html,
        ) is False


class TestShortTitles:
    """Short titles need stricter matching to avoid false positives."""

    def test_accepts_short_title_in_html(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "Publications\nQuo Vadis?\nJournal of Banking"
        assert verify_title_in_html("Quo Vadis?", html) is True

    def test_rejects_short_title_not_in_html(self):
        from backend.pipeline.publication import verify_title_in_html
        html = "Publications\nLong Paper About Something Else\nVadis Industries"
        assert verify_title_in_html("Quo Vadis?", html) is False
