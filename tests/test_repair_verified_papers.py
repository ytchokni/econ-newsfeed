"""Safety checks for the explicitly verified production repair runner; no real DB."""
from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest

from backend.database.snapshots import _compute_paper_content_hash
from scripts import repair_verified_papers as repair


class Cursor:
    def __init__(self, tables):
        self.tables = tables
        self.calls = []
        self.selected = []
        self.rowcount = 1
        self.lastrowid = 900001

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith('SELECT r.* FROM feed_event_reviews'):
            self.selected = self.tables.get('feed_event_reviews', [])
        elif sql.startswith('SELECT * FROM researchers WHERE last_name'):
            self.selected = self.tables.get('webb_candidates', [])
        elif sql.startswith('SELECT * FROM researcher_urls'):
            self.selected = self.tables.get('webb_urls', [])
        elif sql.startswith('SELECT * FROM '):
            self.selected = self.tables.get(sql.split()[3], [])

    def fetchall(self):
        return deepcopy(self.selected)

    @property
    def writes(self):
        return [(sql, params) for sql, params in self.calls if sql.startswith(('UPDATE ', 'INSERT ', 'DELETE '))]


def fixture(pid=362530):
    snapshot = dict(id=237260, paper_id=pid, title='Verified paper', status='revise_and_resubmit',
                    venue=None, year=None, abstract=None, draft_url=None)
    snapshot['content_hash'] = _compute_paper_content_hash('revise_and_resubmit', None, None, None, None, title=snapshot['title'])
    author = dict(id=1136315, publication_id=pid, researcher_id=20928, author_order=0)
    evidence = {'papers': {str(pid): {
        'paper': [dict(id=pid, status='revise_and_resubmit', venue=None, year=None, abstract=None)],
        'snapshots': [snapshot],
        'events': [dict(id=12037, paper_id=pid, event_type='status_change',
                        old_status='working_paper', new_status='revise_and_resubmit')],
        'authorship': [{**author, 'first_name': 'Youngjin', 'last_name': 'Yun'}],
        'sources': [dict(id=362522, paper_id=pid, url='https://example.org/research', discovered_at='2026-08-15 18:19:05')],
        'source_texts': [{'content': 'Original source evidence'}],
    }}}
    d = evidence['papers'][str(pid)]
    tables = {table: deepcopy(d[key]) for table, key in repair.EVIDENCE_KEYS.items()}
    tables['authorship'] = [author]
    tables['researchers'] = [dict(id=20928, first_name='Youngjin', last_name='Yun')]
    tables['feed_event_reviews'] = [dict(id=9, feed_event_id=12037, explanation='Original review')]
    manifest = {'scope_paper_ids': [pid], 'candidates': [{'paper_id': pid, 'operations': [
        {'operation': 'update', 'table': 'papers', 'where': {'id': pid, 'status': 'revise_and_resubmit'}, 'set': {'status': 'working_paper'}},
        {'operation': 'update', 'table': 'paper_snapshots', 'where': {'id': 237260, 'paper_id': pid, 'status': 'revise_and_resubmit'}, 'set': {'status': 'working_paper'}},
        {'operation': 'delete', 'table': 'feed_events', 'where': {'id': 12037, 'paper_id': pid, 'event_type': 'status_change'}},
    ]}]}
    cursor = Cursor(tables)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return manifest, evidence, tables, cursor, conn


def test_dry_run_checks_without_writing_or_backing_up(tmp_path):
    manifest, evidence, _, cursor, conn = fixture()
    result = repair.run_repair(conn, manifest, evidence, backup=tmp_path / 'backup.json')
    assert result['planned_operations'] == 3
    assert not cursor.writes
    assert not list(tmp_path.iterdir())
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()
    assert all('FOR UPDATE' in sql for sql, _ in cursor.calls)


def test_apply_requires_backup_before_connecting():
    manifest, evidence, _, cursor, conn = fixture()
    with pytest.raises(repair.RepairRefused, match='requires --backup'):
        repair.run_repair(conn, manifest, evidence, apply=True)
    conn.start_transaction.assert_not_called()
    assert not cursor.calls


@pytest.mark.parametrize('table', ['papers; DROP TABLE researchers', 'researchers'])
def test_unwhitelisted_table_is_rejected_before_transaction(table):
    manifest, evidence, _, cursor, conn = fixture()
    manifest['candidates'][0]['operations'][0]['table'] = table
    with pytest.raises(repair.RepairRefused, match='Unsupported'):
        repair.run_repair(conn, manifest, evidence)
    conn.start_transaction.assert_not_called()
    assert not cursor.writes


def test_changed_target_field_aborts_all_operations(tmp_path):
    manifest, evidence, tables, cursor, conn = fixture()
    tables['paper_snapshots'][0]['status'] = 'accepted'
    with pytest.raises(repair.RepairRefused, match='overwrite'):
        repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    assert not cursor.writes
    assert not list(tmp_path.iterdir())
    conn.rollback.assert_called_once()


def test_changed_row_to_be_deleted_aborts_instead_of_discarding_new_evidence(tmp_path):
    manifest, evidence, tables, cursor, conn = fixture()
    tables['feed_events'][0]['new_status'] = 'accepted'
    with pytest.raises(repair.RepairRefused, match='refusing deletion'):
        repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    assert not cursor.writes


