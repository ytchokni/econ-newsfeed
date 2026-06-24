"""Reconcile work_in_progress → working_paper based on link availability.

One-directional: only promotes WIP papers that gain links. Never demotes.
"""
from backend.database import get_connection
from backend.pipeline.feed_events import FeedEventEmitter


def reconcile_wip_status(paper_id: int) -> None:
    """Promote work_in_progress → working_paper if the paper now has links."""
    with get_connection() as conn:
        with conn.cursor(buffered=True) as cursor:
            cursor.execute(
                """SELECT p.status, p.draft_url_status,
                          EXISTS(SELECT 1 FROM paper_links pl WHERE pl.paper_id = p.id) AS has_link
                   FROM papers p WHERE p.id = %s""",
                (paper_id,),
            )
            row = cursor.fetchone()
            if not row or row[0] != "work_in_progress":
                return

            has_link = row[2] or row[1] == "valid"
            if not has_link:
                return

            cursor.execute(
                "UPDATE papers SET status = 'working_paper' WHERE id = %s",
                (paper_id,),
            )
            conn.commit()

    FeedEventEmitter.emit_status_change(paper_id, "work_in_progress", "working_paper")
