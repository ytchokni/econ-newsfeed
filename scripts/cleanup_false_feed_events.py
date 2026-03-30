"""One-time cleanup: delete feed events for papers whose source URL has < 2 snapshots.

These events were created during first extractions or re-discoveries of seed
papers, before the snapshot baseline guard was implemented.

Run: poetry run python scripts/cleanup_false_feed_events.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database


def main():
    bad_events = Database.fetch_all("""
        SELECT fe.id, fe.created_at, LEFT(p.title, 60) AS title,
               LEFT(p.source_url, 50) AS source_url,
               COALESCE((
                   SELECT MAX(cnt) FROM (
                       SELECT COUNT(*) AS cnt FROM html_snapshots hs
                       JOIN researcher_urls ru ON hs.url_id = ru.id
                       WHERE ru.url = p.source_url
                       GROUP BY hs.url_id
                   ) sub
               ), 0) AS max_snapshots
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        WHERE fe.event_type = 'new_paper'
        HAVING max_snapshots < 2
        ORDER BY fe.created_at
    """)

    total = Database.fetch_one("SELECT COUNT(*) AS c FROM feed_events")
    print(f"Feed events total: {total['c']}")
    print(f"False new_paper events (source URL < 2 snapshots): {len(bad_events)}")

    if not bad_events:
        print("Nothing to clean up.")
        return

    print("\nEvents to delete:")
    for ev in bad_events:
        print(f"  [{ev['id']}] {ev['created_at']} | {ev['title']} | {ev['source_url']} ({ev['max_snapshots']} snaps)")

    confirm = input("\nProceed with deletion? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    ids = [ev['id'] for ev in bad_events]
    placeholders = ','.join(['%s'] * len(ids))
    Database.execute_query(
        f"DELETE FROM feed_events WHERE id IN ({placeholders})",
        tuple(ids),
    )

    remaining = Database.fetch_one("SELECT COUNT(*) AS c FROM feed_events")
    print(f"Deleted {len(bad_events)} false feed events")
    print(f"Feed events remaining: {remaining['c']}")


if __name__ == "__main__":
    main()
