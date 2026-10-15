"""Bounded Unicode identity repair, dry-run by default.

Export all evidence before repairing the contaminated paper 3733:
  python scripts/repair_unicode_identity.py --export /backups/unicode-3733.json
Then review the uniquely named bundle and plan:
  python scripts/repair_unicode_identity.py --bundle <export-path>
Apply only with --apply --backup /backups/unicode-3733-before.json.

The original verified Japanese article is restored. Other titles are archived in
full and returned as a source reconstruction worklist; authors are never guessed.
Use --rehash --after-id N --limit 500 to review bounded safe hash migrations;
--apply requires a backup for each batch. Conflicting histories are skipped.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database.papers import compute_title_hash, normalize_title, title_hash_candidates
from scripts.repair_verified_papers import write_backup

PAPER_ID = 3733
TITLE = '金銭的インセンティブとナッジが健康増進に及ぼす効果: フィールド実験によるエビデンス'
ENGLISH_TITLE = 'Causal Effect of Financial Incentive and Nudge on Health Promotion: Evidence by Field Experiments'
SOURCE = 'https://www.takunori-ishihara.com/research/'
PUBLISHER = 'https://www.jstage.jst.go.jp/article/jbef/11/0/11_132/_article/-char/en'
DOI = '10.11167/jbef.11.132'
AUTHORS = [('Takanori', 'Ida'), ('Takunori', 'Ishihara')]
TABLES = {
    'papers': 'id', 'authorship': 'publication_id', 'paper_urls': 'paper_id',
    'paper_links': 'paper_id', 'paper_snapshots': 'paper_id', 'feed_events': 'paper_id',
    'openalex_coauthors': 'paper_id', 'paper_topics': 'paper_id',
}


def _read(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def read_bundle(cursor, *, lock=False):
    suffix = ' FOR UPDATE' if lock else ''
    rows = {table: _read(cursor, f'SELECT * FROM {table} WHERE {key} = %s ORDER BY id' + suffix, (PAPER_ID,))
            for table, key in TABLES.items()}
    rows['feed_event_reviews'] = _read(cursor,
        'SELECT r.* FROM feed_event_reviews r JOIN feed_events e ON e.id = r.feed_event_id '
        'WHERE e.paper_id = %s ORDER BY r.id' + suffix, (PAPER_ID,))
    rows['researchers'] = _read(cursor,
        'SELECT r.* FROM researchers r JOIN authorship a ON a.researcher_id = r.id '
        'WHERE a.publication_id = %s ORDER BY r.id' + suffix, (PAPER_ID,))
    rows['verified_author_candidates'] = _read(cursor,
        'SELECT * FROM researchers WHERE (first_name = %s AND last_name = %s) '
        'OR (first_name = %s AND last_name = %s) ORDER BY id' + suffix, tuple(v for name in AUTHORS for v in name))
    sources = sorted({r['url'] for r in rows['paper_urls']} |
                     {r['source_url'] for r in rows['paper_snapshots'] if r.get('source_url')})
    tracked = _read(cursor, 'SELECT id, url, is_active FROM researcher_urls WHERE url IN (' +
                    ','.join(['%s'] * len(sources)) + ') ORDER BY id' + suffix, tuple(sources)) if sources else []
    by_id = {r['id']: r for r in tracked}
    cached = _read(cursor, 'SELECT * FROM html_content WHERE url_id IN (' +
                   ','.join(['%s'] * len(by_id)) + ') ORDER BY id' + suffix, tuple(by_id)) if by_id else []
    rows['source_queue'] = [{**r, 'url': by_id[r['url_id']]['url'], 'is_active': by_id[r['url_id']]['is_active']} for r in cached]
    return rows


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def original_plan(rows):
    if len(rows['papers']) != 1 or rows['papers'][0]['id'] != PAPER_ID:
        raise ValueError('Expected exactly the verified paper 3733')
    paper = rows['papers'][0]
    if normalize_title(paper['title']) != normalize_title(TITLE):
        raise ValueError('Canonical title changed; re-review identity before applying')
    authors = []
    for first, last in AUTHORS:
        matches = [r for r in rows['verified_author_candidates'] if (r['first_name'], r['last_name']) == (first, last)]
        if len(matches) != 1:
            raise ValueError(f'Expected one independently verified researcher {first} {last}; got {len(matches)}')
        authors.append(matches[0]['id'])
    keep_titles = {normalize_title(TITLE), normalize_title(ENGLISH_TITLE)}
    preserved_snapshots = [r['id'] for r in rows['paper_snapshots']
                           if normalize_title(r.get('title')) in keep_titles and r.get('source_url') in (SOURCE, PUBLISHER)]
    worklist = {}
    for row in rows['paper_snapshots']:
        if row['id'] not in preserved_snapshots:
            worklist.setdefault(row.get('source_url') or '', []).append({'snapshot_id': row['id'], 'title': row.get('title')})
    return {'paper_id': PAPER_ID, 'author_ids_in_order': authors, 'preserve_snapshot_ids': preserved_snapshots,
            'source_reconstruction_worklist': worklist,
            'queue_for_seed_extraction': [r['url_id'] for r in rows.get('source_queue', [])
                if r['is_active'] and r.get('content_hash') and (r.get('raw_html') or r.get('content'))],
            'verified_metadata': {'title': TITLE, 'title_hash': compute_title_hash(TITLE),
                'status': 'published', 'venue': 'Journal of Behavioral Economics and Finance',
                'year': '2019', 'doi': DOI, 'openalex_id': None, 'abstract': None,
                'source_url': SOURCE, 'draft_url': None, 'draft_url_status': 'unchecked',
                'draft_url_checked_at': None, 'enrichment_attempted_at': None},
            'primary_evidence': PUBLISHER}


def restore_original(conn, evidence, *, apply=False, backup=None):
    if apply and not backup:
        raise ValueError('--apply requires --backup')
    conn.start_transaction()
    try:
        with conn.cursor(dictionary=True) as cursor:
            rows = read_bundle(cursor, lock=True)
            plan = original_plan(rows)
            # A rerun after the scoped restoration must not rebuild or queue again.
            if (rows['papers'][0]['title_hash'] == compute_title_hash(TITLE)
                    and all(rows['papers'][0].get(k) == v for k, v in plan['verified_metadata'].items()
                            if k not in ('enrichment_attempted_at',))
                    and {r['researcher_id'] for r in rows['authorship']} == set(plan['author_ids_in_order'])
                    and not rows['feed_events']
                    and all(r['id'] in plan['preserve_snapshot_ids'] for r in rows['paper_snapshots'])):
                conn.rollback()
                return {'applied': False, 'already_restored': True, 'plan': plan}
            if _canonical(rows) != _canonical(evidence['rows']):
                raise ValueError('Scoped rows drifted since export; export and review again')
            clash = _read(cursor, 'SELECT id FROM papers WHERE title_hash = %s AND id != %s FOR UPDATE',
                          (compute_title_hash(TITLE), PAPER_ID))
            if clash:
                raise ValueError('Verified original already has another record; reconcile explicitly')
            if not apply:
                conn.rollback()
                return {'applied': False, 'plan': plan}
            backup_path = write_backup(backup, {'rows': rows, 'plan': plan, 'original_export': evidence})
            keep = plan['preserve_snapshot_ids']
            for table, key in TABLES.items():
                if table in ('papers', 'paper_snapshots'):
                    continue
                cursor.execute(f'DELETE FROM {table} WHERE {key} = %s', (PAPER_ID,))
            if keep:
                cursor.execute('DELETE FROM paper_snapshots WHERE paper_id = %s AND id NOT IN (' + ','.join(['%s'] * len(keep)) + ')', (PAPER_ID, *keep))
            else:
                cursor.execute('DELETE FROM paper_snapshots WHERE paper_id = %s', (PAPER_ID,))
            values = plan['verified_metadata']
            cursor.execute('UPDATE papers SET ' + ', '.join(f'{key} = %s' for key in values) + ' WHERE id = %s',
                           (*values.values(), PAPER_ID))
            for order, author_id in enumerate(plan['author_ids_in_order']):
                cursor.execute('INSERT INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, %s)', (author_id, PAPER_ID, order))
            for url in (SOURCE, PUBLISHER):
                cursor.execute('INSERT INTO paper_urls (paper_id, url, discovered_at) VALUES (%s, %s, UTC_TIMESTAMP())', (PAPER_ID, url))
            cursor.execute("INSERT INTO paper_links (paper_id, url, link_type, doi, discovered_at) VALUES (%s, %s, 'doi', %s, UTC_TIMESTAMP())", (PAPER_ID, 'https://doi.org/' + DOI, DOI))
            for source in rows.get('source_queue', []):
                if source['url_id'] not in plan['queue_for_seed_extraction']:
                    continue
                # Seed extraction bypasses the diff and suppresses historic announcements.
                cursor.execute('UPDATE html_content hc JOIN researcher_urls ru ON ru.id = hc.url_id '
                    'SET hc.extracted_hash = NULL, hc.extracted_at = NULL '
                    'WHERE hc.id = %s AND hc.content_hash <=> %s AND hc.extracted_hash <=> %s '
                    'AND hc.extracted_at <=> %s AND ru.is_active = TRUE',
                    (source['id'], source['content_hash'], source.get('extracted_hash'), source.get('extracted_at')))
                if cursor.rowcount != 1 and (source.get('extracted_hash') is not None or source.get('extracted_at') is not None):
                    raise ValueError('Source changed while queueing reconstruction')
            conn.commit()
            return {'applied': True, 'backup': backup_path, 'plan': plan}
    except Exception:
        conn.rollback()
        raise


def rehash_batch(conn, *, after_id=0, limit=500, apply=False, backup=None):
    if not 1 <= limit <= 1000 or (apply and not backup):
        raise ValueError('Limit must be 1..1000 and apply requires backup')
    conn.start_transaction()
    try:
        with conn.cursor(dictionary=True) as cursor:
            rows = _read(cursor, 'SELECT id, title, title_hash FROM papers WHERE id > %s ORDER BY id LIMIT %s FOR UPDATE', (after_id, limit))
            plans, skipped, snapshots = [], [], {}
            for row in rows:
                try:
                    new_hash = compute_title_hash(row['title'])
                except ValueError:
                    skipped.append({'id': row['id'], 'reason': 'non-discriminative title'})
                    continue
                if new_hash == row['title_hash']:
                    continue
                history = _read(cursor, 'SELECT id, title FROM paper_snapshots WHERE paper_id = %s ORDER BY id FOR UPDATE', (row['id'],))
                snapshots[str(row['id'])] = history
                reason = None
                if row['id'] == PAPER_ID or row['title_hash'] not in title_hash_candidates(row['title']):
                    reason = 'unsafe or unknown legacy hash; requires scoped identity review'
                elif any(r['title'] and normalize_title(r['title']) != normalize_title(row['title']) for r in history):
                    reason = 'different historical titles; do not infer shared identity'
                elif _read(cursor, 'SELECT id FROM papers WHERE title_hash = %s AND id != %s FOR UPDATE', (new_hash, row['id'])) or any(p['new_hash'] == new_hash for p in plans):
                    reason = 'new hash collision; requires validated merge'
                if reason:
                    skipped.append({'id': row['id'], 'reason': reason})
                else:
                    plans.append({'id': row['id'], 'old_hash': row['title_hash'], 'new_hash': new_hash})
            result = {'plans': plans, 'skipped': skipped, 'after_id': rows[-1]['id'] if rows else after_id, 'applied': False}
            if not apply or not plans:
                conn.rollback()
                return result
            result['backup'] = write_backup(backup, {'rows': rows, 'snapshots': snapshots, 'plan': result})
            for plan in plans:
                cursor.execute('UPDATE papers SET title_hash = %s WHERE id = %s AND title_hash = %s', (plan['new_hash'], plan['id'], plan['old_hash']))
                if cursor.rowcount != 1:
                    raise ValueError('Hash changed during migration')
            conn.commit()
            result['applied'] = True
            return result
    except Exception:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--export', type=Path)
    mode.add_argument('--bundle', type=Path)
    mode.add_argument('--rehash', action='store_true')
    parser.add_argument('--after-id', type=int, default=0)
    parser.add_argument('--limit', type=int, default=500)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.apply and (not args.backup or args.export):
        parser.error('--apply requires --backup and cannot be used with --export')
    from backend.database.connection import get_connection
    with get_connection() as conn:
        if args.export:
            conn.start_transaction(consistent_snapshot=True)
            with conn.cursor(dictionary=True) as cursor:
                rows = read_bundle(cursor)
            conn.rollback()
            result = {'export': write_backup(args.export, {'rows': rows, 'primary_evidence': PUBLISHER})}
        elif args.rehash:
            result = rehash_batch(conn, after_id=args.after_id, limit=args.limit, apply=args.apply, backup=args.backup)
        else:
            result = restore_original(conn, json.loads(args.bundle.read_text()), apply=args.apply, backup=args.backup)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
