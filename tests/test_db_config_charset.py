"""Verify db_config includes charset for UTF-8 MySQL connections."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")

from db_config import db_config


class TestDbConfigCharset:
    def test_charset_is_utf8mb4(self):
        assert db_config.get("charset") == "utf8mb4"

    def test_collation_is_unicode_ci(self):
        assert db_config.get("collation") == "utf8mb4_unicode_ci"
