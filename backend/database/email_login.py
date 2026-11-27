"""One-time email login codes and email-based account lookup.

Codes are stored only as HMAC digests. Datetimes are naive UTC, matching the
DATETIME columns. Policy (code length, expiry, limits) lives in
``backend.email_login``; this module only persists and atomically checks.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta

from backend.database.connection import execute_query, fetch_one, get_connection

# Rows older than this are deleted whenever a new code is stored. It must
# exceed the per-email rate-limit window, which counts recent rows.
CODE_RETENTION = timedelta(days=1)


def latest_code_created_at(email: str) -> datetime | None:
    """Return when the newest code for this email was issued, if any."""
    row = fetch_one(
        "SELECT MAX(created_at) AS created_at FROM email_login_codes WHERE email = %s",
        (email,),
    )
    return row["created_at"] if row else None


def count_codes_for_email_since(email: str, since: datetime) -> int:
    """Count codes issued to one address since ``since`` (per-email rate limit)."""
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM email_login_codes WHERE email = %s AND created_at >= %s",
        (email, since),
    )
    return int(row["n"]) if row else 0


def count_codes_since(since: datetime) -> int:
    """Count codes issued to anyone since ``since`` (global send cap)."""
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM email_login_codes WHERE created_at >= %s",
        (since,),
    )
    return int(row["n"]) if row else 0


def failed_attempts_for_email_since(email: str, since: datetime) -> int:
    """Wrong guesses against this address's codes issued since ``since``."""
    row = fetch_one(
        "SELECT COALESCE(SUM(attempts), 0) AS n FROM email_login_codes "
        "WHERE email = %s AND created_at >= %s",
        (email, since),
    )
    return int(row["n"]) if row else 0


def failed_attempts_since(since: datetime) -> int:
    """Wrong guesses against anyone's codes issued since ``since`` (circuit breaker)."""
    row = fetch_one(
        "SELECT COALESCE(SUM(attempts), 0) AS n FROM email_login_codes WHERE created_at >= %s",
        (since,),
    )
    return int(row["n"]) if row else 0


def store_code(email: str, code_hash: str, now: datetime, expires_at: datetime) -> int:
    """Invalidate earlier codes for this email and store a new one. Returns its id."""
    with get_connection() as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE email_login_codes SET consumed_at = %s "
                    "WHERE email = %s AND consumed_at IS NULL",
                    (now, email),
                )
                cursor.execute(
                    "INSERT INTO email_login_codes (email, code_hash, created_at, expires_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (email, code_hash, now, expires_at),
                )
                code_id = cursor.lastrowid
                cursor.execute(
                    "DELETE FROM email_login_codes WHERE created_at < %s",
                    (now - CODE_RETENTION,),
                )
            conn.commit()
            return code_id
        except Exception:
            conn.rollback()
            raise


def discard_code(code_id: int, now: datetime) -> None:
    """Retire a code whose email could not be sent."""
    execute_query(
        "UPDATE email_login_codes SET consumed_at = %s WHERE id = %s AND consumed_at IS NULL",
        (now, code_id),
    )


def evaluate_attempt(row: dict | None, candidate_hash: str, now: datetime,
                     max_attempts: int) -> tuple[str, int, bool]:
    """Decide one verification attempt against the newest live code.

    Returns ``(result, attempts, consume)``: the outcome (``"ok"``,
    ``"invalid"``, ``"expired"`` or ``"locked"`` when this attempt used up the
    last try), the code's new attempt count, and whether to retire the code.
    """
    if row is None:
        return "invalid", 0, False
    attempts = int(row["attempts"])
    if row["expires_at"] <= now:
        return "expired", attempts, True
    if hmac.compare_digest(row["code_hash"], candidate_hash):
        return "ok", attempts, True
    attempts += 1
    if attempts >= max_attempts:
        return "locked", attempts, True
    return "invalid", attempts, False


def consume_code(email: str, candidate_hash: str, now: datetime, max_attempts: int) -> str:
    """Check a code for this email and consume it on success.

    The row lock serialises concurrent guesses, so the attempt limit holds.
    See :func:`evaluate_attempt` for the results.
    """
    with get_connection() as conn:
        try:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT id, code_hash, attempts, expires_at FROM email_login_codes "
                    "WHERE email = %s AND consumed_at IS NULL "
                    "ORDER BY id DESC LIMIT 1 FOR UPDATE",
                    (email,),
                )
                row = cursor.fetchone()
                result, attempts, consume = evaluate_attempt(row, candidate_hash, now, max_attempts)
                if row is not None:
                    cursor.execute(
                        "UPDATE email_login_codes SET attempts = %s, consumed_at = %s WHERE id = %s",
                        (attempts, now if consume else None, row["id"]),
                    )
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def get_user_by_id(user_id: int) -> dict | None:
    """Look up a user by primary key."""
    return fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))


def get_user_by_email(email: str) -> dict | None:
    """Return the oldest account for this address (the column collation ignores case)."""
    return fetch_one("SELECT * FROM users WHERE email = %s ORDER BY id LIMIT 1", (email,))


def get_or_create_email_user(email: str, now: datetime) -> dict:
    """Sign in to the account that owns this verified address, creating one if needed.

    An existing Google account with the same address is reused, so the two
    login methods share follows and digest settings.
    """
    row = get_user_by_email(email)
    if row:
        return row
    user_id = execute_query(
        "INSERT INTO users (google_id, email, created_at) VALUES (NULL, %s, %s)",
        (email, now),
    )
    return fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
