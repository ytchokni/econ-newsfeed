"""Unicode title identities and conservative merge/repair boundaries."""
import hashlib
import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from backend.database.papers import (compute_title_hash, find_papers_by_titles,
                                     normalize_title, title_hash_candidates)
from backend.enrichment.paper_merge import _compatible_identity, _connected_groups, merge_paper_group
from backend.pipeline.publication import verify_title_in_html
from backend.pipeline.feed_events import _title_in_previous_snapshot, _normalize_html_for_matching
from scripts import repair_unicode_identity as repair


@pytest.mark.parametrize('title', ['日本の経済政策', '中国经济与贸易', 'Денежная политика', 'বাংলাদেশের অর্থনীতি'])
def test_exact_non_latin_source_is_verified_without_ascii_loss(title):
    assert verify_title_in_html(title, 'Research: ' + title)
    assert _title_in_previous_snapshot(title, _normalize_html_for_matching('<p>' + title + '</p>'))
    assert normalize_title(title)


def test_matching_numeric_suffixes_do_not_equate_unrelated_unicode_titles():
    a, b = '日本の経済政策2024', '中国贸易政策2024'
    assert compute_title_hash(a) != compute_title_hash(b)
    assert not verify_title_in_html(a, b)
    assert len(title_hash_candidates(a)) == 1  # excludes the old unsafe '2024' bucket


def test_safe_legacy_row_remains_discoverable_and_wrong_hash_match_is_refused():
    old_hash = hashlib.sha256(b'caf economics').hexdigest()
    with patch('backend.database.papers.fetch_all', return_value=[
        {'id': 7, 'title': 'Café Economics', 'title_hash': old_hash},
    ]) as fetch:
        assert find_papers_by_titles(['Cafe\u0301 Economics'])['Cafe\u0301 Economics']['id'] == 7
    assert old_hash in fetch.call_args.args[1]
    with patch('backend.database.papers.fetch_all', return_value=[
        {'id': 7, 'title': '经济 FX', 'title_hash': compute_title_hash('FX')},
    ]):
        assert find_papers_by_titles(['FX']) == {}


def test_unicode_composition_is_stable_and_ascii_hashes_stay_compatible():
    assert compute_title_hash('Café') == compute_title_hash('Cafe\u0301')
    assert compute_title_hash('Trade, and Wages!') == hashlib.sha256(b'trade and wages').hexdigest()


def test_transitive_groups_collapse_bridges():
    assert _connected_groups([{1, 2}, {3, 4}, {2, 3}]) == [[1, 2, 3, 4]]


@pytest.mark.parametrize('change', [
    {'title': 'Managerial Collusion and Market Competition'},
    {'author_ids': {99}}, {'doi': '10.1234/other'}, {'openalex_id': 'W999'},
    {'title': 'NASA Licensing Part II'},
])
def test_shared_bad_identifier_never_overrides_title_authors_or_conflicts(change):
    paper = {'title': 'NASA Licensing', 'doi': '10.1234/nasa', 'openalex_id': 'W1', 'author_ids': {1, 2}}
    assert not _compatible_identity(paper, {**paper, **change})


