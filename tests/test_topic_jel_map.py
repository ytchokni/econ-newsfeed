# tests/test_topic_jel_map.py
"""Tests for OpenAlex topic → JEL code mapping."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from topic_jel_map import map_topic_to_jel


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


class TestPaperTopicsSchema:
    def test_table_definition_exists(self):
        from database.schema import _TABLE_DEFINITIONS
        assert "paper_topics" in _TABLE_DEFINITIONS
        ddl = _TABLE_DEFINITIONS["paper_topics"]
        assert "paper_id" in ddl
        assert "openalex_topic_id" in ddl
        assert "topic_name" in ddl
        assert "score" in ddl
        assert "FOREIGN KEY" in ddl
