"""Source-verified OpenAlex corrections, dry-run by default.

python scripts/repair_openalex_identity.py
python scripts/repair_openalex_identity.py --apply --backup /backups/openalex-identity.json
If the old merger has already deleted 71119, recover its audited children first:
python scripts/repair_openalex_identity.py --recover-merged survivor-export.json

The correction mode writes only paper 71119 and three incompatible Huber IDs.
Recovery recreates 71119 and reassigns its exact audited children from 71115;
the NASA canonical row and paper 353074 remain unchanged. The primary metadata
below was checked on 2026-10-15 using free DOI/author lookups.
The abbreviated C Huber record is deliberately left for separate review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database.papers import compute_title_hash, normalize_title
from scripts.repair_verified_papers import write_backup

PAPER_ID = 71119
TITLE = 'Do Managerial Incentives Facilitate Anti-Competitive Behavior? Evidence from Collusion'
OLD_DOI = '10.2139/ssrn.5008782'
OLD_WORK_ID = 'W4404576306'
CORRECTION = {'doi': '10.1016/j.jebo.2026.107625', 'openalex_id': 'W4404578895', 'year': '2026'}
HUBER_ID = 'A5049313917'
WRONG_HUBERS = {13875: 'Johannes', 14108: 'Stefanie Jeanette', 55104: 'Jürgen'}
AUTHOR_NAMES = {10001: ('Marek', 'Giebel'), 8638: ('Anja', 'Rösner')}
WRONG_LINK_ID = 63169
WRONG_LINK_URL = 'https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5008782'
PRIMARY_EVIDENCE = {
    'verified_date': '2026-10-15',
    'correct_work': 'https://api.openalex.org/works/doi:10.1016/j.jebo.2026.107625',
    'correct_preprint': 'https://api.openalex.org/works/doi:10.2139/ssrn.5008778',
    'wrong_work': 'https://api.openalex.org/works/doi:10.2139/ssrn.5008782',
    'author': 'https://api.openalex.org/authors/A5049313917',
    'author_name': 'Christoph Huber', 'author_orcid': '0000-0001-5820-571X',
}

SURVIVOR_ID = 71115
UNTOUCHED_ALIAS_ID = 353074
RECOVERY_IDS = (SURVIVOR_ID, PAPER_ID, UNTOUCHED_ALIAS_ID)
SNAPSHOT_IDS = {224904, 217957, 95662, 94457, 93920, 79266, 79253, 66457, 33453}
LINKS_TO_RESTORE = {
    25874: ('https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5008778', '10.2139/ssrn.5008778'),
    45302: ('https://doi.org/10.1016/j.jebo.2026.107625', CORRECTION['doi']),
    45303: ('http://dx.doi.org/10.2139/ssrn.5008778', '10.2139/ssrn.5008778'),
    81105: ('https://www.sciencedirect.com/science/article/pii/S0167268126002118', CORRECTION['doi']),
}
SOURCE_URLS = {
    'http://www.marekgiebel.com', 'http://www.marekgiebel.com/publications',
    'http://www.marekgiebel.com/research', 'https://www.anjaroesner.com',
    'https://www.anjaroesner.com/research',
}
RECOVERY_FIELDS = {
    'title': TITLE, 'title_hash': compute_title_hash(TITLE), **CORRECTION,
    'status': 'published', 'venue': 'Journal of Economic Behavior and Organization',
    'source_url': 'https://www.anjaroesner.com', 'abstract': None,
    'discovered_at': None, 'is_seed': True, 'draft_url': None,
    'draft_url_status': 'unchecked', 'draft_url_checked_at': None,
}
RECOVERY_TABLES = ('papers', 'paper_links', 'paper_urls', 'openalex_coauthors',
                   'paper_topics', 'paper_snapshots', 'feed_events', 'authorship')


def read_recovery_rows(cursor):
    """Lock and back up the complete two-paper history plus untouched alias."""
    rows = {}
    for table in RECOVERY_TABLES:
        key = 'id' if table == 'papers' else 'publication_id' if table == 'authorship' else 'paper_id'
        cursor.execute(f'SELECT * FROM {table} WHERE {key} IN (%s, %s, %s) ORDER BY id FOR UPDATE', RECOVERY_IDS)
        rows[table] = cursor.fetchall()
    cursor.execute('SELECT r.* FROM feed_event_reviews r JOIN feed_events e ON e.id = r.feed_event_id '
                   'WHERE e.paper_id IN (%s, %s, %s) ORDER BY r.id FOR UPDATE', RECOVERY_IDS)
    rows['feed_event_reviews'] = cursor.fetchall()
    cursor.execute('SELECT * FROM researchers WHERE id IN (%s, %s) ORDER BY id FOR UPDATE', tuple(AUTHOR_NAMES))
    rows['researchers'] = cursor.fetchall()
    return rows


def _same_audited_rows(actual, expected, *, changes=None):
    """Compare every exported field; allow newly deployed, non-audited columns."""
    by_id = {row['id']: row for row in actual}
    if set(by_id) != {row['id'] for row in expected}:
        return False
    for before in expected:
        target = {**before, **((changes or {}).get(before['id'], {}))}
        for key, value in target.items():
            current = by_id[before['id']].get(key)
            if hasattr(current, 'isoformat'):
                current = str(current)
            if current != value:
                return False
    return True


def recovery_plan(rows, evidence):
    """Recover only exact child IDs established in the pre-merge audit."""
    before_papers = {row['id']: row for row in evidence['papers']}
    if set(before_papers) != {SURVIVOR_ID, UNTOUCHED_ALIAS_ID}:
        raise ValueError('Recovery evidence must show missing 71119 and the audited survivor/alias')
    survivor = before_papers[SURVIVOR_ID]
    if (survivor['title'] != 'Reaching for the Society: The Commercializing Effects of NASA Technology Licensing'
            or survivor['doi'] != OLD_DOI or survivor['openalex_id'] != OLD_WORK_ID):
        raise ValueError('Survivor does not match the audited NASA identity')
    authors = {r['id']: (r['first_name'], r['last_name']) for r in evidence['researchers']}
    if authors != AUTHOR_NAMES:
        raise ValueError('Verified author identities changed')
    snapshots = {r['id']: r for r in evidence['paper_snapshots'] if r['id'] in SNAPSHOT_IDS}
    if set(snapshots) != SNAPSHOT_IDS or any(
        r['paper_id'] != SURVIVOR_ID or r['title'] not in (TITLE, 'Do Managerial Incentives Facilitate Collusion?')
        or r['source_url'] not in SOURCE_URLS for r in snapshots.values()
    ):
        raise ValueError('The nine original collusion snapshots are not intact on the survivor')
    links = {r['id']: r for r in evidence['paper_links'] if r['id'] in LINKS_TO_RESTORE}
    if set(links) != set(LINKS_TO_RESTORE) or any(
        r['paper_id'] != SURVIVOR_ID or (r['url'], r['doi']) != LINKS_TO_RESTORE[r['id']]
        for r in links.values()
    ):
        raise ValueError('The four original collusion links are not intact on the survivor')
    events = [r for r in evidence['feed_events'] if r['id'] == 4698]
    if len(events) != 1 or any(events[0].get(k) != v for k, v in {
        'paper_id': SURVIVOR_ID, 'event_type': 'status_change', 'old_status': 'accepted',
        'new_status': 'published', 'created_at': '2026-06-17 17:31:09',
    }.items()):
        raise ValueError('Original collusion event 4698 changed')
    published = snapshots[79253]
    if published['status'] != 'published' or published['year'] != '2026' or published['venue'] != RECOVERY_FIELDS['venue']:
        raise ValueError('Published snapshot cannot establish historical event metadata')
    event_values = {'paper_id': PAPER_ID, 'event_venue': published['venue'],
                    'event_source_url': published['source_url'], 'event_metadata_captured': True}
    coauthors = [r for r in evidence['openalex_coauthors'] if r['paper_id'] == SURVIVOR_ID]
    if {(r['display_name'], r['openalex_author_id']) for r in coauthors} != {
        ('Marek Giebel', 'A5083169965'), ('Anja Rösner', 'A5087564492'),
    }:
        raise ValueError('Shared coauthor identities are not verified')
    restored = next((r for r in rows['papers'] if r['id'] == PAPER_ID), None)
    for table in (*RECOVERY_TABLES, 'feed_event_reviews', 'researchers'):
        actual = rows[table]
        changes = None
        if restored:
            if table in ('papers', 'paper_urls', 'openalex_coauthors', 'authorship'):
                key = 'id' if table == 'papers' else 'publication_id' if table == 'authorship' else 'paper_id'
                actual = [r for r in actual if r[key] != PAPER_ID]
            elif table == 'paper_snapshots':
                changes = {row_id: {'paper_id': PAPER_ID} for row_id in SNAPSHOT_IDS}
            elif table == 'paper_links':
                changes = {row_id: {'paper_id': PAPER_ID} for row_id in LINKS_TO_RESTORE}
            elif table == 'feed_events':
                changes = {4698: event_values}
        if not _same_audited_rows(actual, evidence[table], changes=changes):
            raise ValueError(f'{table} changed since the reviewed recovery export')
    if restored:
        if any(restored.get(k) != v for k, v in RECOVERY_FIELDS.items()):
            raise ValueError('Restored paper changed; re-review before reapplying')
        actual_authors = {(r['researcher_id'], r['author_order']) for r in rows['authorship'] if r['publication_id'] == PAPER_ID}
        actual_urls = {r['url'] for r in rows['paper_urls'] if r['paper_id'] == PAPER_ID}
        actual_coauthors = {(r['display_name'], r['openalex_author_id']) for r in rows['openalex_coauthors'] if r['paper_id'] == PAPER_ID}
        if (actual_authors != {(10001, 1), (8638, 2)} or actual_urls != SOURCE_URLS or
                actual_coauthors != {(r['display_name'], r['openalex_author_id']) for r in coauthors}):
            raise ValueError('Restored associations changed; re-review before reapplying')
    return {'already_restored': restored is not None, 'paper_values': RECOVERY_FIELDS,
            'move_snapshot_ids': sorted(SNAPSHOT_IDS), 'move_link_ids': sorted(LINKS_TO_RESTORE),
            'move_event_id': 4698, 'event_values': event_values, 'event_evidence_snapshot_id': 79253,
            'copy_source_urls': sorted(SOURCE_URLS), 'copy_coauthors': coauthors,
            'earliest_retained_evidence': min(str(r['discovered_at']) for r in links.values()),
            'discovery_note': 'Original discovery timestamp is unknown; restored as NULL. '
                              'is_seed=True conservatively suppresses fresh announcements for this historical reconstruction.',
            'primary_evidence': PRIMARY_EVIDENCE}


def recover_merged(conn, evidence, *, apply=False, backup=None):
    if apply and not backup:
        raise ValueError('--apply requires --backup')
    conn.start_transaction()
    try:
        with conn.cursor(dictionary=True) as cursor:
            rows = read_recovery_rows(cursor)
            plan = recovery_plan(rows, evidence)
            if plan['already_restored'] or not apply:
                conn.rollback()
                return {'applied': False, 'plan': plan}
            cursor.execute('SELECT id FROM papers WHERE title_hash = %s FOR UPDATE', (RECOVERY_FIELDS['title_hash'],))
            if cursor.fetchall():
                raise ValueError('Another paper already holds the recovered title identity')
            backup_path = write_backup(backup, {'before': rows, 'reviewed_export': evidence, 'plan': plan})
            values = {'id': PAPER_ID, **RECOVERY_FIELDS}
            cursor.execute('INSERT INTO papers (' + ', '.join(values) + ') VALUES (' + ', '.join(['%s'] * len(values)) + ')', tuple(values.values()))
            for table, ids in (('paper_links', plan['move_link_ids']), ('paper_snapshots', plan['move_snapshot_ids'])):
                for row_id in ids:
                    cursor.execute(f'UPDATE {table} SET paper_id = %s WHERE id = %s AND paper_id = %s',
                                   (PAPER_ID, row_id, SURVIVOR_ID))
                    if cursor.rowcount != 1:
                        raise ValueError(f'{table} {row_id} changed during recovery')
            event = plan['event_values']
            cursor.execute('UPDATE feed_events SET paper_id = %s, event_venue = %s, event_source_url = %s, '
                           'event_metadata_captured = %s WHERE id = %s AND paper_id = %s',
                           (event['paper_id'], event['event_venue'], event['event_source_url'], True, 4698, SURVIVOR_ID))
            if cursor.rowcount != 1:
                raise ValueError('Event 4698 changed during recovery')
            for order, author_id in enumerate(AUTHOR_NAMES, 1):
                cursor.execute('INSERT INTO authorship (publication_id, researcher_id, author_order) VALUES (%s, %s, %s)',
                               (PAPER_ID, author_id, order))
            for url in plan['copy_source_urls']:
                cursor.execute('INSERT INTO paper_urls (paper_id, url, discovered_at) VALUES (%s, %s, UTC_TIMESTAMP())', (PAPER_ID, url))
            for author in plan['copy_coauthors']:
                cursor.execute('INSERT INTO openalex_coauthors (paper_id, display_name, openalex_author_id) VALUES (%s, %s, %s)',
                               (PAPER_ID, author['display_name'], author['openalex_author_id']))
        conn.commit()
        return {'applied': True, 'backup': str(backup_path), 'plan': plan}
    except Exception:
        conn.rollback()
        raise


def read_rows(cursor):
    rows = {}
    for table in ('papers', 'paper_links', 'openalex_coauthors', 'paper_topics', 'feed_events'):
        key = 'id' if table == 'papers' else 'paper_id'
        cursor.execute(f'SELECT * FROM {table} WHERE {key} = %s ORDER BY id FOR UPDATE', (PAPER_ID,))
        rows[table] = cursor.fetchall()
    cursor.execute('SELECT r.id, r.first_name, r.last_name FROM researchers r '
                   'JOIN authorship a ON a.researcher_id = r.id WHERE a.publication_id = %s '
                   'ORDER BY r.id FOR UPDATE', (PAPER_ID,))
    rows['paper_authors'] = cursor.fetchall()
    cursor.execute('SELECT * FROM researchers WHERE id IN (%s, %s, %s) ORDER BY id FOR UPDATE',
                   tuple(WRONG_HUBERS))
    rows['researchers'] = cursor.fetchall()
    return rows


def plan_changes(rows):
    if len(rows['papers']) != 1:
        raise ValueError('Verified paper 71119 is missing')
    paper = rows['papers'][0]
    if paper['id'] != PAPER_ID or normalize_title(paper['title']) != normalize_title(TITLE):
        raise ValueError('Paper identity changed; re-review before applying')
    actual_authors = {r['id']: (r['first_name'], r['last_name']) for r in rows['paper_authors']}
    if actual_authors != AUTHOR_NAMES:
        raise ValueError('Paper authors differ from the verified identities')
    if paper['status'] != 'published':
        raise ValueError('Publication status changed since the audit')
    before = {'doi': OLD_DOI, 'openalex_id': OLD_WORK_ID, 'year': '2024'}
    for key in CORRECTION:
        if paper.get(key) not in (before[key], CORRECTION[key]):
            raise ValueError(f'Paper {key} drifted from the audited or corrected value')
    updates = {key: value for key, value in CORRECTION.items() if paper[key] != value}
    wrong_links = [r for r in rows['paper_links'] if r['id'] == WRONG_LINK_ID]
    if wrong_links and any(r['paper_id'] != PAPER_ID or r['url'] != WRONG_LINK_URL
                           or r['doi'] != OLD_DOI for r in wrong_links):
        raise ValueError('Audited bad link changed; refusing deletion')
    clear_ids = []
    if {r['id'] for r in rows['researchers']} != set(WRONG_HUBERS):
        raise ValueError('A verified Huber researcher is missing')
    for row in rows['researchers']:
        if (row['first_name'], row['last_name']) != (WRONG_HUBERS[row['id']], 'Huber'):
            raise ValueError('Huber researcher name changed')
        if row['openalex_author_id'] not in (None, HUBER_ID):
            raise ValueError('Huber identifier was independently corrected; re-review')
        if row['openalex_author_id'] == HUBER_ID:
            clear_ids.append(row['id'])
    return {'paper_updates': updates, 'delete_link_ids': [r['id'] for r in wrong_links],
            'delete_topic_ids': [r['id'] for r in rows['paper_topics']] if paper['openalex_id'] == OLD_WORK_ID else [],
            'clear_researcher_ids': clear_ids, 'primary_evidence': PRIMARY_EVIDENCE}


def repair(conn, *, apply=False, backup=None):
    if apply and not backup:
        raise ValueError('--apply requires --backup')
    conn.start_transaction()
    try:
        with conn.cursor(dictionary=True) as cursor:
            rows = read_rows(cursor)
            plan = plan_changes(rows)
            if not apply or not any(plan[key] for key in
                                    ('paper_updates', 'delete_link_ids', 'delete_topic_ids', 'clear_researcher_ids')):
                conn.rollback()
                return {'applied': False, 'plan': plan}
            backup_path = write_backup(backup, {'before': rows, 'plan': plan})
            if plan['paper_updates']:
                values = plan['paper_updates']
                cursor.execute('UPDATE papers SET ' + ', '.join(f'{key} = %s' for key in values) + ' WHERE id = %s',
                               (*values.values(), PAPER_ID))
            for row_id in plan['delete_link_ids']:
                cursor.execute('DELETE FROM paper_links WHERE id = %s AND paper_id = %s', (row_id, PAPER_ID))
            for row_id in plan['delete_topic_ids']:
                cursor.execute('DELETE FROM paper_topics WHERE id = %s AND paper_id = %s', (row_id, PAPER_ID))
            for row_id in plan['clear_researcher_ids']:
                cursor.execute('UPDATE researchers SET openalex_author_id = NULL WHERE id = %s AND openalex_author_id = %s',
                               (row_id, HUBER_ID))
        conn.commit()
        return {'applied': True, 'backup': str(backup_path), 'plan': plan}
    except Exception:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    parser.add_argument('--recover-merged', type=Path, help='Reviewed complete survivor export for restoring missing paper 71119')
    args = parser.parse_args()
    from backend.database.connection import get_connection
    with get_connection() as conn:
        result = (recover_merged(conn, json.loads(args.recover_merged.read_text()), apply=args.apply, backup=args.backup)
                  if args.recover_merged else repair(conn, apply=args.apply, backup=args.backup))
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
