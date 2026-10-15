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