def test_unrelated_paper_metadata_change_is_preserved():
    manifest, evidence, tables, _, conn = fixture()
    tables['papers'][0]['abstract'] = 'A legitimate independently updated abstract'
    result = repair.run_repair(conn, manifest, evidence)
    update = result['operations'][0]
    assert update['values'] == {'status': 'working_paper'}


def test_apply_archives_full_provenance_and_cascades_before_writes(tmp_path):
    manifest, evidence, tables, cursor, conn = fixture()
    result = repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    saved = json.loads(__import__('pathlib').Path(result['backup']).read_text())
    assert saved['locked_current_rows']['feed_event_reviews'] == tables['feed_event_reviews']
    assert saved['locked_current_rows']['paper_urls'] == tables['paper_urls']
    assert saved['audited_before_images'] == evidence
    snapshot_update = next(p for p in result['operations'] if p.get('table') == 'paper_snapshots')
    assert snapshot_update['values']['content_hash'] == _compute_paper_content_hash(
        'working_paper', None, None, None, None, title='Verified paper')
    assert len(cursor.writes) == 3
    assert all('%s' in sql for sql, _ in cursor.writes)
    conn.commit.assert_called_once()


def test_failed_backup_prevents_every_mutation(monkeypatch, tmp_path):
    manifest, evidence, _, cursor, conn = fixture()
    monkeypatch.setattr(repair, 'write_backup', MagicMock(side_effect=OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    assert not cursor.writes
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_rerun_is_noop_and_preserves_existing_backup(tmp_path):
    manifest, evidence, tables, cursor, conn = fixture()
    first = repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    for plan in first['operations']:
        table = tables[plan['table']]
        if plan['kind'] == 'delete':
            table[:] = [r for r in table if r['id'] != plan['id']]
        else:
            next(r for r in table if r['id'] == plan['id']).update(plan['values'])
    prior_backups = {p: p.read_bytes() for p in tmp_path.iterdir()}
    cursor.calls.clear()
    second = repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    assert second['planned_operations'] == 0
    assert second['backup'] is None
    assert not cursor.writes
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == prior_backups


def test_backup_paths_are_unique_atomic_and_never_replace_requested_path(tmp_path):
    original = tmp_path / 'backup.json'
    original.write_text('keep me')
    a = repair.write_backup(original, {'attempt': 1})
    b = repair.write_backup(original, {'attempt': 2})
    assert a != b
    assert original.read_text() == 'keep me'
    assert not list(tmp_path.glob('.paper-repair-*'))


def test_provenance_insert_uses_database_utc_clock(tmp_path):
    manifest, evidence, _, cursor, conn = fixture()
    manifest['candidates'][0]['operations'] = [{
        'operation': 'insert_if_absent', 'table': 'paper_urls',
        'rows': [{'paper_id': 362530, 'url': 'https://example.org/new-source', 'discovered_at': '1900-01-01'}],
    }]
    repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    sql, params = cursor.writes[0]
    assert 'UTC_TIMESTAMP()' in sql
    assert params == (362530, 'https://example.org/new-source')


def test_reassignment_requires_verified_target_identity():
    manifest, evidence, tables, cursor, conn = fixture()
    manifest['candidates'][0]['operations'] = [{
        'operation': 'update', 'table': 'authorship',
        'where': {'id': 1136315, 'publication_id': 362530, 'researcher_id': 20928},
        'set': {'researcher_id': 3515},
    }]
    tables['researchers'].append(dict(id=3515, first_name='Someone', last_name='Else'))
    with pytest.raises(repair.RepairRefused, match='3515 no longer matches'):
        repair.run_repair(conn, manifest, evidence)
    assert not cursor.writes


def test_duplicate_webb_identity_is_not_merged_or_guessed():
    manifest, evidence, tables, cursor, conn = fixture(372003)
    tables['webb_candidates'] = [dict(id=100, **repair.WEBB), dict(id=101, **repair.WEBB)]
    with pytest.raises(repair.RepairRefused, match='Multiple possible David Webb'):
        repair.run_repair(conn, manifest, evidence)
    assert not cursor.writes


def test_webb_new_identity_does_not_create_urls(tmp_path):
    manifest, evidence, tables, cursor, conn = fixture(372003)
    manifest['candidates'][0]['operations'] = [{
        'operation': 'create_researcher_if_absent_then_reassign_authorship',
        'researcher': repair.WEBB, 'verified_identity_url': repair.WEBB_URL,
        'authorship_where': {'id': 1136315, 'publication_id': 372003, 'researcher_id': 20928},
        'set_author_order': 0,
    }]
    repair.run_repair(conn, manifest, evidence, apply=True, backup=tmp_path / 'backup.json')
    assert len(cursor.writes) == 2
    assert cursor.writes[0][0].startswith('INSERT INTO researchers')
    assert cursor.writes[1][1] == (cursor.lastrowid, 0, 1136315)
    assert not any('INSERT INTO researcher_urls' in sql for sql, _ in cursor.writes)
