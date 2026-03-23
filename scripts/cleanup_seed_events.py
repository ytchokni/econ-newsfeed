"""One-time cleanup: delete feed events created during initial scrapes.

All 1,579 existing new_paper events (March 19-20, 2026) were generated
before is_seed detection was implemented. Every paper found on the first
extraction of each URL was incorrectly treated as a new discovery.

Run: poetry run python scripts/cleanup_seed_events.py
"""
import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

# All timestamps in the DB are UTC (datetime.now(timezone.utc) used throughout codebase)
CUTOFF = "2026-03-21 00:00:00"


def main():
    # Show current state
    count = Database.fetch_one("SELECT COUNT(*) AS c FROM feed_events")
    to_delete = Database.fetch_one(
        "SELECT COUNT(*) AS c FROM feed_events WHERE event_type = 'new_paper' AND created_at < %s",
        (CUTOFF,),
    )
    print(f"Feed events total: {count['c']}")
    print(f"Spurious new_paper events (before {CUTOFF} UTC) to delete: {to_delete['c']}")

    if to_delete['c'] == 0:
        print("Nothing to clean up.")
        return

    confirm = input("Proceed with deletion? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    Database.execute_query(
        "DELETE FROM feed_events WHERE event_type = 'new_paper' AND created_at < %s",
        (CUTOFF,),
    )

    remaining = Database.fetch_one("SELECT COUNT(*) AS c FROM feed_events")
    print(f"Deleted {to_delete['c']} spurious feed events")
    print(f"Feed events remaining: {remaining['c']}")


if __name__ == "__main__":
    main()
