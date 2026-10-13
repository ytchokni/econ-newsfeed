"""Apply the seven source-verified October 2026 paper repairs, dry-run by default.

python scripts/repair_verified_papers.py --manifest /tmp/repair-candidates.json \
    --evidence /tmp/repair-current-evidence.json
Add --apply --backup /backups/verified-papers.json to write. The backup filename
receives a unique suffix; an existing backup is never replaced.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

VERIFIED_PAPERS = {362530, 270069, 67583, 201563, 372003, 343259, 266529}
TABLE_KEYS = {
    'papers': 'id', 'paper_snapshots': 'paper_id', 'feed_events': 'paper_id',
    'authorship': 'publication_id', 'paper_urls': 'paper_id', 'paper_links': 'paper_id',
}
EVIDENCE_KEYS = {
    'papers': 'paper', 'paper_snapshots': 'snapshots', 'feed_events': 'events',
    'authorship': 'authorship', 'paper_urls': 'sources',
}
UPDATE_COLUMNS = {
    'papers': {'status', 'venue', 'year', 'abstract', 'doi', 'source_url'},
    'paper_snapshots': {'status', 'venue'},
    'feed_events': {'event_venue', 'event_source_url', 'event_metadata_captured'},
    'authorship': {'researcher_id', 'author_order'},
}
DELETE_TABLES = {'paper_snapshots', 'feed_events', 'authorship', 'paper_urls'}
INSERT_COLUMNS = {
    'authorship': {'publication_id', 'researcher_id', 'author_order'},
    'paper_urls': {'paper_id', 'url', 'discovered_at'},
}
TARGET_AUTHORS = {
    3515: ('David', 'Lagakos'), 3517: ('Todd', 'Schoellman'),
    43637: ('Manav', 'Chaudhary'), 61811: ('Julie Zhiyu', 'Fu'), 35307: ('Jian', 'Li'),
}
EVENT_DEFAULTS = {'event_venue': None, 'event_source_url': None, 'event_metadata_captured': False}
WEBB = {'first_name': 'David', 'last_name': 'Webb',
        'affiliation': 'London School of Economics and Political Science'}
WEBB_URL = 'https://www.fmg.ac.uk/people/david-webb'


class RepairRefused(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RepairRefused(message)


def same(actual, expected):
    if isinstance(actual, datetime):
        actual = str(actual)
    return actual == expected


def validate_manifest(manifest, evidence):
    candidates = manifest['candidates']
    ids = [c['paper_id'] for c in candidates]
    require(ids and len(set(ids)) == len(ids) and set(ids) <= VERIFIED_PAPERS,
            'Manifest must contain unique, explicitly verified paper IDs.')
    require(set(manifest['scope_paper_ids']) == set(ids), 'Manifest scope differs from candidates.')
    for candidate in candidates:
        pid = candidate['paper_id']
        require(str(pid) in evidence['papers'], f'Missing before-images for {pid}.')
        for op in candidate['operations']:
            kind, table = op['operation'], op.get('table')
            if kind in ('update', 'delete'):
                allowed = UPDATE_COLUMNS if kind == 'update' else DELETE_TABLES
                require(table in allowed, f'Unsupported {kind} table: {table}')
                before = next((r for r in evidence['papers'][str(pid)][EVIDENCE_KEYS[table]]
                               if r['id'] == op['where'].get('id')), None)
                require(before is not None, f'No audited before-image for {table} {op["where"]}.')
                require(before[TABLE_KEYS[table]] == pid, 'Operation crosses its verified paper scope.')
                require(set(op['where']) <= set(before) | set(EVENT_DEFAULTS), 'Unknown predicate column.')
                for key, value in op['where'].items():
                    require(same(before.get(key, EVENT_DEFAULTS.get(key)), value),
                            f'Manifest predicate differs from audited {table}.{key}.')
                if kind == 'update':
                    require(op['set'] and set(op['set']) <= UPDATE_COLUMNS[table], 'Unsupported update columns.')
            elif kind == 'insert_if_absent':
                require(table in INSERT_COLUMNS, f'Unsupported insert table: {table}')
                require(op['rows'], 'Empty insertion.')
                for row in op['rows']:
                    require(set(row) == INSERT_COLUMNS[table], 'Unsupported insertion columns.')
                    require(row[TABLE_KEYS[table]] == pid, 'Insertion crosses its verified paper scope.')
            elif kind == 'create_researcher_if_absent_then_reassign_authorship':
                require(pid == 372003 and op['researcher'] == WEBB and
                        op['verified_identity_url'] == WEBB_URL and op['set_author_order'] == 0,
                        'Only the verified David Webb insertion is supported.')
                before = next((r for r in evidence['papers'][str(pid)]['authorship']
                               if r['id'] == op['authorship_where'].get('id')), None)
                require(before and all(same(before.get(k), v) for k, v in op['authorship_where'].items()),
                        'David Webb reassignment lacks its audited authorship.')
                require(set(op['authorship_where']) == {'id', 'publication_id', 'researcher_id'},
                        'Unsupported authorship predicate.')
            else:
                raise RepairRefused(f'Unsupported operation: {kind}')
    return sorted(ids)


def select(cursor, sql, params):
    cursor.execute(sql, params)
    return cursor.fetchall()


def lock_rows(cursor, ids, evidence, manifest):
    placeholders = ', '.join(['%s'] * len(ids))
    rows = {}
    for table, key in TABLE_KEYS.items():
        rows[table] = select(cursor, f'SELECT * FROM {table} WHERE {key} IN ({placeholders}) ORDER BY id FOR UPDATE', tuple(ids))
    require({r['id'] for r in rows['papers']} == set(ids), 'A scoped paper no longer exists.')
    rows['feed_event_reviews'] = select(cursor,
        f'SELECT r.* FROM feed_event_reviews r JOIN feed_events e ON e.id = r.feed_event_id '
        f'WHERE e.paper_id IN ({placeholders}) ORDER BY r.id FOR UPDATE', tuple(ids))
    names = {}
    for pid in ids:
        for row in evidence['papers'][str(pid)]['authorship']:
            names[row['researcher_id']] = (row['first_name'], row['last_name'])
    for candidate in manifest['candidates']:
        for op in candidate['operations']:
            if op.get('table') == 'authorship':
                targets = [op['set']['researcher_id']] if op['operation'] == 'update' and 'researcher_id' in op['set'] else []
                targets += [r['researcher_id'] for r in op.get('rows', [])]
                require(all(rid in TARGET_AUTHORS for rid in targets), 'Unverified target researcher ID.')
                for rid in targets:
                    require(rid not in names or names[rid] == TARGET_AUTHORS[rid], 'Target name conflicts with audited identity.')
                    names[rid] = TARGET_AUTHORS[rid]
    author_ids = sorted(names)
    rows['researchers'] = select(cursor,
        f'SELECT * FROM researchers WHERE id IN ({", ".join(["%s"] * len(author_ids))}) ORDER BY id FOR UPDATE', tuple(author_ids))
    actual_authors = {r['id']: r for r in rows['researchers']}
    for rid, name in names.items():
        row = actual_authors.get(rid)
        require(row and (row['first_name'], row['last_name']) == name,
                f'Researcher {rid} no longer matches verified name {name}.')
    rows['webb_candidates'] = []
    rows['webb_urls'] = []
    if 372003 in ids:
        rows['webb_candidates'] = select(cursor,
            'SELECT * FROM researchers WHERE last_name = %s ORDER BY id FOR UPDATE', ('Webb',))
        matches = [r for r in rows['webb_candidates']
                   if r['first_name'].casefold().startswith('david') or
                   r['first_name'].casefold().replace('.', '').replace(' ', '') in {'d', 'dc'}]
        require(len(matches) <= 1, 'Multiple possible David Webb identities; resolve before applying.')
        if matches:
            row = matches[0]
            require(all(row.get(k) == v for k, v in WEBB.items()), 'Existing David Webb identity is not verified.')
            rows['webb_urls'] = select(cursor,
                'SELECT * FROM researcher_urls WHERE researcher_id = %s ORDER BY id FOR UPDATE', (row['id'],))
            require(all(r['url'] == WEBB_URL for r in rows['webb_urls']), 'David Webb has unverified existing URLs.')
        rows['verified_webb'] = matches
    return rows


def plan_changes(manifest, evidence, rows):
    from backend.database.snapshots import _compute_paper_content_hash
    current = {table: {r['id']: r for r in values} for table, values in rows.items()
               if table in TABLE_KEYS}
    plans = []
    for candidate in manifest['candidates']:
        pid = candidate['paper_id']
        for op in candidate['operations']:
            kind, table = op['operation'], op.get('table')
            if kind in ('update', 'delete'):
                rid = op['where']['id']
                before = next(r for r in evidence['papers'][str(pid)][EVIDENCE_KEYS[table]] if r['id'] == rid)
                row = current[table].get(rid)
                if kind == 'delete':
                    if row is None:
                        continue
                    require(all(same(row[k], v) for k, v in before.items() if k in row),
                            f'{table} {rid} changed since audit; refusing deletion.')
                    plans.append({'kind': 'delete', 'table': table, 'id': rid})
                    continue
                require(row is not None, f'{table} {rid} is missing.')
                values = dict(op['set'])
                for key, value in op['where'].items():
                    if key not in values:
                        require(same(row.get(key), value), f'{table} {rid} predicate {key} changed.')
                if table == 'paper_snapshots':
                    merged = {**row, **values}
                    values['content_hash'] = _compute_paper_content_hash(
                        merged['status'], merged['venue'], merged['abstract'], merged['draft_url'],
                        merged['year'], title=merged['title'])
                require(set(values) <= set(row), f'{table} lacks required columns; deploy migration first.')
                if all(same(row[k], v) for k, v in values.items()):
                    continue
                require(all(same(row[k], before.get(k, EVENT_DEFAULTS.get(k))) for k in values),
                        f'{table} {rid} changed in a field this repair would overwrite.')
                if table == 'authorship' and 'researcher_id' in values:
                    require(not any(r['id'] != rid and r['publication_id'] == pid and
                                    r['researcher_id'] == values['researcher_id'] for r in rows[table]),
                            f'Paper {pid} already links the target author.')
                plans.append({'kind': 'update', 'table': table, 'id': rid, 'values': values})
            elif kind == 'insert_if_absent':
                for value in op['rows']:
                    keys = ('publication_id', 'researcher_id') if table == 'authorship' else ('paper_id', 'url')
                    matches = [r for r in rows[table] if all(r[k] == value[k] for k in keys)]
                    require(len(matches) <= 1, f'Duplicate existing {table} identities.')
                    if matches:
                        require(table != 'authorship' or matches[0]['author_order'] == value['author_order'],
                                'Existing author order differs from verified order.')
                    else:
                        plans.append({'kind': 'insert', 'table': table, 'values': value})
            else:
                rid = op['authorship_where']['id']
                row = current['authorship'].get(rid)
                require(row is not None and row['publication_id'] == pid, 'David Webb authorship is missing or moved.')
                webb = rows['verified_webb'][0] if rows['verified_webb'] else None
                if webb and row['researcher_id'] == webb['id'] and row['author_order'] == 0:
                    continue
                before = next(r for r in evidence['papers'][str(pid)]['authorship'] if r['id'] == rid)
                require(row['researcher_id'] == before['researcher_id'] and row['author_order'] == before['author_order'],
                        'David Webb authorship changed since audit.')
                require(not webb or not any(r['publication_id'] == pid and r['researcher_id'] == webb['id']
                                            for r in rows['authorship']), 'Paper already links David Webb.')
                plans.append({'kind': 'webb', 'id': rid, 'researcher_id': webb['id'] if webb else None})
    return plans


def write_backup(path, contents):
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = path.with_name(f'{path.stem}.{stamp}.{uuid.uuid4().hex}.json')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.paper-repair-', delete=False) as f:
            temporary = Path(f.name)
            json.dump(contents, f, ensure_ascii=False, indent=2, default=str)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.link(temporary, target)  # Atomic publication without ever replacing another backup.
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return str(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def mutate(cursor, plans):
    for plan in plans:
        kind, table = plan['kind'], plan.get('table')
        if kind == 'delete':
            cursor.execute(f'DELETE FROM {table} WHERE id = %s', (plan['id'],))
        elif kind == 'update':
            values = plan['values']
            cursor.execute(f'UPDATE {table} SET {", ".join(k + " = %s" for k in values)} WHERE id = %s',
                           (*values.values(), plan['id']))
        elif kind == 'insert':
            values = plan['values']
            if table == 'paper_urls':
                cursor.execute('INSERT INTO paper_urls (paper_id, url, discovered_at) VALUES (%s, %s, UTC_TIMESTAMP())',
                               (values['paper_id'], values['url']))
            else:
                cursor.execute('INSERT INTO authorship (publication_id, researcher_id, author_order) VALUES (%s, %s, %s)',
                               (values['publication_id'], values['researcher_id'], values['author_order']))
        else:
            researcher_id = plan['researcher_id']
            if researcher_id is None:
                cursor.execute('INSERT INTO researchers (first_name, last_name, affiliation) VALUES (%s, %s, %s)',
                               tuple(WEBB.values()))
                researcher_id = cursor.lastrowid
            cursor.execute('UPDATE authorship SET researcher_id = %s, author_order = %s WHERE id = %s',
                           (researcher_id, 0, plan['id']))
        require(cursor.rowcount == 1, f'Unexpected affected row count for {plan}.')


def run_repair(conn, manifest, evidence, *, apply=False, backup=None):
    require(not apply or backup, '--apply requires --backup.')
    ids = validate_manifest(manifest, evidence)
    conn.start_transaction()
    try:
        with conn.cursor(dictionary=True) as cursor:
            rows = lock_rows(cursor, ids, evidence, manifest)
            plans = plan_changes(manifest, evidence, rows)
            result = {'apply': apply, 'planned_operations': len(plans), 'paper_ids': ids,
                      'operations': plans, 'backup': None, 'applied_operations': 0}
            if not apply or not plans:
                conn.rollback()
                return result
            result['backup'] = write_backup(backup, {
                'kind': 'pre-apply backup', 'created_at': datetime.now(timezone.utc).isoformat(),
                'manifest': manifest, 'audited_before_images': evidence, 'locked_current_rows': rows,
                'planned_operations': plans,
            })
            mutate(cursor, plans)
            conn.commit()
            result['applied_operations'] = len(plans)
            return result
    except Exception:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--evidence', type=Path, help='Defaults to repair-current-evidence.json beside manifest.')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path, help='Required with --apply; a unique backup filename is derived from this path.')
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error('--apply requires --backup')
    evidence_path = args.evidence or args.manifest.with_name('repair-current-evidence.json')
    manifest = json.loads(args.manifest.read_text())
    evidence = json.loads(evidence_path.read_text())
    from backend.database.connection import get_connection
    with get_connection() as conn:
        result = run_repair(conn, manifest, evidence, apply=args.apply, backup=args.backup)
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
