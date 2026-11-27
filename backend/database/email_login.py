"""One-time email login codes and email-based account lookup.

Codes are stored only as HMAC digests. Datetimes are naive UTC, matching the
DATETIME columns. Policy (code length, expiry, limits) lives in
``backend.email_login``; this module only persists and atomically checks.

Concurrency: every read-check-write for one address runs inside
:func:`address_lock` (a MySQL named lock), so two requests for the same
address never interleave. Writes then touch rows by primary key only, which
takes no gap locks, so requests for different addresses cannot deadlock.
All statements use the request's scoped connection when there is one, so a
request holds a single pooled connection.
"""
from __future__ import annotations

import hashlib
import hmac
from contextlib import contextmanager
from datetime import datetime, timedelta

from backend.database.connection import (
    _get_scoped_or_new_connection,
    execute_query,
    fetch_one,
)

# The hourly scheduler purge deletes rows older than this. It must cover the
# per-address windows (codes per hour, wrong codes per day), which count rows.
CODE_RETENTION = timedelta(days=1)

# Seconds a request waits for another request on the same address.
ADDRESS_LOCK_TIMEOUT = 5


class AddressBusy(Exception):
    """Another request held this address's lock for longer than the timeout."""


def _lock_name(email: str) -> str:
    # MySQL rejects lock names over 64 characters; this one is 60.
    return "email_login:" + hashlib.sha256(email.encode()).hexdigest()[:48]


@contextmanager
def _connection():
    """The request's scoped connection, or a fresh one closed afterwards."""
    conn, should_close = _get_scoped_or_new_connection()
    try:
        yield conn
    finally:
        if should_close:
            conn.close()


@contextmanager
def address_lock(email: str, timeout: int = ADDRESS_LOCK_TIMEOUT):
    """Serialize all code issuance and checks for one address.

    Raises AddressBusy if another request holds the lock past ``timeout``.
    The lock lives on the request's connection; the current transaction is
    committed first so reads inside see what the previous holder committed
    (InnoDB keeps a read snapshot open until the transaction ends).
    """
    name = _lock_name(email)
    with _connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, %s)", (name, timeout))
            got = cursor.fetchone()[0]
        if got != 1:
            raise AddressBusy(email)
        try:
            conn.commit()
            yield
        finally:
            try:
                conn.rollback()  # never leave a half-done transaction behind the lock
            finally:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))
                    cursor.fetchone()


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
    """Retire earlier live codes for this email and store a new one. Returns its id.

    Call inside :func:`address_lock`. Earlier codes are found with a plain read
    and retired by primary key, so no gap locks are taken.
    """
    with _connection() as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM email_login_codes WHERE email = %s AND consumed_at IS NULL",
                    (email,),
                )
                live = [row[0] for row in cursor.fetchall()]
                if live:
                    placeholders = ",".join(["%s"] * len(live))
                    cursor.execute(
                        f"UPDATE email_login_codes SET consumed_at = %s WHERE id IN ({placeholders})",
                        (now, *live),
                    )
                cursor.execute(
                    "INSERT INTO email_login_codes (email, code_hash, created_at, expires_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (email, code_hash, now, expires_at),
                )
                code_id = cursor.lastrowid
            conn.commit()
            return code_id
        except Exception:
            conn.rollback()
            raise


def purge_old_codes(now: datetime) -> int:
    """Delete codes issued more than CODE_RETENTION ago. Returns rows deleted.

    Runs hourly from the scheduler, so a code row is gone at most
    CODE_RETENTION + 1 hour after it was issued, even with no new requests.
    READ COMMITTED keeps the range delete from gap-locking against inserts.
    """
    with _connection() as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cursor.execute(
                    "DELETE FROM email_login_codes WHERE created_at < %s",
                    (now - CODE_RETENTION,),
                )
                deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise


def purge_old_codes(now: datetime) -> int:
    """Delete codes issued more than CODE_RETENTION ago. Returns rows deleted.

    Runs hourly from the scheduler, so a code row is gone at most
    CODE_RETENTION + 1 hour after it was issued, even with no new requests.
    """
    with get_connection() as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM email_login_codes WHERE created_at < %s",
                    (now - CODE_RETENTION,),
                )
                deleted = cursor.rowcount
            conn.commit()
            return deleted
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

    Call inside :func:`address_lock`, which serialises concurrent guesses so
    the attempt limit holds; the row is then updated by primary key.
    See :func:`evaluate_attempt` for the results.
    """
    with _connection() as conn:
        try:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT id, code_hash, attempts, expires_at FROM email_login_codes "
                    "WHERE email = %s AND consumed_at IS NULL "
                    "ORDER BY id DESC LIMIT 1",
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
