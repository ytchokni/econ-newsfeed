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
digest_deliveries and feed_event_reviews.

    poetry run python scripts/cleanup_spurious_feed_events.py
    poetry run python scripts/cleanup_spurious_feed_events.py --apply
    poetry run python scripts/cleanup_spurious_feed_events.py --before 2026-11-28 --published-max-age 2
"""
import argparse
import sys
from collections import Counter
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
    parser.add_argument("--apply", action="store_true", help="delete the events (default: dry run)")
    parser.add_argument("--samples", type=int, default=10, help="example rows to print per reason")
    parser.add_argument("--before", help="only events recorded before this date (YYYY-MM-DD); default: all")
    parser.add_argument("--published-max-age", type=int, default=None,
                        help="years a 'published' item may predate its event (default: FEED_PUBLISHED_MAX_AGE_YEARS or 1)")
    args = parser.parse_args(argv)

    from backend.database import fetch_all, get_connection

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

    ids = [row["id"] for row, _ in spurious]
    deleted = 0
    with get_connection() as conn:
        cursor = conn.cursor()
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            cursor.execute(
                f"DELETE FROM feed_events WHERE id IN ({','.join(['%s'] * len(chunk))})",
                tuple(chunk),
            )
            deleted += cursor.rowcount
        conn.commit()
        cursor.close()
    print(f"\nDeleted {deleted} spurious feed events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
