"""Tests for validate_title_change — filters spurious LLM title change artifacts."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")


class TestBracketPrefixStripping:
    """Reject title changes that only differ by a bracket prefix (fe_id 3930 pattern)."""

    def test_rejects_replication_data_prefix_removal(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "[replication data] App-based experiments",
            "App-based experiments",
        ) is False

    def test_rejects_bracket_prefix_addition(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "App-based experiments",
            "[replication data] App-based experiments",
        ) is False

    def test_rejects_generic_bracket_prefix_removal(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "[Dataset] Trade and Growth",
            "Trade and Growth",
        ) is False

    def test_accepts_real_title_change_with_brackets(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "[replication data] App-based experiments",
            "Mobile experiments in behavioral economics",
        ) is True


class TestSubtitleHallucination:
    """Reject title changes that only add or remove a subtitle (fe_id 3598 pattern)."""

    def test_rejects_added_subtitle(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Eviction and Poverty in American Cities",
            "Eviction and Poverty in American Cities: Evidence from Chicago and New York",
        ) is False

    def test_rejects_removed_subtitle(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Eviction and Poverty in American Cities: Evidence from Chicago and New York",
            "Eviction and Poverty in American Cities",
        ) is False

    def test_accepts_different_subtitle(self):
        """If the main title is the same but subtitle changed substantially, accept."""
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Trade Policy: A View from the Midwest",
            "Trade Policy: Evidence from Developing Nations",
        ) is True


class TestDroppedOrAddedWord:
    """Reject changes that add/remove a single word (fe_id 4190 pattern)."""

    def test_rejects_dropped_first_word(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Development Research at High Geographic Resolution",
            "Research at High Geographic Resolution",
        ) is False

    def test_rejects_added_first_word(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Research at High Geographic Resolution",
            "Development Research at High Geographic Resolution",
        ) is False

    def test_accepts_multiple_word_changes(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Research at High Geographic Resolution",
            "Analysis of Low Geographic Precision",
        ) is True


class TestHyphenationVariants:
    """Reject changes that only differ by hyphenation (fe_id 3599 pattern)."""

    def test_rejects_hyphen_vs_no_hyphen(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Non-payment and Eviction in the Rental Housing Market",
            "Nonpayment and Eviction in the Rental Housing Market",
        ) is False

    def test_rejects_hyphen_addition(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Nonpayment and Eviction",
            "Non-payment and Eviction",
        ) is False


class TestIdenticalAfterNormalization:
    """Reject changes where titles are identical after standard normalization."""

    def test_rejects_case_only_change(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "the impact of trade policy",
            "The Impact of Trade Policy",
        ) is False

    def test_rejects_punctuation_only_change(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Trade, Growth, and Poverty",
            "Trade Growth and Poverty",
        ) is False


class TestGenuineTitleChanges:
    """Verify that real title changes pass validation."""

    def test_accepts_substantial_rewrite(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Essays on International Trade",
            "How Tariffs Shape Global Supply Chains",
        ) is True

    def test_accepts_meaningful_word_substitution(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "The Effect of Immigration on Native Wages",
            "The Impact of Immigration on Native Employment",
        ) is True

    def test_rejects_identical_titles(self):
        from backend.pipeline.paper_saver import validate_title_change
        assert validate_title_change(
            "Some Paper Title",
            "Some Paper Title",
        ) is False
