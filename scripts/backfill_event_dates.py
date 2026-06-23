"""One-time backfill: set feed_events.created_at to the HTML fetch timestamp.

Before PR #144, feed events used datetime.now() (extraction time) instead of
the fetch timestamp. When extraction lagged behind fetching (batch API, backlog,
rate limits), events got dates hours or days after the actual page change.

Only updates events where html_content.timestamp < feed_events.created_at,
meaning the original fetch timestamp is still available (hasn't been overwritten
by a newer fetch that postdates the event).

Run: poetry run python scripts/backfill_event_dates.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import execute_query, fetch_all, fetch_one


UPDATE_SQL = """
    UPDATE feed_events fe
    JOIN papers p ON fe.paper_id = p.id
    JOIN researcher_urls ru ON ru.url = p.source_url
    JOIN html_content hc ON hc.url_id = ru.id
    SET fe.created_at = hc.timestamp
    WHERE hc.timestamp IS NOT NULL
      AND hc.timestamp < fe.created_at
"""

PREVIEW_SQL = """
    SELECT fe.id, fe.event_type, fe.created_at AS old_date,
           hc.timestamp AS fetch_date,
           TIMESTAMPDIFF(HOUR, hc.timestamp, fe.created_at) AS drift_hours
    FROM feed_events fe
    JOIN papers p ON fe.paper_id = p.id
    JOIN researcher_urls ru ON ru.url = p.source_url
    JOIN html_content hc ON hc.url_id = ru.id
    WHERE hc.timestamp IS NOT NULL
      AND hc.timestamp < fe.created_at
    ORDER BY drift_hours DESC
    LIMIT 20
"""


def main():
    total = fetch_one("SELECT COUNT(*) AS c FROM feed_events")
    eligible = fetch_one(f"""
        SELECT COUNT(*) AS c
        FROM feed_events fe
        JOIN papers p ON fe.paper_id = p.id
        JOIN researcher_urls ru ON ru.url = p.source_url
        JOIN html_content hc ON hc.url_id = ru.id
        WHERE hc.timestamp IS NOT NULL
          AND hc.timestamp < fe.created_at
    """)

    print(f"Feed events total: {total['c']}")
    print(f"Events eligible for date correction: {eligible['c']}")

    if eligible['c'] == 0:
        print("Nothing to backfill.")
        return

    rows = fetch_all(PREVIEW_SQL)
    print(f"\nTop {len(rows)} events by drift (hours between fetch and event):")
    for r in rows:
        print(f"  id={r['id']}  {r['event_type']:15s}  "
              f"fetch={r['fetch_date']}  event={r['old_date']}  "
              f"drift={r['drift_hours']}h")

    confirm = input("\nProceed with update? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    execute_query(UPDATE_SQL)
    print(f"Updated {eligible['c']} feed events to use fetch timestamps.")


if __name__ == "__main__":
    main()
