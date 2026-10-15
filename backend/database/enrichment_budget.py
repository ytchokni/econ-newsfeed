"""Durable daily request reservations shared by enrichment workers."""
import logging

from backend.database.connection import fetch_one, get_connection

logger = logging.getLogger(__name__)


def has_budget_remaining(provider: str, limit: int) -> bool:
    """Advisory check only; reserve_request enforces the limit atomically."""
    if limit <= 0:
        return False
    try:
        row = fetch_one(
            "SELECT request_count FROM enrichment_daily_budget "
            "WHERE provider = %s AND budget_day = UTC_DATE()",
            (provider,),
        )
        return row is None or row["request_count"] < limit
    except Exception:
        logger.exception("Cannot read %s request budget; skipping paid requests", provider)
        return False


def reserve_request(provider: str, limit: int) -> bool:
    """Charge before dispatch, including failed requests; fail closed on DB errors.

    A conditional UPDATE locks the shared daily row, so concurrent processes
    cannot spend the same last slot. Read the UTC day once to handle midnight.
    """
    if limit <= 0:
        return False
    try:
        with get_connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT UTC_DATE()")
                    day = cursor.fetchone()[0]
                    cursor.execute(
                        "INSERT IGNORE INTO enrichment_daily_budget "
                        "(provider, budget_day, request_count) VALUES (%s, %s, 0)",
                        (provider, day),
                    )
                    cursor.execute(
                        "UPDATE enrichment_daily_budget SET request_count = request_count + 1 "
                        "WHERE provider = %s AND budget_day = %s AND request_count < %s",
                        (provider, day, limit),
                    )
                    reserved = cursor.rowcount == 1
                conn.commit()
                return reserved
            except Exception:
                conn.rollback()
                raise
    except Exception:
        logger.exception("Cannot reserve %s request budget; skipping paid request", provider)
        return False
