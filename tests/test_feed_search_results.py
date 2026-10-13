"""Exercise feed search against stored rows, including non-adjacent terms.

SQLite runs the shared SELECTs with only placeholder/escape syntax adapted;
production MySQL is separately checked read-only before deployment.
"""
import sqlite3

import pytest

from backend.database import papers


@pytest.fixture
def search_rows(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.create_function("CONCAT", -1, lambda *parts: "".join(parts))
    conn.executescript("""
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
        CREATE TABLE researchers (id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT);
        CREATE TABLE authorship (publication_id INTEGER, researcher_id INTEGER);
        INSERT INTO researchers VALUES (1, 'Jane', 'Smith');
    """)
    fixtures = [
        (1, 'Healthcare workers and life satisfaction during the pandemic', None),
        (2, 'Healthcare spending and economic growth', None),
        (3, 'Factory workers and job satisfaction', None),
        (4, "Limits on Banks' FX Forward Positions", 'Hedging prices and real effects'),
        (5, 'Monetary transmission', 'Healthcare workers and life satisfaction'),
        (6, 'A 100% gain in sector_A', None),
        (7, 'A 1000 gain in sectorBA', None),
    ]
    conn.executemany('INSERT INTO papers (id, title, abstract) VALUES (?, ?, ?)', fixtures)
    for paper_id, _, _ in fixtures:
        conn.execute("INSERT INTO feed_events (id, paper_id, event_type) VALUES (?, ?, 'new_paper')", (paper_id, paper_id))
        conn.execute('INSERT INTO authorship VALUES (?, 1)', (paper_id,))

    def execute(sql, params):
        # MySQL string literals escape backslashes; SQLite leaves them literal.
        return conn.execute(sql.replace('%s', '?').replace("ESCAPE '\\\\'", "ESCAPE '\\'"), params)

    monkeypatch.setattr(papers, 'fetch_one', lambda sql, params: dict(execute(sql, params).fetchone()))
    monkeypatch.setattr(papers, 'fetch_all', lambda sql, params: [dict(row) for row in execute(sql, params).fetchall()])
    yield papers.search_feed_events
    conn.close()


@pytest.mark.parametrize(('query', 'expected'), [
    ('Healthcare workers and life satisfaction', {1, 5}),
    ('Healthcare satisfaction', {1, 5}),
    ('  Healthcare   satisfaction  ', {1, 5}),
    ('+Healthcare -satisfaction', {1, 5}),
    ('FX banks', {4}),
    ('the pandemic', {1}),
    ('100%', {6}),
    ('sector_A', {6}),
    ('Jane Smith', {1, 2, 3, 4, 5, 6, 7}),
    ('nonexistent phrase', set()),
    ('***', set()),
])
def test_returns_only_matching_papers(search_rows, query, expected):
    rows, total, researcher_count = search_rows(search=query)
    assert {row['paper_id'] for row in rows} == expected
    assert total == len(expected)
    assert researcher_count == int(bool(expected))


def test_pagination_keeps_filtered_total(search_rows):
    rows, total, _ = search_rows(search='Healthcare satisfaction', limit=1, offset=1)
    assert len(rows) == 1
    assert rows[0]['paper_id'] in {1, 5}
    assert total == 2
