"""Diff title changes whose new title already belongs to another paper.

Prod (27 Nov 2026) raised `ValueError: Title rename collides with another
paper` here on ~40 pages an hour, after the paid LLM call; the page was never
marked extracted, so every retry paid for the same answer and failed again.
Now a collision merges the two rows when they are one work (same authors) and
otherwise keeps both, and the page completes either way.

Runs the real diff path (`_extract_via_diff`, only the LLM call patched) and
`merge_paper_group` against MySQL when TEST_MYSQL_HOST is set (CI starts a
mysql:8.0.40 service on 3307); each test creates and drops a database. Locally:

    docker run -d --rm --name econ-sql-test -e MYSQL_ROOT_PASSWORD=test \\
        -p 3307:3306 mysql:8.0.40
    TEST_MYSQL_HOST=127.0.0.1 TEST_MYSQL_PORT=3307 poetry run pytest tests/test_title_rename_collision_mysql.py
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.pipeline.publication import ExtractionLLMResult

HOST = os.environ.get("TEST_MYSQL_HOST")
PAGE = "https://sites.google.com/site/kateorkin/cv"
OTHER_PAGE = "https://emmaalriley.wordpress.com/research/"


def _server_params() -> dict:
    return {
        "host": HOST,
        "port": int(os.environ.get("TEST_MYSQL_PORT", "3306")),
        "user": os.environ.get("TEST_MYSQL_USER", "root"),
        "password": os.environ.get("TEST_MYSQL_PASSWORD", "test"),
    }


@pytest.fixture
def db(monkeypatch):
    """A throwaway database with the full schema; the app's pool points at it."""
    if not HOST:
        pytest.skip("TEST_MYSQL_HOST not set")
    import mysql.connector
    from mysql.connector.pooling import MySQLConnectionPool
    import backend.database.connection as connection
    from backend.database.schema import create_tables

    admin = None
    for _ in range(30):  # the service may still be starting
        try:
            admin = mysql.connector.connect(**_server_params())
            break
        except mysql.connector.Error:
            time.sleep(2)
    if admin is None:
        pytest.fail("could not reach TEST_MYSQL_HOST")
    name = f"rename_{uuid.uuid4().hex[:8]}"
    cur = admin.cursor()
    cur.execute(f"CREATE DATABASE {name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    pool = MySQLConnectionPool(
        pool_name=f"rn_{uuid.uuid4().hex[:8]}", pool_size=5, database=name,
        charset="utf8mb4", collation="utf8mb4_unicode_ci", **_server_params(),
    )
    monkeypatch.setattr(connection, "_pool", pool)
    create_tables()
    try:
        yield
    finally:
        try:
            pool._remove_connections()
        except Exception:
            pass
        cur.execute(f"DROP DATABASE {name}")
        admin.close()


def _q(sql, params=()):
    from backend.database import fetch_all
    return fetch_all(sql, params)


def _exec(sql, params=()) -> int:
    from backend.database import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        last = cur.lastrowid
        cur.close()
        return last


def _researcher(first, last) -> int:
    return _exec("INSERT INTO researchers (first_name, last_name) VALUES (%s, %s)", (first, last))


def _paper(title, source_url, author_ids, status="working_paper", discovered="2026-01-01") -> int:
    from backend.database.papers import compute_title_hash
    pid = _exec(
        "INSERT INTO papers (title, title_hash, source_url, status, discovered_at) VALUES (%s, %s, %s, %s, %s)",
        (title, compute_title_hash(title), source_url, status, discovered),
    )
    for order, rid in enumerate(author_ids, 1):
        _exec("INSERT INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, %s)",
              (rid, pid, order))
    _baseline(source_url, author_ids[0])
    _exec("INSERT INTO feed_events (paper_id, event_type, created_at) VALUES (%s, 'new_paper', %s)",
          (pid, discovered))
    return pid


def _baseline(url, researcher_id) -> int:
    """The page and the two snapshots the new_paper trigger requires."""
    rows = _q("SELECT id FROM researcher_urls WHERE url = %s", (url,))
    if rows:
        return rows[0]["id"]
    url_id = _exec("INSERT INTO researcher_urls (researcher_id, page_type, url) VALUES (%s, 'PUB', %s)",
                   (researcher_id, url))
    for n in (1, 2):
        _exec("INSERT INTO html_snapshots (url_id, text_content_hash, raw_html_hash, raw_html_compressed, "
              "snapshot_at) VALUES (%s, %s, %s, %s, %s)",
              (url_id, f"t{n}", f"r{n}", b"x", datetime.now(timezone.utc)))
    return url_id


def _page(owner_id) -> int:
    url_id = _baseline(PAGE, owner_id)
    _exec("INSERT INTO html_content (url_id, content_hash, extracted_hash, timestamp) VALUES (%s, 'new', 'old', %s)",
          (url_id, datetime.now(timezone.utc)))
    return url_id


def _title_change(old_title, new_title):
    return {
        "change_type": "title_change", "title": new_title, "old_title": old_title,
        "authors": [], "year": None, "venue": None, "status": "working_paper",
        "draft_url": None, "abstract": None, "old_status": None,
    }


def _run_diff(url_id, changes):
    from backend.pipeline.extraction import _extract_via_diff
    with patch("backend.pipeline.extraction.Publication.try_extract_changes",
               return_value=ExtractionLLMResult(pubs=changes)):
        return _extract_via_diff(url_id, PAGE, "new text", "old text", "new", None)


