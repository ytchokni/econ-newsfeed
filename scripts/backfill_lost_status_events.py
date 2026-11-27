"""Emit status_change events the long-page backfill swallowed (#283).

#283 re-extracted every page whose text had been cut at CONTENT_MAX_CHARS
once in full, as a seed (_backfill_long_page). A seed emits no events, so a
paper the database already had whose status advanced in that extraction
(working paper -> accepted, R&R -> published, ...) got no status_change
event. The pipeline now emits those; this script recovers the ones lost
between the #283 deploy and the fix.

A lost event is a paper snapshot taken since --since (default: the #283
deploy) that advanced the paper's status, where
  - the paper had an earlier snapshot (it existed before that extraction),
  - the transition is one the pipeline announces and passes the #280
    plausibility rules (backend.publication_rules), judged at the snapshot's
    time,
  - no status_change event of the paper reaches that status already,
  - the snapshot's page had been extracted before --since and its text is
    still longer than CONTENT_MAX_CHARS (the backfill's pages; other pages
    went through non-seed extraction, which emitted its events).
Each event is dated at its snapshot's scraped_at (the extraction time; the
pipeline would have used the fetch time a little earlier). recorded_at is
the insert time, so the next weekly digest includes them.

Dry run by default. --apply inserts the events and writes their ids to the
--backup JSONL file (required); --undo FILE deletes exactly those events
again (dry run unless --apply).

    poetry run python scripts/backfill_lost_status_events.py
    poetry run python scripts/backfill_lost_status_events.py --apply --backup /backups/lost-status-events.jsonl
    poetry run python scripts/backfill_lost_status_events.py --undo /backups/lost-status-events.jsonl --apply

In production, mount /backups into a one-off container:
    docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm -v /backups:/backups api \\
        python scripts/backfill_lost_status_events.py --apply --backup /backups/lost-status-events.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.publication_rules import (  # noqa: E402
    INTERESTING_STATUS_TRANSITIONS,
    is_working_paper_series,
    status_change_suppression_reason,
)

# #283 reached production on 27 Nov 2026, shortly before 15:00 UTC.
DEFAULT_SINCE = datetime(2026, 11, 27, 14, 0)

STATUS_ORDER = ('work_in_progress', 'working_paper', 'reject_and_resubmit', 'revise_and_resubmit',
                'accepted', 'published')
_RANK = {s: i for i, s in enumerate(STATUS_ORDER)}

# Papers with a snapshot to accepted/R&R/published since the cutoff.
CANDIDATE_PAPERS_SQL = """
    SELECT DISTINCT paper_id FROM paper_snapshots
    WHERE scraped_at >= %s AND status IN ('revise_and_resubmit', 'accepted', 'published')