@pytest.mark.parametrize('reverse', [False, True])
def test_merger_keeps_editorial_venue_year_ids_and_link_in_both_canonical_orders(reverse):
    papers = [
        {'id': 1, 'title': 'Healthcare workers and life satisfaction', 'status': 'working_paper',
         'venue': 'IZA Discussion Paper', 'year': '2023', 'discovered_at': '2026-01-01',
         'doi': None, 'openalex_id': None, 'draft_url': 'https://old.test/paper.pdf',
         'draft_url_status': 'valid', 'source_url': 'https://old.test'},
        {'id': 2, 'title': 'Healthcare workers and life satisfaction', 'status': 'accepted',
         'venue': 'Health Policy', 'year': '2026', 'discovered_at': '2026-02-01',
         'doi': '10.1234/health', 'openalex_id': 'W1', 'draft_url': None,
         'source_url': 'https://new.test', 'abstract': 'Verified abstract'},
    ]
    if reverse:
        papers[0]['discovered_at'], papers[1]['discovered_at'] = papers[1]['discovered_at'], papers[0]['discovered_at']
    cursor = MagicMock()
    cursor.fetchall.side_effect = [papers, [{'publication_id': pid, 'researcher_id': rid} for pid in (1, 2) for rid in (8, 9)], []]
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor
    with patch('backend.enrichment.paper_merge.get_connection', return_value=conn):
        assert merge_paper_group([1, 2])
    values = next(c.args[1] for c in cursor.execute.call_args_list if c.args[0].startswith('UPDATE papers SET status'))
    assert values[:4] == ('accepted', 'Health Policy', '2026', 'https://new.test')
    assert values[5:9] == ('10.1234/health', 'W1', 'https://old.test/paper.pdf', 'valid')
    assert values[-1] == (2 if reverse else 1)
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    move = next(i for i, sql in enumerate(sqls) if sql.startswith('UPDATE IGNORE `paper_links`'))
    fallback = next(i for i, sql in enumerate(sqls) if sql.startswith('INSERT IGNORE INTO paper_links'))
    reconcile = next(i for i, sql in enumerate(sqls) if sql.startswith('UPDATE paper_links c JOIN'))
    assert reconcile < move < fallback
    assert 'c.doi = COALESCE(c.doi, d.doi)' in sqls[reconcile]


def test_incompatible_group_is_rolled_back_without_any_writes():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [[
        {'id': 1, 'title': 'NASA Licensing', 'doi': '10.1234/shared', 'discovered_at': None},
        {'id': 2, 'title': 'Managerial Collusion', 'doi': '10.1234/shared', 'discovered_at': None},
    ], [{'publication_id': pid, 'researcher_id': 1} for pid in (1, 2)]]
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor
    with patch('backend.enrichment.paper_merge.get_connection', return_value=conn):
        assert not merge_paper_group([1, 2])
    assert all(c.args[0].startswith('SELECT') for c in cursor.execute.call_args_list)
    conn.rollback.assert_called_once()


def repair_rows():
    rows = {table: [] for table in repair.TABLES}
    rows.update(feed_event_reviews=[], researchers=[], source_queue=[
        {'id': 10, 'url_id': 100, 'url': 'https://source.test', 'is_active': True,
         'content_hash': 'current', 'extracted_hash': 'current', 'extracted_at': '2026-10-01', 'raw_html': '<p>Research</p>'},
        {'id': 11, 'url_id': 101, 'url': 'https://inactive.test', 'is_active': False,
         'content_hash': 'current', 'extracted_hash': 'current', 'content': 'Research'},
    ], verified_author_candidates=[{'id': i, 'first_name': name[0], 'last_name': name[1]} for i, name in zip((658, 659), repair.AUTHORS)])
    rows['papers'] = [{'id': 3733, 'title': repair.TITLE, 'title_hash': hashlib.sha256(b'').hexdigest()}]
    rows['paper_snapshots'] = [{'id': 1, 'title': 'Unrelated non-Latin paper', 'source_url': 'https://source.test'}]
    return rows


def test_repair_plan_queues_only_active_sources_without_guessing_contaminated_authors():
    plan = repair.original_plan(repair_rows())
    assert plan['author_ids_in_order'] == [658, 659]
    assert plan['preserve_snapshot_ids'] == []
    assert plan['queue_for_seed_extraction'] == [100]
    assert plan['verified_metadata']['doi'] == '10.11167/jbef.11.132'
    assert plan['verified_metadata']['year'] == '2019'


def test_repair_rejects_any_drift_before_backing_up_or_writing(tmp_path):
    evidence = {'rows': repair_rows()}
    current = deepcopy(evidence['rows'])
    current['authorship'].append({'id': 42, 'researcher_id': 888, 'publication_id': 3733})
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    with patch.object(repair, 'read_bundle', return_value=current), patch.object(repair, 'write_backup') as backup:
        with pytest.raises(ValueError, match='drifted'):
            repair.restore_original(conn, evidence, apply=True, backup=tmp_path / 'before.json')
    assert not cursor.execute.called
    backup.assert_not_called()
    conn.commit.assert_not_called()