def _events():
    return _q("SELECT paper_id, event_type FROM feed_events ORDER BY id")


def _extracted_hash(url_id):
    return _q("SELECT extracted_hash FROM html_content WHERE url_id = %s", (url_id,))[0]["extracted_hash"]


def test_same_work_collision_merges_without_events(db):
    """Orkin's CV renamed the paper to the title Riley's page already uses."""
    mahmud, riley, orkin = (_researcher("Mahreen", "Mahmud"), _researcher("Emma", "Riley"),
                            _researcher("Kate", "Orkin"))
    old_title = "Cash Transfers and Role Models: Impacts on Women's Empowerment in Rural Kenya"
    new_title = "Empowering Women at a Pivotal Moment: Evidence from a Cash Transfer and Role Model Workshop in Kenya"
    old_id = _paper(old_title, PAGE, [mahmud, riley, orkin], discovered="2025-03-01")
    other_id = _paper(new_title, OTHER_PAGE, [riley, mahmud, orkin], discovered="2026-02-01")
    url_id = _page(orkin)
    events_before = _events()

    outcome = _run_diff(url_id, [_title_change(old_title, new_title)])

    assert outcome.ok
    assert _extracted_hash(url_id) == "new"
    papers = _q("SELECT id, title FROM papers")
    assert papers == [{"id": old_id, "title": new_title}]  # older row kept, page's title
    assert {r["researcher_id"] for r in _q("SELECT researcher_id FROM authorship")} == {mahmud, riley, orkin}
    assert {r["url"] for r in _q("SELECT url FROM paper_urls WHERE paper_id = %s", (old_id,))} == {PAGE, OTHER_PAGE}
    # No title_change; the duplicate new_paper event is folded into the earlier one.
    assert [e["event_type"] for e in _events()] == ["new_paper"]
    assert {e["paper_id"] for e in _events()} == {old_id}
    assert len(events_before) == 2 and other_id != old_id


def test_different_work_collision_keeps_both_and_page_completes(db, caplog):
    """FedSpeak Matters (Handlan, Acevedo) is not FedSpeak Indicators (Handlan, Canilang, Michaud)."""
    handlan, acevedo = _researcher("Amy", "Handlan"), _researcher("Laura", "Acevedo")
    canilang, michaud = _researcher("Sara", "Canilang"), _researcher("Amanda", "Michaud")
    matters = _paper("FedSpeak Matters: Statement Similarity and Monetary Policy Expectations",
                     PAGE, [handlan, acevedo])
    indicators = _paper("FedSpeak Indicators", "https://ammichau.github.io/res.html",
                        [canilang, handlan, michaud])
    renamed = _paper("Monetary Policy Communication and Household Expectations", PAGE, [handlan])
    url_id = _page(handlan)

    with caplog.at_level("WARNING"):
        outcome = _run_diff(url_id, [
            _title_change("FedSpeak Matters: Statement Similarity and Monetary Policy Expectations",
                          "FedSpeak Indicators"),
            # A clean rename on the same page is still applied.
            _title_change("Monetary Policy Communication and Household Expectations",
                          "Central Bank Communication and Household Inflation Expectations"),
        ])

    assert outcome.ok and outcome.pubs_count == 2
    assert _extracted_hash(url_id) == "new"
    titles = {r["id"]: r["title"] for r in _q("SELECT id, title FROM papers")}
    assert titles[matters] == "FedSpeak Matters: Statement Similarity and Monetary Policy Expectations"
    assert titles[indicators] == "FedSpeak Indicators"
    assert titles[renamed] == "Central Bank Communication and Household Inflation Expectations"
    assert [(e["paper_id"], e["event_type"]) for e in _events() if e["event_type"] != "new_paper"] == [
        (renamed, "title_change"),
    ]
    skipped = [r for r in caplog.records if "Skipped title rename" in r.getMessage()]
    assert len(skipped) == 1
    assert f"paper {matters}" in skipped[0].getMessage() and f"paper {indicators}" in skipped[0].getMessage()
    assert not [r for r in caplog.records if "Refused incompatible" in r.getMessage()
                and r.levelname == "WARNING"]


def test_subset_authors_without_shared_identifier_keep_both(db):
    """Degrees for GPTs: two authors vs those two plus a third is not proof of one work."""
    smith, dodini, xu = _researcher("Tucker", "Smith"), _researcher("Samuel", "Dodini"), _researcher("Yaling", "Xu")
    old_id = _paper("Degrees for GPTs: The Effects of Generative AI on Skills and Schooling", PAGE, [smith, dodini])
    other_id = _paper("Degrees for GPTs: The Effects of Generative AI on Skill Demand and Supply",
                      "https://samueldodini.com", [dodini, smith, xu])
    url_id = _page(smith)

    outcome = _run_diff(url_id, [_title_change(
        "Degrees for GPTs: The Effects of Generative AI on Skills and Schooling",
        "Degrees for GPTs: The Effects of Generative AI on Skill Demand and Supply",
    )])

    assert outcome.ok
    assert _extracted_hash(url_id) == "new"
    assert {r["id"] for r in _q("SELECT id FROM papers")} == {old_id, other_id}
    assert [e["event_type"] for e in _events()] == ["new_paper", "new_paper"]
