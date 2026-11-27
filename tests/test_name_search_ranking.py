"""A name query lists that person first on every search surface.

Regression for "Thomas Rieger": the researcher directory matched ANY query
word (every Thomas) and sorted alphabetically, so he was buried among ~400
Thomases; "Rieger" listed Brieger/Krieger before Rieger.

SQLite runs the shared SELECTs with only placeholder/escape syntax adapted
(as in test_feed_search_results.py). Case-insensitivity is exercised here;
accent-insensitivity comes from MySQL's utf8mb4_unicode_ci collation, which
SQLite does not emulate.
"""
import re
import sqlite3

import pytest

from backend.database import papers, researchers
from backend.database.search_helpers import name_relevance_sql, name_search_words

# id, first, last
PEOPLE = [
    (1, "Thomas", "Addoah"),
    (2, "Thomas", "Andersen"),
    (3, "Thomas", "Rieger"),
    (4, "Matthias", "Rieger"),
    (5, "Bastian", "Krieger"),
    (6, "Steven", "Brieger"),
    (7, "Thomas", "Riegert"),
    (8, "Jane", "Smith"),
]


def _connect():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.create_function("CONCAT", -1, lambda *parts: "".join(parts))
    conn.create_function("CHAR_LENGTH", 1, lambda s: len(s) if s is not None else None)
    conn.create_function(
        "REGEXP", 2, lambda pattern, value: value is not None and re.search(pattern, value) is not None
    )
    conn.executescript("""
        CREATE TABLE researchers (
            id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, position TEXT,
            affiliation TEXT, description TEXT, openalex_author_id TEXT,
            has_top5_pub INTEGER DEFAULT 0
        );
        CREATE TABLE researcher_urls (researcher_id INTEGER);
        CREATE TABLE authorship (publication_id INTEGER, researcher_id INTEGER);
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY, title TEXT, abstract TEXT, year TEXT,
            venue TEXT, source_url TEXT, discovered_at TEXT, status TEXT,
            draft_url TEXT, draft_url_status TEXT, doi TEXT
        );
        CREATE TABLE feed_events (
            id INTEGER PRIMARY KEY, paper_id INTEGER, event_type TEXT,
            old_status TEXT, new_status TEXT, old_title TEXT, new_title TEXT,
            created_at TEXT, event_metadata_captured INTEGER, event_venue TEXT,
            event_source_url TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO researchers (id, first_name, last_name, openalex_author_id) VALUES (?, ?, ?, 'A')",
        PEOPLE,
    )
    return conn


def _install(monkeypatch, module, conn):
    def execute(sql, params):
        # MySQL string literals escape backslashes; SQLite leaves them literal.
        sql = sql.replace("%s", "?").replace("ESCAPE '\\\\'", "ESCAPE '\\'")
        return conn.execute(sql, params)

    monkeypatch.setattr(module, "fetch_one", lambda sql, params=(): dict(execute(sql, params).fetchone()))
    monkeypatch.setattr(module, "fetch_all", lambda sql, params=(): [dict(r) for r in execute(sql, params).fetchall()])


@pytest.fixture
def directory(monkeypatch):
    conn = _connect()
    # Everyone authored something, so all pass the directory's base guards.
    conn.executemany("INSERT INTO authorship VALUES (?, ?)", [(100 + rid, rid) for rid, _, _ in PEOPLE])
    _install(monkeypatch, researchers, conn)
    yield conn
    conn.close()


@pytest.fixture
def feed(monkeypatch):
    conn = _connect()
    # paper id, title, created_at, author ids  (newest first)
    fixtures = [
        (1, "Fertility decline and preventive care", "2026-11-05", [5]),       # Krieger
        (2, "Rieger-type estimators revisited", "2026-11-04", [8]),            # title word only
        (3, "Trade shocks and voting", "2026-11-03", [7]),                     # Thomas Riegert
        (4, "Wealth taxes and migration", "2026-11-02", [3, 8]),               # Thomas Rieger
        (5, "Housing and household debt", "2026-11-01", [1]),                  # Thomas Addoah
    ]
    for paper_id, title, created_at, authors in fixtures:
        conn.execute("INSERT INTO papers (id, title) VALUES (?, ?)", (paper_id, title))
        conn.execute(
            "INSERT INTO feed_events (id, paper_id, event_type, created_at) VALUES (?, ?, 'new_paper', ?)",
            (paper_id, paper_id, created_at),
        )
        conn.executemany("INSERT INTO authorship VALUES (?, ?)", [(paper_id, a) for a in authors])
    _install(monkeypatch, papers, conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Researcher directory (/api/researchers?search=)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", ["Thomas Rieger", "thomas rieger", "Rieger Thomas", "Rieger, Thomas", "  THOMAS   RIEGER "])
def test_directory_lists_exact_name_first_and_drops_partial_word_matches(directory, query):
    rows, total = researchers.search_researchers(search=query)
    assert [r["id"] for r in rows] == [3, 7]  # Thomas Rieger, then prefix "Riegert"
    assert total == 2  # other Thomases no longer match on "Thomas" alone


def test_directory_last_name_query_puts_whole_word_matches_before_substrings(directory):
    rows, total = researchers.search_researchers(search="Rieger")
    # Rieger (whole word, alphabetical) > Riegert (prefix) > Brieger/Krieger (inside a word)
    assert [r["id"] for r in rows] == [4, 3, 7, 6, 5]
    assert total == 5


def test_directory_ranking_survives_pagination(directory):
    first, total = researchers.search_researchers(search="Rieger", limit=2, offset=0)
    second, _ = researchers.search_researchers(search="Rieger", limit=2, offset=2)
    assert [r["id"] for r in first] == [4, 3]
    assert [r["id"] for r in second] == [7, 6]
    assert total == 5


def test_directory_without_search_keeps_alphabetical_order(directory):
    rows, total = researchers.search_researchers()
    assert [r["id"] for r in rows] == [1, 2, 6, 5, 4, 3, 7, 8]
    assert total == len(PEOPLE)


def test_directory_search_treats_wildcards_literally(directory):
    rows, total = researchers.search_researchers(search="Rie%")
    assert rows == [] and total == 0


# ---------------------------------------------------------------------------
# Researcher chooser (/api/researcher-options?q=)
# ---------------------------------------------------------------------------

def test_options_put_whole_last_name_before_substring_matches(directory):
    rows = researchers.search_researcher_options(search="rieger")
    assert [r["id"] for r in rows] == [4, 3, 7, 6, 5]


@pytest.mark.parametrize("query", ["Thomas Rieger", "rieger thomas"])
def test_options_exact_name_first(directory, query):
    rows = researchers.search_researcher_options(search=query)
    assert [r["id"] for r in rows] == [3, 7]


# ---------------------------------------------------------------------------
# Publication feed (/api/publications?search=)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", ["Thomas Rieger", "thomas rieger", "Rieger Thomas"])
def test_feed_full_name_lists_that_authors_papers_first(feed, query):
    rows, total, researcher_count = papers.search_feed_events(search=query)
    ids = [r["paper_id"] for r in rows]
    assert ids[0] == 4           # authored by Thomas Rieger
    assert set(ids) == {4, 3}    # Riegert still matches every word, ranked after
    assert total == 2
    assert researcher_count == 3  # Rieger, Smith (coauthor of 4), Riegert


def test_feed_last_name_boosts_whole_word_author_then_keeps_chronology(feed):
    rows, total, _ = papers.search_feed_events(search="Rieger")
    # Rieger (whole word) > Riegert (prefix) > newest-first for Krieger/title matches
    assert [r["paper_id"] for r in rows] == [4, 3, 1, 2]
    assert total == 4


def test_feed_topic_search_stays_chronological(feed):
    rows, _, _ = papers.search_feed_events(search="and")
    assert [r["paper_id"] for r in rows] == [1, 3, 4, 5]


def test_feed_without_search_is_chronological(feed):
    rows, total, _ = papers.search_feed_events()
    assert [r["paper_id"] for r in rows] == [1, 2, 3, 4, 5]
    assert total == 5


# ---------------------------------------------------------------------------
# Helper contract
# ---------------------------------------------------------------------------

def test_name_search_words_splits_commas_and_normalizes_unicode():
    decomposed = "Müller, Jörg"  # NFD input from some keyboards
    assert name_search_words(decomposed) == ["Müller", "Jörg"]
    assert name_search_words("   ") == []
    assert name_search_words(None) == []


def test_name_relevance_sql_placeholders_match_params():
    for include_substring in (True, False):
        sql, params = name_relevance_sql("r.first_name", "r.last_name", "Thomas 50%_Rieger", include_substring=include_substring)
        assert sql.count("%s") == len(params)
        assert "Thomas 50\\%\\_Rieger" in params
    assert name_relevance_sql("f", "l", "  ") == ("", [])
