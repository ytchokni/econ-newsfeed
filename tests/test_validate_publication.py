"""Tests for validate_publication — catches garbage LLM extractions."""
import pytest
from publication import validate_publication


class TestAuthorTitleOverlap:
    """Skip papers where 2+ author last names appear as words in the title."""

    def test_rejects_title_words_as_author_names(self):
        """'A Theory of Disappointment Aversion' with authors Theory, Disappointment, A."""
        pub = {
            "title": "A Theory of Disappointment Aversion",
            "authors": [["A.", "Theory"], ["o.", "Disappointment"], ["A.", ""]],
        }
        assert validate_publication(pub) is False

    def test_accepts_legitimate_paper(self):
        pub = {
            "title": "Trade and Wages in a Global Economy",
            "authors": [["John", "Smith"], ["Jane", "Doe"]],
        }
        assert validate_publication(pub) is True

    def test_ignores_common_short_words(self):
        """Words like 'a', 'the', 'of', 'on', 'in' should not count as overlap."""
        pub = {
            "title": "A Study of Trade in Europe",
            "authors": [["Anna", "Tradeworth"], ["Oliver", "Europe"]],
        }
        assert validate_publication(pub) is True

    def test_rejects_when_two_last_names_match_title(self):
        pub = {
            "title": "Growth and Trade in Africa",
            "authors": [["A.", "Growth"], ["B.", "Trade"]],
        }
        assert validate_publication(pub) is False


class TestMinimumAuthorQuality:
    """Skip if all authors have last names shorter than 2 characters."""

    def test_rejects_all_single_char_last_names(self):
        pub = {
            "title": "Some Paper Title",
            "authors": [["X", "A"], ["Y", "B"]],
        }
        assert validate_publication(pub) is False

    def test_accepts_if_at_least_one_valid_last_name(self):
        pub = {
            "title": "Some Paper Title",
            "authors": [["X", "A"], ["John", "Smith"]],
        }
        assert validate_publication(pub) is True


class TestGitHubExclusion:
    """Skip papers with github.com draft URLs."""

    def test_rejects_github_com_draft_url(self):
        pub = {
            "title": "Econ-Newsfeed",
            "authors": [["Y.", "Tchokni"]],
            "draft_url": "https://github.com/user/repo",
        }
        assert validate_publication(pub) is False

    def test_allows_github_io_draft_url(self):
        pub = {
            "title": "My Research Paper",
            "authors": [["Y.", "Tchokni"]],
            "draft_url": "https://user.github.io/paper.pdf",
        }
        assert validate_publication(pub) is True

    def test_allows_no_draft_url(self):
        pub = {
            "title": "My Research Paper",
            "authors": [["Y.", "Tchokni"]],
        }
        assert validate_publication(pub) is True

    def test_rejects_software_title_indicators(self):
        pub = {
            "title": "Automatic Causal Inference Python package",
            "authors": [["Y.", "Tchokni"]],
        }
        assert validate_publication(pub) is False

    def test_allows_legitimate_paper_with_common_words(self):
        """Papers about software/libraries as economic topics should pass."""
        pub = {
            "title": "Labor Market Dynamics in Library Services",
            "authors": [["John", "Smith"]],
        }
        assert validate_publication(pub) is True


class TestEdgeCases:
    """Edge cases that should not crash."""

    def test_empty_authors_list(self):
        pub = {"title": "Some Title", "authors": []}
        assert validate_publication(pub) is True

    def test_missing_draft_url_key(self):
        pub = {"title": "Some Title", "authors": [["John", "Doe"]]}
        assert validate_publication(pub) is True


class TestWebsiteSnippetRejection:
    """Reject titles that are clearly website elements, not paper titles."""

    def test_rejects_very_short_title(self):
        pub = {"title": "CV", "authors": [["John", "Doe"]]}
        assert validate_publication(pub) is False

    def test_rejects_website_element_titles(self):
        for title in ["Email", "Follow", "Sitemap", "Feed", "Teaching", "Publications"]:
            pub = {"title": title, "authors": [["John", "Doe"]]}
            assert validate_publication(pub) is False, f"Should reject '{title}'"

    def test_rejects_no_publications_hallucination(self):
        pub = {"title": "No publications found in the provided page content", "authors": []}
        assert validate_publication(pub) is False

    def test_rejects_copyright_notice(self):
        pub = {"title": "© 2025 Jason Chen, Powered by Jekyll", "authors": [["Jason", "Chen"]]}
        assert validate_publication(pub) is False

    def test_rejects_bio_snippet(self):
        pub = {"title": "I will be on the job market in the 2025-26 academic year.", "authors": []}
        assert validate_publication(pub) is False

    def test_rejects_welcome_message(self):
        pub = {"title": "Welcome to my academic webpage.", "authors": []}
        assert validate_publication(pub) is False

    def test_rejects_github_venue(self):
        pub = {"title": "My Cool Project", "authors": [["J", "Doe"]], "venue": "GitHub"}
        assert validate_publication(pub) is False

    def test_accepts_short_but_real_title(self):
        """Real papers can have short titles like 'Voting' or 'Big G' if they have venue/status."""
        pub = {"title": "Voting", "authors": [["John", "Smith"]], "status": "published", "venue": "AER"}
        assert validate_publication(pub) is True

    def test_accepts_normal_paper(self):
        pub = {"title": "The Effect of Trade on Growth", "authors": [["J", "Smith"]]}
        assert validate_publication(pub) is True
