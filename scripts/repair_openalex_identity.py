"""Source-verified OpenAlex corrections, dry-run by default.

python scripts/repair_openalex_identity.py
python scripts/repair_openalex_identity.py --apply --backup /backups/openalex-identity.json

Only paper 71119 and three name-incompatible Huber IDs are writable. The
primary metadata below was checked on 2026-10-15 using free DOI/author lookups.
The abbreviated C Huber record is deliberately left for separate review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database.papers import normalize_title
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
    args = parser.parse_args()
    from backend.database.connection import get_connection
    with get_connection() as conn:
        print(json.dumps(repair(conn, apply=args.apply, backup=args.backup), indent=2, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
