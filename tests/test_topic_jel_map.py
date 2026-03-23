# tests/test_topic_jel_map.py
"""Tests for OpenAlex topic → JEL code mapping."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from topic_jel_map import map_topic_to_jel, map_topics_to_jel


class TestMapTopicToJel:
    def test_labor_topic_maps_to_j(self):
        assert "J" in map_topic_to_jel("Labor market dynamics and wage inequality")

    def test_monetary_policy_maps_to_e(self):
        assert "E" in map_topic_to_jel("Monetary Policy and Economic Impact")

    def test_trade_maps_to_f(self):
        assert "F" in map_topic_to_jel("Global trade and economics")

    def test_migration_maps_to_j_and_f(self):
        codes = map_topic_to_jel("Migration and Labor Dynamics")
        assert "J" in codes
        assert "F" in codes

    def test_financial_maps_to_g(self):
        assert "G" in map_topic_to_jel("Financial market volatility and risk")

    def test_education_maps_to_i(self):
        assert "I" in map_topic_to_jel("Education policy and outcomes")

    def test_environmental_maps_to_q(self):
        assert "Q" in map_topic_to_jel("Environmental regulation and climate change")

    def test_urban_maps_to_r(self):
        assert "R" in map_topic_to_jel("Urban development and housing markets")

    def test_unrelated_topic_returns_empty(self):
        assert map_topic_to_jel("Quantum computing advances") == []

    def test_case_insensitive(self):
        assert "J" in map_topic_to_jel("LABOR MARKET DYNAMICS")


class TestMapTopicsToJel:
    def test_aggregates_multiple_topics(self):
        topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "Migration and policy", "score": 0.95},
            {"topic_name": "International trade flows", "score": 0.80},
        ]
        result = map_topics_to_jel(topics)
        assert "J" in result
        assert "F" in result

    def test_higher_score_wins(self):
        topics = [
            {"topic_name": "Labor market", "score": 0.60},
            {"topic_name": "Wage inequality", "score": 0.95},
        ]
        result = map_topics_to_jel(topics)
        assert result["J"] == 0.95

    def test_empty_topics_returns_empty(self):
        assert map_topics_to_jel([]) == {}

    def test_handles_display_name_key(self):
        """OpenAlex raw response uses 'display_name', stored data uses 'topic_name'."""
        topics = [{"display_name": "Monetary Policy", "score": 0.9}]
        result = map_topics_to_jel(topics)
        assert "E" in result
