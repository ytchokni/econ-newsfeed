"""Plan or apply only duplicate new-paper feed announcement deletions.

    poetry run python scripts/repair_feed_history.py --plan /tmp/feed-repair.json
    poetry run python scripts/repair_feed_history.py --apply /tmp/feed-repair.json

The default is a read-only, consistent-snapshot plan. Plans contain complete
before-images of affected papers, announcements, and review rows (the latter cascade
when an event is removed). Apply re-reads and locks every planned row, checks the
database identity and exact before-images, then applies all deletions together.
Paper metadata and historical status records are never modified.
No SQL, table names, or column names are accepted from the plan.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database.connection import get_connection  # noqa: E402

FORMAT = "econ-newsfeed-duplicate-announcements-v1"
RECORD_KEYS = {"paper_before", "events_before", "reviews_before", "actions"}


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported before-image value: {type(value).__name__}")


def _json_value(value):
    return json.loads(json.dumps(value, default=_json_default, ensure_ascii=False))


def _id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError("Plan IDs must be positive integers")
    return value


def _identity(cursor):
    cursor.execute("SELECT DATABASE() AS database_name, @@server_uuid AS server_uuid")
    identity = cursor.fetchone()
    if not identity or not identity.get("database_name") or not identity.get("server_uuid"):
        raise ValueError("Could not establish database identity")
    return identity


def _actions(events):
    new_events = sorted(
        (row for row in events if row['event_type'] == 'new_paper'),
        key=lambda row: (row['created_at'], row['id']),
    )
    return {
        "keep_new_paper_event_id": new_events[0]['id'] if new_events else None,
        "delete_new_paper_event_ids": [row['id'] for row in new_events[1:]],
    }


def _read_record(cursor, paper, *, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    cursor.execute("SELECT * FROM feed_events WHERE paper_id = %s AND event_type = 'new_paper' ORDER BY id" + suffix, (paper['id'],))
    events = cursor.fetchall()
    reviews = []
    if events:
        placeholders = ','.join(['%s'] * len(events))
        cursor.execute(
            f"SELECT * FROM feed_event_reviews WHERE feed_event_id IN ({placeholders}) ORDER BY id" + suffix,
            tuple(row['id'] for row in events),
        )
        reviews = cursor.fetchall()
    return _json_value({
        "paper_before": paper,
        "events_before": events,
        "reviews_before": reviews,
        "actions": _actions(events),
    })


def _candidate_ids(cursor):
    cursor.execute(
        """SELECT paper_id AS id FROM feed_events
            WHERE event_type = 'new_paper'
            GROUP BY paper_id HAVING COUNT(*) > 1
            ORDER BY id""",
    )
    return [_id(row['id']) for row in cursor.fetchall()]


def create_plan(conn):
    """Read a plan without modifying any database row."""
    conn.start_transaction(isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
    cursor = conn.cursor(dictionary=True)
    try:
        plan = {"format": FORMAT, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "database": _identity(cursor), "repairs": []}
        for paper_id in _candidate_ids(cursor):
            cursor.execute("SELECT * FROM papers WHERE id = %s", (paper_id,))
            paper = cursor.fetchone()
            if paper is None:
                raise ValueError(f"Paper {paper_id} disappeared during planning")
            plan['repairs'].append(_read_record(cursor, paper))
        return plan
    finally:
        conn.rollback()
        cursor.close()


def validate_plan(plan):
    """Validate structure and recompute fixed actions from the before-images."""
    if not isinstance(plan, dict) or set(plan) != {"format", "created_at_utc", "database", "repairs"}:
        raise ValueError("Unexpected plan fields")
    if plan['format'] != FORMAT or not isinstance(plan['created_at_utc'], str):
        raise ValueError("Unsupported plan format")
    if not isinstance(plan['database'], dict) or set(plan['database']) != {'database_name', 'server_uuid'}:
        raise ValueError("Missing database identity")
    if not isinstance(plan['repairs'], list):
        raise ValueError("Invalid repair list")
    seen = set()
    for record in plan['repairs']:
        if not isinstance(record, dict) or set(record) != RECORD_KEYS:
            raise ValueError("Unexpected repair fields")
        paper_id = _id(record['paper_before']['id'])
        if paper_id in seen:
            raise ValueError("Duplicate paper in plan")
        seen.add(paper_id)
        for key in ('events_before', 'reviews_before'):
            if not isinstance(record[key], list):
                raise ValueError("Invalid before-image list")
            row_ids = [_id(row['id']) for row in record[key]]
            if len(row_ids) != len(set(row_ids)):
                raise ValueError("Duplicate before-image ID")
        for row in record['events_before']:
            if _id(row['paper_id']) != paper_id:
                raise ValueError("History belongs to another paper")
            if row['event_type'] != 'new_paper':
                raise ValueError("Only new_paper announcements can be repaired")
        expected = _actions(record['events_before'])
        if record['actions'] != expected:
            raise ValueError("Plan actions do not match before-images")
        if not expected['delete_new_paper_event_ids']:
            raise ValueError("Plan contains a paper with no repair")
    return sorted(seen)


def apply_plan(conn, plan):
    """Validate all locked before-images, then apply just the planned actions."""
    paper_ids = validate_plan(plan)
    conn.start_transaction(isolation_level="REPEATABLE READ")
    cursor = conn.cursor(dictionary=True)
    try:
        if _identity(cursor) != plan['database']:
            raise ValueError("Plan belongs to a different database")
        records = {record['paper_before']['id']: record for record in plan['repairs']}
        # Lock all parents in ID order first. FK checks prevent new history rows
        # being inserted while their parents are held exclusively.
        papers = {}
        for paper_id in paper_ids:
            cursor.execute("SELECT * FROM papers WHERE id = %s FOR UPDATE", (paper_id,))
            paper = cursor.fetchone()
            if paper is None:
                raise ValueError(f"Paper {paper_id} no longer exists")
            papers[paper_id] = paper
        for paper_id in paper_ids:
            current = _read_record(cursor, papers[paper_id], lock=True)
            if current != records[paper_id]:
                raise ValueError(f"Paper {paper_id} or its history changed; create a fresh plan")

        deleted = 0
        for paper_id in paper_ids:
            actions = records[paper_id]['actions']
            for event_id in actions['delete_new_paper_event_ids']:
                cursor.execute(
                    "DELETE FROM feed_events WHERE id = %s AND paper_id = %s AND event_type = 'new_paper'",
                    (event_id, paper_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"Unexpected deletion count for event {event_id}")
                deleted += 1
        conn.commit()
        return {"duplicate_events_deleted": deleted}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan', type=Path, help='Write a read-only repair plan (default: timestamped local JSON)')
    mode.add_argument('--apply', type=Path, help='Apply exactly the reviewed JSON plan')
    args = parser.parse_args()
    if args.apply:
        plan = json.loads(args.apply.read_text())
        validate_plan(plan)
        with get_connection() as conn:
            result = apply_plan(conn, plan)
        print(json.dumps(result))
    else:
        with get_connection() as conn:
            plan = create_plan(conn)
        destination = args.plan or Path(datetime.now(timezone.utc).strftime('feed-history-plan-%Y%m%dT%H%M%SZ.json'))
        # Never replace an earlier plan containing backup before-images.
        with destination.open('x') as output:
            destination.chmod(0o600)
            json.dump(plan, output, indent=2, ensure_ascii=False)
            output.write('\n')
        print(json.dumps({"plan": str(destination.resolve()), "papers": len(plan['repairs']),
                          "duplicate_events_to_delete": sum(len(r['actions']['delete_new_paper_event_ids']) for r in plan['repairs'])}))


if __name__ == '__main__':
    main()
