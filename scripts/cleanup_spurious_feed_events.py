"""Delete feed events the pipeline no longer emits (issues #261 and #264).

- new_paper events for papers that were not new: seed papers, papers already
  known for more than a week before the event, and papers dated more than two
  years before the event (#261).
- status_change events to accepted/published without a journal venue (blog
  posts, policy briefs, working-paper series), "published" events for items
  dated more than FEED_PUBLISHED_MAX_AGE_YEARS (default 1) before the event,
  and other forward transitions for items more than ten years old (#264).

The rules are the ones the pipeline applies when emitting events
(backend.publication_rules), so a re-run after --apply finds nothing.

Dry run by default; pass --apply to delete. Deleting an event cascades to
digest_deliveries and feed_event_reviews, so --apply first copies the affected
rows of all three tables into <table>_cleanup_backup, tagged with a run id, in
the same transaction as the delete. --undo RUN restores a run.

    poetry run python scripts/cleanup_spurious_feed_events.py
    poetry run python scripts/cleanup_spurious_feed_events.py --apply
    poetry run python scripts/cleanup_spurious_feed_events.py --before 2026-11-28 --published-max-age 2
    poetry run python scripts/cleanup_spurious_feed_events.py --list-runs
    poetry run python scripts/cleanup_spurious_feed_events.py --undo 20261128T101500Z
"""
import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.publication_rules import (  # noqa: E402
    new_paper_suppression_reason,
    status_change_suppression_reason,
)

NEW_PAPER_SQL = """
    SELECT fe.id, fe.event_type, fe.created_at, fe.new_status,
           p.is_seed, p.discovered_at, p.year, LEFT(p.title, 80) AS title
    FROM feed_events fe
    JOIN papers p ON p.id = fe.paper_id
    WHERE fe.event_type = 'new_paper'
"""

# Events recorded before event_metadata_captured existed have no stored venue;
# the paper's current venue is the best evidence for them.
STATUS_CHANGE_SQL = """
    SELECT fe.id, fe.event_type, fe.created_at, fe.old_status, fe.new_status,
           CASE WHEN fe.event_metadata_captured THEN fe.event_venue ELSE p.venue END AS venue,
           p.year, LEFT(p.title, 80) AS title
    FROM feed_events fe
    JOIN papers p ON p.id = fe.paper_id
    WHERE fe.event_type = 'status_change'
"""

BATCH = 500

# (table, column holding the feed_events id). Parent first.
BACKED_UP_TABLES = (
    ("feed_events", "id"),
    ("digest_deliveries", "event_id"),
    ("feed_event_reviews", "feed_event_id"),
)

# Restoring a child row needs its other parents to still exist.
_RESTORE_GUARDS = {
    "feed_events": "paper_id IN (SELECT id FROM papers)",
    "digest_deliveries": "event_id IN (SELECT id FROM feed_events) AND user_id IN (SELECT id FROM users)",
    "feed_event_reviews": "feed_event_id IN (SELECT id FROM feed_events)",
}


def backup_table(table: str) -> str:
    return f"{table}_cleanup_backup"


def _table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return cursor.fetchone()[0] > 0


def _columns(cursor, table: str) -> list[str]:
    """Insertable columns. Not EXTRA LIKE '%GENERATED%': MySQL 8 reports
    DEFAULT CURRENT_TIMESTAMP columns as DEFAULT_GENERATED."""
    cursor.execute(
        """SELECT COLUMN_NAME FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
             AND COALESCE(GENERATION_EXPRESSION, '') = ''
           ORDER BY ORDINAL_POSITION""",
        (table,),
    )
    return [r[0] for r in cursor.fetchall()]


def copied_columns(source_cols: list[str], backup_cols: list[str]) -> list[str]:
    """Source columns to copy; fails if the backup table lacks one (schema drift)."""
    missing = [c for c in source_cols if c not in backup_cols]
    if missing:
        raise RuntimeError(
            f"backup table lacks columns {missing}; add them to the backup table before running"
        )
    return source_cols


def ensure_backup_tables(cursor) -> list[tuple[str, str, list[str]]]:
    """Create the backup tables if needed. Returns (table, id column, columns) to copy."""
    tables = []
    for table, id_col in BACKED_UP_TABLES:
        if not _table_exists(cursor, table):
            continue
        backup = backup_table(table)
        # LIKE copies columns and keys but not foreign keys, so backups survive
        # deletes of the papers and users they point at.
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {backup} LIKE {table}")
        backup_cols = _columns(cursor, backup)
        if "cleanup_run" not in backup_cols:
            cursor.execute(
                f"""ALTER TABLE {backup}
                    ADD COLUMN cleanup_run VARCHAR(32) NOT NULL DEFAULT '',
                    ADD COLUMN cleanup_reason VARCHAR(255) NULL,
                    ADD COLUMN backed_up_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    ADD INDEX idx_cleanup_run (cleanup_run)"""
            )
            backup_cols = _columns(cursor, backup)
        tables.append((table, id_col, copied_columns(_columns(cursor, table), backup_cols)))
    return tables


