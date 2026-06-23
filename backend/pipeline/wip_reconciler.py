"""Reconcile work_in_progress → working_paper based on link availability.

One-directional: only promotes WIP papers that gain links. Never demotes.
"""
from backend.database import get_connection
from backend.pipeline.feed_events import FeedEventEmitter


def _paper_has_link(cursor, paper_id: int) -> bool:
    """Return True if the paper has any paper_links row or a valid draft_url."""
    cursor.execute(
        "SELECT COUNT(*) FROM paper_links WHERE paper_id = %s",
        (paper_id,),
    )
    if cursor.fetchone()[0] > 0:
        return True

    cursor.execute(
        "SELECT draft_url_status FROM papers WHERE id = %s",
        (paper_id,),
    )
    row = cursor.fetchone()
    return row is not None and row[0] == "valid"


def reconcile_wip_status(paper_id: int) -> None:
    """Promote work_in_progress → working_paper if the paper now has links."""
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        cursor.execute("SELECT status FROM papers WHERE id = %s", (paper_id,))
        row = cursor.fetchone()
        if not row or row[0] != "work_in_progress":
            cursor.close()
            return

        if not _paper_has_link(cursor, paper_id):
            cursor.close()
            return

        cursor.execute(
            "UPDATE papers SET status = 'working_paper' WHERE id = %s",
            (paper_id,),
        )
        conn.commit()
        cursor.close()

    FeedEventEmitter.emit_status_change(paper_id, "work_in_progress", "working_paper")
