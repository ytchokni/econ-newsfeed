"""The production repair refuses drift and cannot write without a durable backup."""
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from scripts import repair_openalex_identity as repair


@pytest.fixture
def before():
    return {
        'papers': [{'id': repair.PAPER_ID, 'title': repair.TITLE, 'status': 'published',
                    'doi': repair.OLD_DOI, 'openalex_id': repair.OLD_WORK_ID, 'year': '2024'}],
        'paper_authors': [{'id': row_id, 'first_name': name[0], 'last_name': name[1]}
                          for row_id, name in repair.AUTHOR_NAMES.items()],
        'paper_links': [{'id': repair.WRONG_LINK_ID, 'paper_id': repair.PAPER_ID,
                         'url': repair.WRONG_LINK_URL, 'doi': repair.OLD_DOI}],
        'paper_topics': [{'id': 1}], 'openalex_coauthors': [], 'feed_events': [{'id': 4698}],
        'researchers': [{'id': row_id, 'first_name': first, 'last_name': 'Huber',
                         'openalex_author_id': repair.HUBER_ID} for row_id, first in repair.WRONG_HUBERS.items()],
    }


def test_plan_is_scoped_to_verified_bad_identities(before):
    plan = repair.plan_changes(before)
    assert plan['paper_updates'] == repair.CORRECTION
    assert plan['clear_researcher_ids'] == [13875, 14108, 55104]
    assert plan['delete_link_ids'] == [63169]
    assert plan['delete_topic_ids'] == [1]
    assert before['feed_events'] == [{'id': 4698}]


@pytest.mark.parametrize('change', ['title', 'doi', 'author', 'researcher', 'link'])
def test_drift_aborts_plan(before, change):
    if change in ('title', 'doi'):
        before['papers'][0][change] = 'changed'
    elif change == 'author':
        before['paper_authors'][0]['first_name'] = 'Someone'
    elif change == 'researcher':
        before['researchers'][0]['openalex_author_id'] = 'A999'
    else:
        before['paper_links'][0]['url'] = 'https://example.test/changed'
    with pytest.raises(ValueError):
        repair.plan_changes(before)


def test_corrected_rows_produce_empty_idempotent_plan(before):
    before['papers'][0].update(repair.CORRECTION)
    before['paper_links'] = []
    for researcher in before['researchers']:
        researcher['openalex_author_id'] = None
    plan = repair.plan_changes(before)
    assert not any(plan[key] for key in ('paper_updates', 'delete_link_ids', 'delete_topic_ids', 'clear_researcher_ids'))


def test_dry_run_never_writes(before):
    conn = MagicMock()
    with patch.object(repair, 'read_rows', return_value=before), patch.object(repair, 'write_backup') as backup:
        assert not repair.repair(conn)['applied']
    conn.cursor.return_value.__enter__.return_value.execute.assert_not_called()
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()
    backup.assert_not_called()


def test_backup_failure_prevents_mutation(before):
    conn = MagicMock()
    with (
        patch.object(repair, 'read_rows', return_value=before),
        patch.object(repair, 'write_backup', side_effect=OSError('disk full')),
        pytest.raises(OSError),
    ):
        repair.repair(conn, apply=True, backup='/backups/test.json')
    conn.cursor.return_value.__enter__.return_value.execute.assert_not_called()
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()


def test_apply_backs_up_before_writes_and_preserves_events(before):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    def backup(path, bundle):
        cursor.execute.assert_not_called()
        assert bundle['before'] == before
        return '/backups/unique.json'
    with patch.object(repair, 'read_rows', return_value=deepcopy(before)), patch.object(repair, 'write_backup', side_effect=backup):
        assert repair.repair(conn, apply=True, backup='/backups/test.json')['applied']
    assert all('feed_events' not in call.args[0] for call in cursor.execute.call_args_list)
    conn.commit.assert_called_once()


