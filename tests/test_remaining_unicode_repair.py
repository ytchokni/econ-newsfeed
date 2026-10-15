"""Safety boundaries for the six explicitly source-verified collision restores."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import repair_remaining_unicode as repair


def rows_for(ids=(68907, 68920)):
    rows = {'papers': {}, 'researchers': [], 'tracked_sources': [
        {'id': 1, 'url': repair.SDU_SOURCE, 'is_active': False},
        {'id': 2, 'url': 'https://secondary.test', 'is_active': True},
    ], 'source_cache': [
        {'id': 10, 'url_id': 1, 'content_hash': 'a', 'extracted_hash': 'a', 'extracted_at': '2026-01-01', 'content': 'original source'},
        {'id': 20, 'url_id': 2, 'content_hash': 'b', 'extracted_hash': 'b', 'extracted_at': '2026-01-02', 'raw_html': 'secondary source'},
    ]}
    seen = set()
    for pid in ids:
        spec = repair.VERIFIED[pid]
        current = {table: [] for table in repair.TABLES}
        current['feed_event_reviews'] = []
        current['papers'] = [{'id': pid, 'title': spec['title'], 'title_hash': 'unsafe-legacy', 'status': 'published'}]
        current['authorship'] = [{'id': pid, 'publication_id': pid, 'researcher_id': 123, 'author_order': 0}]
        current['paper_snapshots'] = [{'id': pid + 1, 'paper_id': pid, 'title': 'Unrelated study', 'source_url': 'https://secondary.test'}]
        current['paper_urls'] = [{'id': pid, 'paper_id': pid, 'url': spec['source_url']}]
        rows['papers'][str(pid)] = current
        for rid, first, last in spec['authors']:
            if rid not in seen:
                rows['researchers'].append({'id': rid, 'first_name': first, 'last_name': last})
                seen.add(rid)
    return rows


def evidence_for(rows, ids=(68907, 68920)):
    return {'scope_paper_ids': list(ids), 'plan_digest': repair.plan_digest(ids), 'rows': deepcopy(rows)}


def connection():
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.rowcount = 1
    return conn, cursor


def writes(cursor):
    return [c for c in cursor.execute.call_args_list if not c.args[0].startswith('SELECT')]


def test_only_six_reviewed_identities_and_source_backed_authors_are_allowed():
    assert set(repair.VERIFIED) == {68907, 68920, 205830, 279630, 312775, 319920}
    assert repair.metadata(205830)['status'] == 'working_paper'
    assert repair.metadata(205830)['year'] == '2018'
    assert repair.metadata(279630)['doi'] is None
    assert repair.metadata(319920)['doi'] == '10.7014/SRMA.2016100003'
    assert [a[0] for a in repair.VERIFIED[68907]['authors']] == [39063]
    assert [a[0] for a in repair.VERIFIED[319920]['authors']] == [129696, 16520, 129695, 129700]
    for ids in ([46014], [84359], [3733], [], [68907, 68907]):
        with pytest.raises(ValueError, match='source-verified'):
            repair.scope_ids(ids)


def test_plan_lists_displaced_history_and_never_queues_inactive_original_source():
    ids = repair.scope_ids()
    rows = rows_for(ids)
    plan = repair.build_plan(rows, ids)
    assert plan['queue_url_ids'] == [2]
    assert plan['inactive_source_url_ids'] == [1]
    assert all(p['displaced_work_worklist'] for p in plan['papers'].values())
    assert plan['papers']['279630']['verified_displaced_works'][0]['snapshot_ids'] == [159402]


def test_original_identity_snapshot_survives_but_unrelated_history_is_archived():
    rows = rows_for((205830,))
    rows['papers']['205830']['paper_snapshots'].append({
        'id': 98827, 'paper_id': 205830, 'title': repair.VERIFIED[205830]['title'],
        'source_url': repair.VERIFIED[205830]['source_url'],
    })
    plan = repair.build_plan(rows, [205830])['papers']['205830']
    assert plan['preserve_snapshot_ids'] == [98827]
    assert [r['snapshot_id'] for r in plan['displaced_work_worklist']] == [205831]


@pytest.mark.parametrize('change', ['author', 'title', 'event'])
def test_unreviewed_identity_or_event_changes_abort_without_writes(change, tmp_path):
    rows = rows_for()
    if change == 'author':
        rows['researchers'][0]['last_name'] = 'Different'
    elif change == 'title':
        rows['papers']['68907']['papers'][0]['title'] = 'Different paper'
    else:
        rows['papers']['68907']['feed_events'] = [{'id': 101, 'paper_id': 68907}]
    conn, cursor = connection()
    with patch.object(repair, 'read_scope', return_value=rows), patch.object(repair, 'write_backup') as backup:
        with pytest.raises(ValueError):
            repair.restore(conn, evidence_for(rows), apply=True, backup=tmp_path / 'before.json')
    backup.assert_not_called()
    assert not writes(cursor)
    conn.rollback.assert_called_once()


def test_any_scoped_drift_aborts_before_backup_or_mutation(tmp_path):
    rows = rows_for()
    evidence = evidence_for(rows)
    rows['source_cache'][1]['content_hash'] = 'new content'
    conn, cursor = connection()
    with patch.object(repair, 'read_scope', return_value=rows), patch.object(repair, 'write_backup') as backup:
        with pytest.raises(ValueError, match='before-images drifted'):
            repair.restore(conn, evidence, apply=True, backup=tmp_path / 'before.json')
    backup.assert_not_called()
    assert not writes(cursor)


def test_changed_plan_or_unknown_scope_cannot_reuse_export(tmp_path):
    rows = rows_for()
    for evidence in (evidence_for(rows) | {'plan_digest': 'outdated'},
                     evidence_for(rows) | {'scope_paper_ids': [3733]}):
        conn, cursor = connection()
        with pytest.raises(ValueError):
            repair.restore(conn, evidence, apply=True, backup=tmp_path / 'before.json')
        conn.start_transaction.assert_not_called()
        assert not writes(cursor)


def test_collision_aborts_before_backup_and_dry_run_never_writes(tmp_path):
    rows = rows_for()
    conn, cursor = connection()
    with patch.object(repair, 'read_scope', return_value=rows), patch.object(repair, 'write_backup') as backup:
        result = repair.restore(conn, evidence_for(rows))
        assert not result['applied']
        assert not writes(cursor)
        cursor.fetchall.return_value = [{'id': 999}]
        with pytest.raises(ValueError, match='already belongs'):
            repair.restore(conn, evidence_for(rows), apply=True, backup=tmp_path / 'before.json')
    backup.assert_not_called()
    conn.commit.assert_not_called()


def test_full_backup_precedes_mutation_doi_and_source_queue_are_scoped(tmp_path):
    ids = repair.scope_ids()
    rows = rows_for(ids)
    conn, cursor = connection()
    real_backup = repair.write_backup
    def archive(path, payload):
        assert not writes(cursor)
        return real_backup(path, payload)
    with patch.object(repair, 'read_scope', return_value=rows), patch.object(repair, 'write_backup', side_effect=archive):
        result = repair.restore(conn, evidence_for(rows, ids), apply=True, backup=tmp_path / 'before.json')
    saved = json.loads(Path(result['backup']).read_text())
    assert saved['rows'] == rows
    assert saved['original_export']['rows'] == rows
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert not any(sql.startswith(('UPDATE researchers ', 'DELETE FROM researchers ', 'UPDATE researcher_urls ')) for sql in sqls)
    queued = [c for c in cursor.execute.call_args_list if c.args[0].startswith('UPDATE html_content')]
    assert len(queued) == 1 and queued[0].args[1] == (20, 'b', 'b', '2026-01-02')
    assert 'ru.is_active = TRUE' in queued[0].args[0]
    links = [c for c in cursor.execute.call_args_list if c.args[0].startswith('INSERT INTO paper_links')]
    assert len(links) == 1
    assert links[0].args[1] == (319920, 'https://doi.org/10.7014/SRMA.2016100003', '10.7014/SRMA.2016100003')
    for c in cursor.execute.call_args_list:
        if c.args[0].startswith('DELETE FROM'):
            assert c.args[1][0] in ids
    conn.commit.assert_called_once()


def test_backup_failure_and_cas_failure_roll_back(tmp_path):
    rows = rows_for()
    conn, cursor = connection()
    with patch.object(repair, 'read_scope', return_value=rows), patch.object(repair, 'write_backup', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            repair.restore(conn, evidence_for(rows), apply=True, backup=tmp_path / 'before.json')
    assert not writes(cursor)
    conn.commit.assert_not_called()
    conn, cursor = connection()
    cursor.rowcount = 0
    with patch.object(repair, 'read_scope', return_value=rows):
        with pytest.raises(ValueError, match='Source changed'):
            repair.restore(conn, evidence_for(rows), apply=True, backup=tmp_path / 'before.json')
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    assert list(tmp_path.glob('before.*.json'))  # retain before-image after a rolled-back attempt


def test_successful_restore_rerun_is_noop_and_does_not_requeue_sources(tmp_path):
    ids = repair.scope_ids()
    rows = rows_for(ids)
    evidence = evidence_for(rows, ids)
    for pid in ids:
        current, spec = rows['papers'][str(pid)], repair.VERIFIED[pid]
        current['papers'][0].update(repair.metadata(pid))
        current['authorship'] = [{'researcher_id': a[0], 'author_order': order} for order, a in enumerate(spec['authors'], 1)]
        current['paper_snapshots'] = []
        current['paper_urls'] = [{'url': u} for u in {spec['source_url'], *spec['evidence_urls']}]
        if spec.get('doi'):
            current['paper_links'] = [{'url': 'https://doi.org/' + spec['doi'], 'doi': spec['doi'], 'link_type': 'doi'}]
    conn, cursor = connection()
    with patch.object(repair, 'read_scope', return_value=rows), patch.object(repair, 'write_backup') as backup:
        result = repair.restore(conn, evidence, apply=True, backup=tmp_path / 'before.json')
    assert result['already_restored'] and not result['applied']
    backup.assert_not_called()
    assert not writes(cursor)
    conn.rollback.assert_called_once()



def test_queue_skips_canonical_only_source_and_known_mixed_owner_even_when_active():
    rows = rows_for((205830,))
    source = repair.VERIFIED[205830]['source_url']
    rows['tracked_sources'] += [
        {'id': 3, 'url': source, 'is_active': True, 'researcher_id': 13571},
        {'id': 4, 'url': 'https://secondary.test', 'is_active': True, 'researcher_id': 2284},
    ]
    rows['source_cache'] += [
        {'id': 30, 'url_id': 3, 'content_hash': 'x', 'content': 'known canonical'},
        {'id': 40, 'url_id': 4, 'content_hash': 'y', 'content': 'unsafe owner'},
    ]
    plan = repair.build_plan(rows, [205830])
    assert plan['queue_url_ids'] == [2]
    assert plan['canonical_only_url_ids'] == [3]
    assert plan['held_source_url_ids'] == [4]
