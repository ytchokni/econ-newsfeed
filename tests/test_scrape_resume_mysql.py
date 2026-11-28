"""The resumable fetch pass's SQL against a real MySQL server.

Runs when TEST_MYSQL_HOST is set (CI starts a mysql:8.0.40 service); locally:

    docker run -d --rm --name econ-sql-test -e MYSQL_ROOT_PASSWORD=test \\
        -p 3307:3306 mysql:8.0.40
    TEST_MYSQL_HOST=127.0.0.1 TEST_MYSQL_PORT=3307 poetry run pytest tests/test_scrape_resume_mysql.py
"""
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import backend.pipeline.scheduler as scheduler
from backend.database.schema import _TABLE_DEFINITIONS
from backend.researcher import Researcher

HOST = os.environ.get("TEST_MYSQL_HOST")


def _ddl(table: str) -> str:
    """A production table definition without its foreign keys."""
    return re.sub(r",\s*FOREIGN KEY[^,]*?ON DELETE CASCADE", "", _TABLE_DEFINITIONS[table])


@pytest.fixture
def db():
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
                autocommit=True,
            )
            break
        except mysql.connector.Error:
            time.sleep(2)
    if conn is None:
        pytest.fail("could not reach TEST_MYSQL_HOST")
    name = f"scrape_resume_{uuid.uuid4().hex[:8]}"
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE {name}")
    cur.execute(f"USE {name}")
    cur.execute("SET time_zone = '+00:00'")
    cur.execute(_ddl("researcher_urls"))
    cur.execute(_ddl("scrape_log"))
    cur.execute("ALTER TABLE scrape_log ADD COLUMN extraction_errors INT DEFAULT 0")  # a migration
    cur.execute(_ddl("llm_usage"))  # update_scrape_log sums this pass's LLM tokens
    cur.close()
    lock = threading.Lock()  # one connection shared by the pass's worker threads

    def execute_query(query, params=None):
        with lock:
            c = conn.cursor()
            c.execute(query, params)
            result = c.lastrowid if query.lstrip().upper().startswith("INSERT") else c.rowcount
            c.close()
            return result

    def fetch_all(query, params=None):
        with lock:
            c = conn.cursor(dictionary=True)
            c.execute(query, params)
            rows = c.fetchall()
            c.close()
            return rows

    def fetch_one(query, params=None):
        rows = fetch_all(query, params)
        return rows[0] if rows else None

    with patch.object(scheduler, "execute_query", side_effect=execute_query), \
         patch.object(scheduler, "fetch_one", side_effect=fetch_one), \
         patch("backend.researcher.fetch_all", side_effect=fetch_all):
        yield {"execute": execute_query, "one": fetch_one, "all": fetch_all}
    c = conn.cursor()
    c.execute(f"DROP DATABASE {name}")
    c.close()
    conn.close()


def _add_urls(db, n):
    for i in range(1, n + 1):
        db["execute"]("INSERT INTO researcher_urls (researcher_id, page_type, url) VALUES (%s, 'HOME', %s)",
                      (i, f"https://host{i}.example.org/"))


def test_interrupted_pass_resumes_where_it_stopped(db):
    _add_urls(db, 6)
    stop = threading.Event()
    fetched = []

    def fetch(url_id, url, researcher_id):
        fetched.append(url_id)
        if len(fetched) == 3:
            stop.set()
        return url_id == 2

    with patch.object(scheduler, "_acquire_db_lock", return_value=object()), \
         patch.object(scheduler, "_release_db_lock"), \
         patch.object(scheduler, "_validate_draft_urls"), \
         patch.object(scheduler, "SCRAPE_CONCURRENCY", 1), \
         patch.object(scheduler.HTMLFetcher, "fetch_and_save_if_changed", side_effect=fetch), \
         patch("backend.enrichment.paper_merge.merge_duplicate_papers"), \
         patch("backend.database.researchers.refresh_all_has_top5", return_value=0):
        assert scheduler.run_scrape_job(stop_event=stop) == "interrupted"
        row = db["one"]("SELECT * FROM scrape_log")
        assert row["status"] == "interrupted" and row["is_fetch_run"] == 1
        assert (row["urls_checked"], row["urls_changed"]) == (3, 1)
        assert db["one"]("SELECT COUNT(*) n FROM researcher_urls WHERE scrape_checked_at IS NOT NULL")["n"] == 3
        assert scheduler.scrape_due()[0] is True

        stop.clear()
        assert scheduler.run_scrape_job(stop_event=stop) == "completed"

    assert sorted(fetched) == [1, 2, 3, 4, 5, 6]  # nothing fetched twice
    rows = db["all"]("SELECT * FROM scrape_log")
    assert len(rows) == 1
    assert rows[0]["status"] == "completed" and rows[0]["resumed_count"] == 1
    assert (rows[0]["urls_checked"], rows[0]["urls_changed"]) == (6, 1)
    due, wait = scheduler.scrape_due()
    assert due is False and wait > 3600


def test_old_unfinished_pass_is_not_resumed(db):
    _add_urls(db, 2)
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=30)
    db["execute"]("INSERT INTO scrape_log (started_at, status, is_fetch_run) VALUES (%s, 'interrupted', TRUE)", (old,))
    db["execute"]("UPDATE researcher_urls SET scrape_checked_at = %s", (old + timedelta(hours=1),))
    run = scheduler._start_or_resume_run()
    assert run["resumed"] is False
    # A new pass started now: every URL is due again.
    assert len(Researcher.get_all_researcher_urls(checked_before=run["started_at"])) == 2


def test_status_enum_migration_from_old_definition(db):
    """create_tables' check-and-modify turns the old enum into one with 'interrupted'."""
    db["execute"]("ALTER TABLE scrape_log MODIFY COLUMN status "
                  "ENUM('running', 'completed', 'failed') DEFAULT 'running'")
    col = db["one"]("SELECT COLUMN_TYPE t FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = 'scrape_log' AND COLUMN_NAME = 'status'")
    assert "'interrupted'" not in col["t"]
    db["execute"]("ALTER TABLE scrape_log MODIFY COLUMN status "
                  "ENUM('running', 'completed', 'failed', 'interrupted') DEFAULT 'running'")
    db["execute"]("INSERT INTO scrape_log (started_at, status) VALUES (NOW(), 'interrupted')")
    assert db["one"]("SELECT status FROM scrape_log")["status"] == "interrupted"