@pytest.fixture
def merged_before():
    """A bounded reproduction of the reviewed survivor export, not a live DB."""
    rows = {table: [] for table in repair.RECOVERY_TABLES}
    rows['papers'] = [
        {'id': 71115, 'title': 'Reaching for the Society: The Commercializing Effects of NASA Technology Licensing',
         'doi': repair.OLD_DOI, 'openalex_id': repair.OLD_WORK_ID, 'abstract': None},
        {'id': 353074, 'title': 'Do Managerial Incentives Facilitate Anti-Competitive Behavior?', 'doi': None},
    ]
    rows['paper_snapshots'] = [
        {'id': row_id, 'paper_id': 71115, 'title': repair.TITLE, 'source_url': 'http://www.marekgiebel.com/research',
         'status': 'published', 'year': '2026', 'venue': repair.RECOVERY_FIELDS['venue'],
         'content_hash': f'original-{row_id}', 'abstract': f'original abstract {row_id}'}
        for row_id in sorted(repair.SNAPSHOT_IDS)
    ] + [{'id': 230616, 'paper_id': 71115, 'title': 'NASA original'},
         {'id': 218781, 'paper_id': 353074, 'title': 'Short alias'}]
    rows['paper_links'] = [
        {'id': row_id, 'paper_id': 71115, 'url': value[0], 'doi': value[1],
         'discovered_at': '2026-04-09 18:00:07', 'link_type': 'doi'}
        for row_id, value in repair.LINKS_TO_RESTORE.items()
    ] + [{'id': 63169, 'paper_id': 71115, 'url': repair.WRONG_LINK_URL, 'doi': repair.OLD_DOI}]
    rows['feed_events'] = [
        {'id': 4698, 'paper_id': 71115, 'event_type': 'status_change', 'old_status': 'accepted',
         'new_status': 'published', 'created_at': '2026-06-17 17:31:09',
         'event_venue': None, 'event_source_url': None, 'event_metadata_captured': False},
        {'id': 5068, 'paper_id': 71115, 'event_type': 'title_change'},
    ]
    rows['feed_event_reviews'] = [{'id': 526, 'feed_event_id': 4698, 'issues': '[]'}]
    rows['researchers'] = [{'id': row_id, 'first_name': name[0], 'last_name': name[1]}
                           for row_id, name in repair.AUTHOR_NAMES.items()]
    rows['openalex_coauthors'] = [
        {'id': 28993, 'paper_id': 71115, 'display_name': 'Marek Giebel', 'openalex_author_id': 'A5083169965'},
        {'id': 28994, 'paper_id': 71115, 'display_name': 'Anja Rösner', 'openalex_author_id': 'A5087564492'},
    ]
    rows['paper_urls'] = [{'id': 1, 'paper_id': 71115, 'url': 'https://www.anjaroesner.com'},
                          {'id': 2, 'paper_id': 353074, 'url': 'https://www.anjaroesner.com'}]
    rows['authorship'] = [
        {'id': 1, 'publication_id': 71115, 'researcher_id': 10001, 'author_order': 1},
        {'id': 2, 'publication_id': 71115, 'researcher_id': 8638, 'author_order': 2},
        {'id': 3, 'publication_id': 353074, 'researcher_id': 8638, 'author_order': 0},
    ]
    return rows


def test_recovery_plan_does_not_invent_discovery_or_move_nasa_children(merged_before):
    plan = repair.recovery_plan(merged_before, deepcopy(merged_before))
    assert not plan['already_restored']
    assert plan['paper_values']['discovered_at'] is None
    assert plan['earliest_retained_evidence'] == '2026-04-09 18:00:07'
    assert 63169 not in plan['move_link_ids']
    assert 230616 not in plan['move_snapshot_ids']
    assert 218781 not in plan['move_snapshot_ids']
    assert plan['move_event_id'] == 4698
    assert plan['event_values']['event_source_url'] == 'http://www.marekgiebel.com/research'


@pytest.mark.parametrize('table,field,value', [
    ('papers', 'abstract', 'new NASA abstract'),
    ('paper_links', 'doi', '10.1234/drift'),
    ('paper_snapshots', 'content_hash', 'changed'),
    ('feed_events', 'created_at', '2026-10-15 00:00:00'),
    ('researchers', 'first_name', 'Someone else'),
    ('feed_event_reviews', 'issues', '["new finding"]'),
])
def test_recovery_refuses_any_audited_row_drift(merged_before, table, field, value):
    evidence = deepcopy(merged_before)
    merged_before[table][0][field] = value
    with pytest.raises(ValueError):
        repair.recovery_plan(merged_before, evidence)