"""


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def plan_events(snapshots: list[dict], papers: dict[int, dict], events: list[dict],
                eligible_pages: set[str], since: datetime) -> list[dict]:
    """The status_change events lost to seed extractions since `since`.

    snapshots: every snapshot of the candidate papers (paper_id, id, status,
    venue, year, scraped_at, source_url); papers: id -> venue, year, title;
    events: their status_change events (paper_id, new_status); eligible_pages:
    source URLs the backfill can have run on.
    """
    reached: dict[int, int] = {}
    for ev in events:
        rank = _RANK.get(ev["new_status"], -1)
        reached[ev["paper_id"]] = max(reached.get(ev["paper_id"], -1), rank)

    by_paper: dict[int, list[dict]] = {}
    for snap in snapshots:
        by_paper.setdefault(snap["paper_id"], []).append(snap)

    planned = []
    for paper_id, snaps in sorted(by_paper.items()):
        snaps.sort(key=lambda s: (s["scraped_at"], s["id"]))
        paper = papers.get(paper_id) or {}
        effective = None  # the paper's monotone status, as append_paper_snapshot keeps it
        venue = year = None
        for snap in snaps:
            old, new = effective, snap["status"]
            advanced = old is not None and _RANK.get(new, -1) > _RANK.get(old, -1)
            if old is None or advanced:
                effective = new
            # Venue and year the pipeline would have stored with the event.
            snap_venue = snap["venue"] if snap["venue"] and snap["venue"].strip() else None
            if (snap_venue and _RANK.get(effective, -1) > _RANK["working_paper"]
                    and is_working_paper_series(snap_venue)):
                snap_venue = None
            venue = snap_venue or venue
            year = snap["year"] or year
            if not advanced or _naive(snap["scraped_at"]) < since:
                continue
            if (old, new) not in INTERESTING_STATUS_TRANSITIONS:
                continue
            if snap["source_url"] not in eligible_pages:
                continue
            if reached.get(paper_id, -1) >= _RANK[new]:
                continue
            event_venue = venue or paper.get("venue")
            event_year = year or paper.get("year")
            if status_change_suppression_reason(new, event_venue, event_year, snap["scraped_at"].year):
                continue
            reached[paper_id] = _RANK[new]
            planned.append({
                "paper_id": paper_id, "old_status": old, "new_status": new,
                "created_at": _naive(snap["scraped_at"]), "event_venue": event_venue,
                "event_source_url": snap["source_url"], "snapshot_id": snap["id"],
                "title": (paper.get("title") or "")[:80], "year": event_year,
            })
    return planned


def _long_page(raw_html: str | None, content: str | None) -> bool:
    from backend.pipeline.html_fetcher import CONTENT_MAX_CHARS, HTMLFetcher
    if raw_html:
        return len(HTMLFetcher.extract_text_content(raw_html)) > CONTENT_MAX_CHARS
    return len(content or "") > CONTENT_MAX_CHARS


def load_and_plan(since: datetime) -> list[dict]:
    from backend.database import fetch_all

    paper_ids = [r["paper_id"] for r in fetch_all(CANDIDATE_PAPERS_SQL, (since,))]
    if not paper_ids:
        return []
    snapshots, papers, events = [], {}, []
    for i in range(0, len(paper_ids), 500):
        chunk = paper_ids[i:i + 500]
        marks = ",".join(["%s"] * len(chunk))
        snapshots += fetch_all(
            f"""SELECT id, paper_id, status, venue, year, scraped_at, source_url
                FROM paper_snapshots WHERE paper_id IN ({marks})""", tuple(chunk))
        for row in fetch_all(f"SELECT id, venue, year, title FROM papers WHERE id IN ({marks})", tuple(chunk)):
            papers[row["id"]] = row
        events += fetch_all(
            f"""SELECT paper_id, new_status FROM feed_events
                WHERE event_type = 'status_change' AND paper_id IN ({marks})""", tuple(chunk))

    # Pages extracted before the cutoff whose text is longer than the old cut.
    pages = sorted({s["source_url"] for s in snapshots
                    if s["source_url"] and _naive(s["scraped_at"]) >= since})
    eligible = set()
    for url in pages:
        before = fetch_all(
            "SELECT 1 FROM paper_snapshots WHERE source_url = %s AND scraped_at < %s LIMIT 1", (url, since))
        if not before:
            continue
        rows = fetch_all(
            """SELECT hc.raw_html, hc.content FROM html_content hc
               JOIN researcher_urls ru ON ru.id = hc.url_id WHERE ru.url = %s LIMIT 1""", (url,))
        if rows and _long_page(rows[0]["raw_html"], rows[0]["content"]):
            eligible.add(url)
    return plan_events(snapshots, papers, events, eligible, since)


def insert_events(planned: list[dict], backup: Path, run_id: str) -> list[int]:
    """Insert the events in one transaction; their ids go to the backup file first."""
    from backend.database import get_connection

    ids = []
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            for ev in planned:
                cur.execute(
                    """INSERT INTO feed_events
                       (paper_id, event_type, old_status, new_status, created_at,
                        event_venue, event_source_url, event_metadata_captured)
                       VALUES (%s, 'status_change', %s, %s, %s, %s, %s, TRUE)""",
                    (ev["paper_id"], ev["old_status"], ev["new_status"], ev["created_at"],
                     ev["event_venue"], ev["event_source_url"]),
                )
                ids.append(cur.lastrowid)
            with backup.open("a", encoding="utf-8") as fh:
                for event_id, ev in zip(ids, planned):
                    fh.write(json.dumps({"run": run_id, "event_id": event_id, **ev}, default=str) + "\n")
                fh.flush()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    return ids


def undo(backup: Path, apply: bool) -> int:
    """Delete the events a backup file lists, if they are still the ones inserted."""
    from backend.database import get_connection

    entries = [json.loads(line) for line in backup.read_text(encoding="utf-8").splitlines() if line.strip()]
    deleted = 0
    with get_connection() as conn:
        cur = conn.cursor()
        for e in entries:
            cur.execute(
                """SELECT id FROM feed_events WHERE id = %s AND paper_id = %s
                   AND event_type = 'status_change' AND new_status = %s AND created_at = %s""",
                (e["event_id"], e["paper_id"], e["new_status"], e["created_at"]),
            )
            if cur.fetchone() is None:
                print(f"  event {e['event_id']} not found or changed; left alone")
                continue
            if apply:
                cur.execute("DELETE FROM feed_events WHERE id = %s", (e["event_id"],))
            deleted += 1
        if apply:
            conn.commit()
        cur.close()
    verb = "Deleted" if apply else "Would delete"
    print(f"{verb} {deleted} of {len(entries)} events listed in {backup}.")
    return deleted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=datetime.fromisoformat, default=DEFAULT_SINCE,
                        help="UTC start of the window (default: the #283 deploy, 2026-11-27T14:00)")
    parser.add_argument("--apply", action="store_true", help="insert the events (or, with --undo, delete them)")
    parser.add_argument("--backup", type=Path, help="JSONL file recording inserted event ids (required with --apply)")
    parser.add_argument("--undo", type=Path, metavar="FILE", help="delete the events listed in a backup file")
    parser.add_argument("--samples", type=int, default=50, help="events to list in the report")
    args = parser.parse_args(argv)

    if args.undo:
        undo(args.undo, args.apply)
        return 0
    if args.apply and not args.backup:
        parser.error("--apply needs --backup FILE")

    since = _naive(args.since)
    planned = load_and_plan(since)
    print(f"{len(planned)} status_change events lost to the long-page backfill since {since:%Y-%m-%d %H:%M} UTC")
    for ev in planned[:args.samples]:
        print(f"  paper {ev['paper_id']} {ev['created_at']:%Y-%m-%d %H:%M} {ev['old_status']}→{ev['new_status']}"
              f" year={ev['year']} | {ev['event_venue']} | {ev['title']} | {ev['event_source_url']}")
    if not planned:
        return 0
    if not args.apply:
        print("\nDry run — pass --apply --backup FILE to insert these events.")
        return 0
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ids = insert_events(planned, args.backup, run_id)
    print(f"\nInserted {len(ids)} events (run {run_id}); ids recorded in {args.backup}.")
    print(f"Undo with: python scripts/backfill_lost_status_events.py --undo {args.backup} --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
