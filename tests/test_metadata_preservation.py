"""Exercise snapshot writes and frozen feed metadata against an in-memory SQL store."""
import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytest

from backend.database.snapshots import append_paper_snapshot
from backend.pipeline.feed_events import FeedEventEmitter
from backend.publication_rules import is_working_paper_series


class _Cursor:
    def __init__(self, conn, dictionary=False):
        self.cursor = conn.cursor()
        self.dictionary = dictionary

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def execute(self, sql, params=()):
        # SQLite runs the actual writes; only MySQL placeholders/row lock differ.
        sql = sql.replace('%s', '?').replace(' FOR UPDATE', '')
        self.cursor.execute(sql, tuple(p.isoformat() if isinstance(p, datetime) else p for p in params))

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None and self.dictionary else row

    def close(self):
        self.cursor.close()


class _Connection:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc, *_):
        if exc:
            self.conn.rollback()

    def cursor(self, dictionary=False, **_):
        return _Cursor(self.conn, dictionary)

    def commit(self):
        self.conn.commit()


@pytest.fixture
def metadata_db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE papers (id INTEGER PRIMARY KEY, status TEXT, venue TEXT, abstract TEXT,
            draft_url TEXT, draft_url_status TEXT, draft_url_checked_at TEXT, year TEXT, source_url TEXT);
        CREATE TABLE paper_snapshots (id INTEGER PRIMARY KEY, paper_id INTEGER, title TEXT,
            status TEXT, venue TEXT, abstract TEXT, draft_url TEXT, draft_url_status TEXT,
            year TEXT, scraped_at TEXT, source_url TEXT, content_hash TEXT);
        CREATE TABLE feed_events (id INTEGER PRIMARY KEY, paper_id INTEGER, event_type TEXT,
            old_status TEXT, new_status TEXT, old_title TEXT, new_title TEXT, created_at TEXT,
            event_venue TEXT, event_source_url TEXT, event_metadata_captured INTEGER DEFAULT 0);
        INSERT INTO papers VALUES (270069, 'revise_and_resubmit', 'The Review of Economic Studies',
            'Known abstract', 'https://author.test/paper.pdf', 'valid', '2026-10-01', '2025',
            'https://first-author.test');
    ''')
    with patch('backend.database.snapshots.get_connection', return_value=_Connection(db)), \
         patch('backend.pipeline.feed_events.get_connection', return_value=_Connection(db)):
        yield db
    db.close()


@pytest.mark.parametrize('venue', [None, 'NBER Working Paper 33618'])
def test_stale_coauthor_snapshot_preserves_canonical_metadata(metadata_db, venue):
    result = append_paper_snapshot(270069, 'working_paper', venue, None, None, None,
                                   source_url='https://coauthor.test')
    paper = dict(metadata_db.execute('SELECT * FROM papers').fetchone())
    assert paper['status'] == 'revise_and_resubmit'
    assert paper['venue'] == 'The Review of Economic Studies'
    assert paper['year'] == '2025'
    assert paper['abstract'] == 'Known abstract'
    assert paper['draft_url'] == 'https://author.test/paper.pdf'
    assert paper['draft_url_status'] == 'valid'
    assert paper['draft_url_checked_at'] == '2026-10-01'
    assert not result.status_changed
    raw = dict(metadata_db.execute('SELECT * FROM paper_snapshots').fetchone())
    assert (raw['status'], raw['venue'], raw['year']) == ('working_paper', venue, None)


def test_sparse_progression_retains_fields_and_new_link_resets_validation(metadata_db):
    result = append_paper_snapshot(270069, 'accepted', None, '', 'https://author.test/new.pdf', None)
    paper = dict(metadata_db.execute('SELECT * FROM papers').fetchone())
    assert result.status_changed
    assert result.venue == 'The Review of Economic Studies'
    assert (paper['year'], paper['abstract']) == ('2025', 'Known abstract')
    assert paper['draft_url'] == 'https://author.test/new.pdf'
    assert (paper['draft_url_status'], paper['draft_url_checked_at']) == ('unchecked', None)


def test_series_does_not_replace_known_journal(metadata_db):
    append_paper_snapshot(270069, 'revise_and_resubmit', 'NBER Working Paper 1', None, None, None)
    assert metadata_db.execute('SELECT venue FROM papers').fetchone()[0] == 'The Review of Economic Studies'


def test_progression_does_not_inherit_working_paper_series(metadata_db):
    metadata_db.execute("UPDATE papers SET status='working_paper', venue='NBER Working Paper 1'")
    result = append_paper_snapshot(270069, 'accepted', None, None, None, None)
    assert result.new_status == 'accepted'
    assert result.venue is None
    assert metadata_db.execute('SELECT venue FROM papers').fetchone()[0] is None


@pytest.mark.parametrize('venue', [None, 'Journal at event time'])
def test_event_venue_and_triggering_source_survive_later_paper_updates(metadata_db, venue):
    FeedEventEmitter.emit_status_change(270069, 'working_paper', 'accepted',
                                       venue=venue, source_url='https://trigger.test')
    metadata_db.execute("UPDATE papers SET venue='Later journal', source_url='https://later.test'")
    from backend.database.papers import get_paper_history

    def fetch_all(sql, params):
        return [dict(row) for row in metadata_db.execute(sql.replace('%s', '?'), params)]

    with patch('backend.database.papers.fetch_all', side_effect=fetch_all):
        event = get_paper_history(270069)[0]
    assert event['event_venue'] == venue
    assert event['event_source_url'] == 'https://trigger.test'


@pytest.mark.parametrize('venue,expected', [
    ('NBER Working Paper #33618', True), ('CEPR Discussion Paper No. 1234', True),
    ('ECB Working Paper Series', True), ('World Bank Policy Research Working Paper 1234', True),
    ('IMF Economic Review', False), ('World Bank Economic Review', False),
    ('American Economic Review; NBER Working Paper 1234', False), (None, False),
])
def test_series_classifier_excludes_journals_and_mixed_venues(venue, expected):
    assert is_working_paper_series(venue) is expected


def test_enrichment_queue_defers_recent_attempts_and_uses_canonical_doi():
    from backend.database.papers import get_unenriched_papers
    with patch('backend.database.papers.fetch_all', return_value=[]) as fetch:
        get_unenriched_papers(50)
    sql, params = fetch.call_args.args
    assert 'enrichment_attempted_at < UTC_TIMESTAMP() - INTERVAL 7 DAY' in sql
    assert 'ORDER BY p.enrichment_attempted_at,' in sql
    assert 'p.doi AS link_doi' in sql
    assert 'MAX(pl.doi)' not in sql
    assert params == (50,)


@pytest.mark.parametrize('event_venue', [None, 'Original journal'])
def test_atom_feed_uses_event_venue_even_when_explicitly_unknown(event_venue):
    from backend.api import _build_atom_feed
    xml = _build_atom_feed([{
        'id': 1, 'title': 'Paper', 'event_id': 1, 'event_type': 'status_change',
        'event_date': '2026-10-01T00:00:00Z', 'old_status': 'working_paper',
        'new_status': 'accepted', 'venue': 'Later journal', 'event_venue': event_venue,
    }], 'https://feed.test')
    assert 'Later journal' not in xml
    assert ('Original journal' in xml) == (event_venue is not None)