def test_recovery_rerun_is_noop_with_exact_moved_history(merged_before):
    evidence = deepcopy(merged_before)
    plan = repair.recovery_plan(merged_before, evidence)
    merged_before['papers'].append({'id': 71119, **repair.RECOVERY_FIELDS})
    for table, ids in [('paper_snapshots', repair.SNAPSHOT_IDS), ('paper_links', set(repair.LINKS_TO_RESTORE))]:
        for row in merged_before[table]:
            if row['id'] in ids:
                row['paper_id'] = 71119
    merged_before['feed_events'][0].update(plan['event_values'])
    merged_before['authorship'] += [
        {'id': 10, 'publication_id': 71119, 'researcher_id': 10001, 'author_order': 1},
        {'id': 11, 'publication_id': 71119, 'researcher_id': 8638, 'author_order': 2},
    ]
    merged_before['paper_urls'] += [{'id': i, 'paper_id': 71119, 'url': url}
                                   for i, url in enumerate(sorted(repair.SOURCE_URLS), 10)]
    merged_before['openalex_coauthors'] += [
        {**author, 'id': i, 'paper_id': 71119} for i, author in enumerate(plan['copy_coauthors'], 10)]
    assert repair.recovery_plan(merged_before, evidence)['already_restored']
    conn = MagicMock()
    with patch.object(repair, 'read_recovery_rows', return_value=merged_before), patch.object(repair, 'write_backup') as backup:
        assert not repair.recover_merged(conn, evidence, apply=True, backup='/backups/test.json')['applied']
    conn.cursor.return_value.__enter__.return_value.execute.assert_not_called()
    backup.assert_not_called()


def test_recovery_backs_up_before_reassignments_and_never_deletes(merged_before):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.rowcount = 1
    def backup(path, bundle):
        assert all(call.args[0].startswith('SELECT') for call in cursor.execute.call_args_list)
        assert bundle['before'] == merged_before
        return '/backups/unique.json'
    with patch.object(repair, 'read_recovery_rows', return_value=merged_before), patch.object(repair, 'write_backup', side_effect=backup):
        assert repair.recover_merged(conn, deepcopy(merged_before), apply=True, backup='/backups/test.json')['applied']
    sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert not any(statement.startswith('DELETE') for statement in sql)
    assert not any(statement.startswith('UPDATE papers') for statement in sql)
    snapshot_calls = [call for call in cursor.execute.call_args_list if call.args[0].startswith('UPDATE paper_snapshots')]
    assert len(snapshot_calls) == 9
    assert all(call.args[0] == 'UPDATE paper_snapshots SET paper_id = %s WHERE id = %s AND paper_id = %s' for call in snapshot_calls)
    assert all(call.args[1][0] == 71119 and call.args[1][2] == 71115 for call in snapshot_calls)
    conn.commit.assert_called_once()


def test_recovery_rowcount_failure_rolls_back_whole_split(merged_before):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    with (
        patch.object(repair, 'read_recovery_rows', return_value=merged_before),
        patch.object(repair, 'write_backup', return_value='/backups/unique.json'),
        pytest.raises(ValueError, match='changed during recovery'),
    ):
        repair.recover_merged(conn, deepcopy(merged_before), apply=True, backup='/backups/test.json')
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()


def test_recovery_backup_failure_prevents_all_writes(merged_before):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    with (
        patch.object(repair, 'read_recovery_rows', return_value=merged_before),
        patch.object(repair, 'write_backup', side_effect=OSError('disk full')),
        pytest.raises(OSError),
    ):
        repair.recover_merged(conn, deepcopy(merged_before), apply=True, backup='/backups/test.json')
    assert all(call.args[0].startswith('SELECT') for call in cursor.execute.call_args_list)
    conn.commit.assert_not_called()