def test_backup_failure_cannot_delete_contaminated_evidence(tmp_path):
    rows = repair_rows()
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchall.return_value = []
    with patch.object(repair, 'read_bundle', return_value=rows), patch.object(repair, 'write_backup', side_effect=OSError('full')):
        with pytest.raises(OSError):
            repair.restore_original(conn, {'rows': rows}, apply=True, backup=tmp_path / 'before.json')
    assert all(c.args[0].startswith('SELECT') for c in cursor.execute.call_args_list)
    conn.rollback.assert_called_once()


def test_repair_archives_all_evidence_before_cas_queue_and_never_reactivates_sources(tmp_path):
    rows = repair_rows()
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.rowcount = 1
    with patch.object(repair, 'read_bundle', return_value=rows):
        result = repair.restore_original(conn, {'rows': rows}, apply=True, backup=tmp_path / 'before.json')
    saved = json.loads(__import__('pathlib').Path(result['backup']).read_text())
    assert saved['rows'] == rows
    queued = [c for c in cursor.execute.call_args_list if c.args[0].startswith('UPDATE html_content')]
    assert len(queued) == 1
    assert queued[0].args[1] == (10, 'current', 'current', '2026-10-01')
    assert 'ru.is_active = TRUE' in queued[0].args[0]
    assert not any('SET is_active' in c.args[0] for c in cursor.execute.call_args_list)
    conn.commit.assert_called_once()


def test_seed_reconstruction_updates_snapshot_without_feed_event():
    from backend.database.snapshots import PaperSnapshotResult
    from backend.pipeline.extraction import _append_snapshots
    with patch('backend.pipeline.extraction.find_papers_by_titles', return_value={'日本経済': {'id': 1}}), \
         patch('backend.pipeline.extraction.append_paper_snapshot', return_value=PaperSnapshotResult(True, 'working_paper', 'published')), \
         patch('backend.pipeline.extraction.FeedEventEmitter') as emitter:
        _append_snapshots([{'title': '日本経済', 'status': 'published'}], 'https://source.test', emit_events=False)
    emitter.emit_status_change.assert_not_called()


def test_saver_upgrades_matching_safe_legacy_hash_before_insert():
    from backend.pipeline.paper_saver import PaperSaver
    title = 'Café Economics'
    old_hash = hashlib.sha256(b'caf economics').hexdigest()
    conn, cursor = MagicMock(), MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor
    cursor.lastrowid = 0
    cursor.rowcount = 1
    cursor.fetchall.return_value = [(7, title, old_hash)]
    cursor.fetchone.side_effect = [(7, title), (None, None, None, 'working_paper'), None]
    with patch('backend.pipeline.paper_saver.get_connection', return_value=conn), \
         patch('backend.pipeline.paper_saver.fetch_all', return_value=[]):
        results = PaperSaver.save_publications('https://source.test', [
            {'title': title, 'authors': [], 'status': 'working_paper'},
        ])
    assert len(results) == 1
    assert results[0].paper_id == 7 and not results[0].is_new
    update = next(i for i, c in enumerate(cursor.execute.call_args_list) if c.args[0].startswith('UPDATE papers SET title_hash'))
    insert = next(i for i, c in enumerate(cursor.execute.call_args_list) if 'INSERT IGNORE INTO papers' in c.args[0])
    assert update < insert
    conn.commit.assert_called_once()


def test_saver_refuses_ascii_title_collision_with_old_non_latin_record():
    from backend.pipeline.paper_saver import PaperSaver
    conn, cursor = MagicMock(), MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor
    cursor.lastrowid = 0
    cursor.fetchone.return_value = (7, '中国经济 FX')
    with patch('backend.pipeline.paper_saver.get_connection', return_value=conn), \
         patch('backend.pipeline.paper_saver.fetch_all', return_value=[]):
        results = PaperSaver.save_publications('https://source.test', [{'title': 'FX', 'authors': []}])
    assert not results
    assert not any('INSERT IGNORE INTO authorship' in c.args[0] for c in cursor.execute.call_args_list)
    conn.rollback.assert_called_once()