def _chunks(ids: list[int]):
    for i in range(0, len(ids), BATCH):
        yield ids[i:i + BATCH]


def backup_and_delete(conn, spurious: list[tuple[dict, str]], run_id: str) -> dict[str, int]:
    """Copy the events and their dependent rows into the backup tables, then delete.

    The copies and the delete commit together, so a failure leaves both untouched.
    """
    cursor = conn.cursor()
    tables = ensure_backup_tables(cursor)  # DDL commits implicitly; do it first
    counts: dict[str, int] = {}
    try:
        by_reason: dict[str, list[int]] = {}
        for row, reason in spurious:
            by_reason.setdefault(reason[:255], []).append(row["id"])
        for table, id_col, cols in tables:
            col_list = ", ".join(cols)
            counts[table] = 0
            for reason, ids in by_reason.items():
                for chunk in _chunks(ids):
                    cursor.execute(
                        f"""INSERT INTO {backup_table(table)} ({col_list}, cleanup_run, cleanup_reason)
                            SELECT {col_list}, %s, %s FROM {table}
                            WHERE {id_col} IN ({','.join(['%s'] * len(chunk))})""",
                        (run_id, reason, *chunk),
                    )
                    counts[table] += cursor.rowcount
        deleted = 0
        for chunk in _chunks([row["id"] for row, _ in spurious]):
            cursor.execute(
                f"DELETE FROM feed_events WHERE id IN ({','.join(['%s'] * len(chunk))})",
                tuple(chunk),
            )
            deleted += cursor.rowcount
        if deleted != counts.get("feed_events"):
            raise RuntimeError(f"backed up {counts.get('feed_events')} events but deleted {deleted}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
    return counts


def undo_run(conn, run_id: str) -> dict[str, int]:
    """Restore a cleanup run's rows (parent first) and drop restored rows from the backups.

    Events go back one at a time: the feed_events snapshot-guard trigger can
    refuse a new_paper event whose source URL has since lost its snapshots, and
    such events stay in the backup (reported as "kept") instead of failing the
    whole restore. Dependent rows follow only for restored events.
    """
    from mysql.connector import errors

    cursor = conn.cursor()
    result: dict[str, int] = {}
    try:
        events_backup = backup_table("feed_events")
        if not _table_exists(cursor, events_backup):
            return result
        cols = copied_columns(_columns(cursor, "feed_events"), _columns(cursor, events_backup))
        col_list = ", ".join(cols)
        cursor.execute(
            f"SELECT id FROM {events_backup} WHERE cleanup_run = %s AND {_RESTORE_GUARDS['feed_events']}",
            (run_id,),
        )
        restored_ids = []
        for (event_id,) in cursor.fetchall():
            try:
                cursor.execute(
                    f"""INSERT INTO feed_events ({col_list})
                        SELECT {col_list} FROM {events_backup} WHERE cleanup_run = %s AND id = %s""",
                    (run_id, event_id),
                )
                restored_ids.append(event_id)
            except errors.DatabaseError as exc:
                if exc.errno != 1644:  # SIGNAL from the snapshot-guard trigger
                    raise
        result["feed_events"] = len(restored_ids)

        for table, id_col in BACKED_UP_TABLES[1:]:
            backup = backup_table(table)
            if not (restored_ids and _table_exists(cursor, table) and _table_exists(cursor, backup)):
                continue
            cols = copied_columns(_columns(cursor, table), _columns(cursor, backup))
            col_list = ", ".join(cols)
            result[table] = 0
            for chunk in _chunks(restored_ids):
                marks = ",".join(["%s"] * len(chunk))
                cursor.execute(
                    f"""INSERT INTO {table} ({col_list})
                        SELECT {col_list} FROM {backup}
                        WHERE cleanup_run = %s AND {id_col} IN ({marks}) AND {_RESTORE_GUARDS[table]}""",
                    (run_id, *chunk),
                )
                result[table] += cursor.rowcount
                cursor.execute(
                    f"DELETE FROM {backup} WHERE cleanup_run = %s AND {id_col} IN ({marks})",
                    (run_id, *chunk),
                )
        for chunk in _chunks(restored_ids):
            cursor.execute(
                f"DELETE FROM {events_backup} WHERE cleanup_run = %s AND id IN ({','.join(['%s'] * len(chunk))})",
                (run_id, *chunk),
            )
        cursor.execute(f"SELECT COUNT(*) FROM {events_backup} WHERE cleanup_run = %s", (run_id,))
        result["kept_in_backup"] = cursor.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
    return result


def list_runs(conn) -> list[tuple]:
    cursor = conn.cursor()
    try:
        backup = backup_table("feed_events")
        if not _table_exists(cursor, backup):
            return []
        cursor.execute(
            f"""SELECT cleanup_run, COUNT(*), MIN(backed_up_at) FROM {backup}
                GROUP BY cleanup_run ORDER BY MIN(backed_up_at)"""
        )
        return cursor.fetchall()
    finally:
        cursor.close()


def spurious_reason(row: dict, published_max_age_years: int | None = None) -> str | None:
    """Why an existing event would not be emitted today, or None to keep it."""
    if row["event_type"] == "new_paper":
        # The emitter skips seeds before this check; the stored is_seed flag
        # carries that over. A paper first saved by this event was discovered
        # at (or just after) the event, so "known since" only fires for papers
        # that already existed.
        return new_paper_suppression_reason(
            is_new=False,
            is_seed=bool(row["is_seed"]),
            discovered_at=row["discovered_at"],
            year=row["year"],
            already_announced=False,
            event_at=row["created_at"],
        )
    if row["event_type"] == "status_change":
        return status_change_suppression_reason(
            row["new_status"], row["venue"], row["year"], row["created_at"].year,
            published_max_age_years,
        )
    return None


def find_spurious(rows: list[dict], published_max_age_years: int | None = None) -> list[tuple[dict, str]]:
    found = []
    for row in rows:
        reason = spurious_reason(row, published_max_age_years)
        if reason:
            found.append((row, reason))
    return found


def _reason_label(event_type: str, reason: str) -> str:
    # Collapse per-row details ("year 2002 older than 2y") into buckets.
    if reason.startswith("year "):
        reason = "stale year (" + reason.rsplit(" ", 1)[-1] + ")"
    elif reason.startswith("known since"):
        reason = "known > 7 days before event"
    return f"{event_type}: {reason}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="back up and delete the events (default: dry run)")
    parser.add_argument("--undo", metavar="RUN", help="restore the events deleted by a cleanup run")
    parser.add_argument("--list-runs", action="store_true", help="list cleanup runs that can be undone")
    parser.add_argument("--samples", type=int, default=10, help="example rows to print per reason")
    parser.add_argument("--before", help="only events recorded before this date (YYYY-MM-DD); default: all")
    parser.add_argument("--published-max-age", type=int, default=None,
                        help="years a 'published' item may predate its event (default: FEED_PUBLISHED_MAX_AGE_YEARS or 1)")
    args = parser.parse_args(argv)

    from backend.database import fetch_all, get_connection

    if args.list_runs:
        with get_connection() as conn:
            runs = list_runs(conn)
        for run_id, n, at in runs:
            print(f"{run_id}\t{n} events\tbacked up {at}")
        if not runs:
            print("No cleanup runs to undo.")
        return 0
    if args.undo:
        with get_connection() as conn:
            restored = undo_run(conn, args.undo)
        print(f"Restored run {args.undo}: {restored}")
        return 0

    # recorded_at is the insert time; older rows only have created_at.
    cutoff_sql, params = "", ()
    if args.before:
        cutoff_sql, params = " AND COALESCE(fe.recorded_at, fe.created_at) < %s", (args.before,)
    rows = fetch_all(NEW_PAPER_SQL + cutoff_sql, params) + fetch_all(STATUS_CHANGE_SQL + cutoff_sql, params)
    spurious = find_spurious(rows, args.published_max_age)

    by_reason: dict[str, list[dict]] = {}
    for row, reason in spurious:
        by_reason.setdefault(_reason_label(row["event_type"], reason), []).append(row)

    by_type = Counter(row["event_type"] for row, _ in spurious)
    print(f"Checked {len(rows)} new_paper/status_change events; "
          f"{len(spurious)} spurious ({dict(by_type)})")
    for label, items in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{label}: {len(items)}")
        for row in items[:args.samples]:
            status = (f"{row.get('old_status')}→{row['new_status']}"
                      if row["event_type"] == "status_change" else row.get("new_status"))
            venue = f" | {row['venue']}" if row.get("venue") else ""
            print(f"  [{row['id']}] {row['created_at']:%Y-%m-%d} {status} "
                  f"year={row['year']}{venue} | {row['title']}")

    if not spurious:
        print("Nothing to clean up.")
        return 0
    if not args.apply:
        print("\nDry run — pass --apply to delete these events.")
        return 0

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with get_connection() as conn:
        counts = backup_and_delete(conn, spurious, run_id)
    print(f"\nDeleted {counts['feed_events']} spurious feed events (run {run_id}).")
    print(f"Backed up: {counts}")
    print(f"Undo with: python scripts/cleanup_spurious_feed_events.py --undo {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
