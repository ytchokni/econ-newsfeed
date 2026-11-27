"""HTMLFetcher.save_text's upsert against a real MySQL server.

Every other test mocks this SQL, and that hid an ERROR 1052 (ambiguous
column with the `AS new_row` alias) that would have made every save fail.
Runs when TEST_MYSQL_HOST is set (CI starts a mysql:8.0.40 service);
locally:

    docker run -d --rm --name econ-sql-test -e MYSQL_ROOT_PASSWORD=test \\
        -p 3307:3306 mysql:8.0.40
    TEST_MYSQL_HOST=127.0.0.1 TEST_MYSQL_PORT=3307 poetry run pytest tests/test_save_text_mysql.py
"""
import os
import re
import time
import uuid
from unittest.mock import patch

import pytest

from backend.database.schema import _TABLE_DEFINITIONS
from backend.pipeline.html_fetcher import HTMLFetcher

HOST = os.environ.get("TEST_MYSQL_HOST")


def _html_content_ddl() -> str:
    """The production html_content table, without its foreign keys."""
    ddl = _TABLE_DEFINITIONS["html_content"]
    ddl = re.sub(r",\s*FOREIGN KEY[^,]*?ON DELETE CASCADE", "", ddl)
    return ddl


def test_upsert_names_html_content_columns_explicitly():
    """Always runs: the IF must not use bare column names next to new_row."""
    captured = []
    with patch("backend.pipeline.html_fetcher.execute_query", side_effect=lambda q, p: captured.append(q)), \
         patch.object(HTMLFetcher, "archive_snapshot"):
        HTMLFetcher.save_text(1, "text", "h", 2)
    sql = captured[0]
    assert "html_content.content_hash <=> new_row.content_hash" in sql
    assert re.search(r"IF\(\s*content_hash", sql) is None


@pytest.fixture
def mysql_conn():
    if not HOST:
        pytest.skip("TEST_MYSQL_HOST not set")
    import mysql.connector

    conn = None
    for _ in range(30):  # the service may still be starting
        try:
            conn = mysql.connector.connect(
                host=HOST, port=int(os.environ.get("TEST_MYSQL_PORT", "3306")),
                user=os.environ.get("TEST_MYSQL_USER", "root"),
                password=os.environ.get("TEST_MYSQL_PASSWORD", "test"),
            )
            break
        except mysql.connector.Error:
            time.sleep(2)
    if conn is None:
        pytest.fail("could not reach TEST_MYSQL_HOST")
    db = f"save_text_{uuid.uuid4().hex[:8]}"
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE {db}")
    cur.execute(f"USE {db}")
    cur.execute(_html_content_ddl())
    cur.execute("ALTER TABLE html_content ADD COLUMN raw_html MEDIUMTEXT")  # added by a migration
    conn.commit()
    try:
        yield conn
    finally:
        cur.execute(f"DROP DATABASE {db}")
        conn.close()


def test_save_text_upsert_tracks_content_changes(mysql_conn):
    def execute(query, params=None):
        cur = mysql_conn.cursor()
        cur.execute(query, params)
        mysql_conn.commit()
        cur.close()

    def row():
        cur = mysql_conn.cursor(dictionary=True)
        cur.execute("SELECT content, content_hash, content_changed_at FROM html_content WHERE url_id = 1")
        result = cur.fetchone()
        cur.close()
        return result

    with patch("backend.pipeline.html_fetcher.execute_query", side_effect=execute), \
         patch.object(HTMLFetcher, "archive_snapshot"):
        HTMLFetcher.save_text(1, "first", "h1", 2, raw_html="<p>first</p>")
        inserted = row()
        assert inserted["content_hash"] == "h1" and inserted["content_changed_at"] is not None

        execute("UPDATE html_content SET content_changed_at = '2020-01-01 00:00:00' WHERE url_id = 1")
        HTMLFetcher.save_text(1, "first again", "h1", 2)
        same = row()
        assert same["content"] == "first again"
        assert str(same["content_changed_at"]) == "2020-01-01 00:00:00"  # hash unchanged

        HTMLFetcher.save_text(1, "second", "h2", 2)
        changed = row()
        assert changed["content_hash"] == "h2"
        assert changed["content_changed_at"].year >= 2026  # hash changed: set to now
