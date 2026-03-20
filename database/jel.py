"""JEL code database operations."""
from datetime import datetime, timezone

from database.connection import execute_query, fetch_all, fetch_one


def get_all_jel_codes() -> list[dict]:
    """Return all JEL codes ordered by code."""
    return fetch_all("SELECT code, name, parent_code FROM jel_codes ORDER BY code")


def get_jel_codes_for_researcher(researcher_id: int) -> list[dict]:
    """Return JEL codes for a single researcher."""
    return fetch_all(
        """SELECT jc.code, jc.name
           FROM researcher_jel_codes rjc
           JOIN jel_codes jc ON jc.code = rjc.jel_code
           WHERE rjc.researcher_id = %s
           ORDER BY jc.code""",
        (researcher_id,),
    )


def get_jel_codes_for_researchers(researcher_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch JEL codes for multiple researchers."""
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = fetch_all(
        f"""SELECT rjc.researcher_id, jc.code, jc.name
            FROM researcher_jel_codes rjc
            JOIN jel_codes jc ON jc.code = rjc.jel_code
            WHERE rjc.researcher_id IN ({placeholders})
            ORDER BY jc.code""",
        tuple(researcher_ids),
    )
    result: dict[int, list[dict]] = {rid: [] for rid in researcher_ids}
    for row in rows:
        result[row["researcher_id"]].append({"code": row["code"], "name": row["name"]})
    return result


def save_researcher_jel_codes(researcher_id: int, jel_codes: list[str]) -> None:
    """Replace a researcher's JEL codes with the given list.

    Deletes existing codes and inserts the new set in a single transaction.
    Invalid codes (not in jel_codes table) are silently skipped.
    """
    from database.connection import get_connection

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM researcher_jel_codes WHERE researcher_id = %s",
                (researcher_id,),
            )
            for code in jel_codes:
                try:
                    cursor.execute(
                        """INSERT INTO researcher_jel_codes
                           (researcher_id, jel_code, classified_at)
                           VALUES (%s, %s, %s)""",
                        (researcher_id, code.upper().strip(), now),
                    )
                except Exception:
                    pass  # Skip invalid codes (FK violation)
            conn.commit()


def get_researchers_needing_classification() -> list[dict]:
    """Return researchers with a description but no JEL codes assigned."""
    return fetch_all(
        """SELECT r.id, r.first_name, r.last_name, r.description
           FROM researchers r
           WHERE r.description IS NOT NULL
             AND r.description != ''
             AND r.id NOT IN (
                 SELECT DISTINCT researcher_id FROM researcher_jel_codes
             )
           ORDER BY r.id"""
    )