def test_repair_rerun_after_restoration_is_noop(tmp_path):
    evidence = {'rows': repair_rows()}
    current = repair_rows()
    plan = repair.original_plan(current)
    current['papers'][0].update(plan['verified_metadata'])
    current['paper_snapshots'] = []
    current['authorship'] = [{'researcher_id': rid} for rid in plan['author_ids_in_order']]
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    with patch.object(repair, 'read_bundle', return_value=current), patch.object(repair, 'write_backup') as backup:
        result = repair.restore_original(conn, evidence, apply=True, backup=tmp_path / 'before.json')
    assert result['already_restored']
    backup.assert_not_called()
    cursor.execute.assert_not_called()
    conn.rollback.assert_called_once()


@pytest.mark.parametrize('shared_id', [None, '10.1234/possibly-wrong'])
def test_same_authors_and_near_identical_geography_titles_are_separate(shared_id):
    paper = {'title': 'The Effects of Immigration on Employment and Wages in Germany',
             'author_ids': {1, 2}, 'doi': shared_id}
    assert not _compatible_identity(paper, {**paper, 'title': paper['title'].replace('Germany', 'France')})


@pytest.mark.parametrize('collision', [True, False])
def test_rename_snapshot_joins_transaction_only_after_collision_check(collision):
    from backend.pipeline.paper_saver import PaperSaver
    conn, cursor = MagicMock(), MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (2,) if collision else None
    with patch('backend.pipeline.paper_saver.get_connection', return_value=conn), \
         patch('backend.pipeline.paper_saver.append_paper_snapshot') as snapshot:
        if collision:
            with pytest.raises(ValueError, match='require a validated merge'):
                PaperSaver.apply_title_rename(1, 'Old title', 'New title', {'status': 'published'}, 'https://source.test')
            snapshot.assert_not_called()
            assert all(c.args[0].startswith('SELECT') for c in cursor.execute.call_args_list)
            conn.commit.assert_not_called()
        else:
            PaperSaver.apply_title_rename(1, 'Old title', 'New title', {'status': 'published'}, 'https://source.test')
            assert snapshot.call_args.kwargs['conn'] is conn
            conn.commit.assert_called_once()


def test_snapshot_caller_owned_connection_does_not_commit_or_close():
    from backend.database.snapshots import append_paper_snapshot
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [{'status': 'working_paper'}, None]
    with patch('backend.database.snapshots.get_connection') as connect:
        result = append_paper_snapshot(1, 'published', 'Journal', None, None, '2026', conn=conn)
    assert result.status_changed
    connect.assert_not_called()
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


def test_rehash_changes_only_safe_legacy_rows_and_is_idempotent(tmp_path):
    title = 'Café Economics'
    old_hash = hashlib.sha256(b'caf economics').hexdigest()
    rows = [
        {'id': 7, 'title': title, 'title_hash': old_hash},
        {'id': 8, 'title': '日本経済', 'title_hash': hashlib.sha256(b'').hexdigest()},
        {'id': 9, 'title': 'Other Café', 'title_hash': hashlib.sha256(b'other caf').hexdigest()},
    ]
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.rowcount = 1
    cursor.fetchall.side_effect = [rows, [{'id': 10, 'title': title}], [],
                                  [{'id': 11, 'title': '日本経済'}], [{'id': 12, 'title': 'Different study'}]]
    result = repair.rehash_batch(conn, apply=True, backup=tmp_path / 'hashes.json')
    assert [p['id'] for p in result['plans']] == [7]
    assert [p['id'] for p in result['skipped']] == [8, 9]
    backup = json.loads(__import__('pathlib').Path(result['backup']).read_text())
    assert backup['rows'] == rows
    updates = [c for c in cursor.execute.call_args_list if c.args[0].startswith('UPDATE')]
    assert len(updates) == 1
    assert updates[0].args[1] == (compute_title_hash(title), 7, old_hash)
    conn.commit.assert_called_once()

    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchall.return_value = [{**rows[0], 'title_hash': compute_title_hash(title)}]
    result = repair.rehash_batch(conn, apply=True, backup=tmp_path / 'should-not-exist.json')
    assert result['plans'] == [] and not result['applied']
    assert all(c.args[0].startswith('SELECT') for c in cursor.execute.call_args_list)
    conn.commit.assert_not_called()
