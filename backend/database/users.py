"""User accounts, follows, and notification preferences."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.database.connection import execute_query, fetch_all, fetch_one, get_connection


def get_or_create_user(google_id: str, email: str, name: str | None = None,
                       picture_url: str | None = None) -> dict:
    """Find user by google_id or create one. Returns the user row as a dict."""
    row = fetch_one("SELECT * FROM users WHERE google_id = %s", (google_id,))
    if row:
        execute_query(
            "UPDATE users SET email = %s, name = %s, picture_url = %s, "
            "updated_at = %s WHERE id = %s",
            (email, name, picture_url, datetime.now(timezone.utc), row['id']),
        )
        row['email'] = email
        row['name'] = name
        row['picture_url'] = picture_url
        return row

    user_id = execute_query(
        "INSERT INTO users (google_id, email, name, picture_url, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (google_id, email, name, picture_url, datetime.now(timezone.utc)),
    )
    return fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))


def get_user_by_google_id(google_id: str) -> dict | None:
    """Look up a user by Google ID."""
    return fetch_one("SELECT * FROM users WHERE google_id = %s", (google_id,))


def add_follow(user_id: int, researcher_id: int) -> None:
    """Follow a researcher. Idempotent."""
    execute_query(
        "INSERT IGNORE INTO user_follows (user_id, researcher_id) VALUES (%s, %s)",
        (user_id, researcher_id),
    )


def remove_follow(user_id: int, researcher_id: int) -> None:
    """Unfollow a researcher. Idempotent."""
    execute_query(
        "DELETE FROM user_follows WHERE user_id = %s AND researcher_id = %s",
        (user_id, researcher_id),
    )


def get_followed_researcher_ids(user_id: int) -> list[int]:
    """Return list of researcher IDs the user follows."""
    rows = fetch_all(
        "SELECT researcher_id FROM user_follows WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,),
    )
    return [r['researcher_id'] for r in rows]


def get_notification_prefs(user_id: int) -> dict:
    """Get notification prefs, creating defaults if needed."""
    row = fetch_one(
        "SELECT * FROM user_notification_prefs WHERE user_id = %s", (user_id,),
    )
    if row:
        return row
    execute_query(
        "INSERT INTO user_notification_prefs (user_id, digest_enabled) VALUES (%s, TRUE)",
        (user_id,),
    )
    return {"user_id": user_id, "digest_enabled": True, "last_digest_sent": None}


def update_notification_prefs(user_id: int, digest_enabled: bool) -> None:
    """Update digest preference. Creates row if missing."""
    execute_query(
        "INSERT INTO user_notification_prefs (user_id, digest_enabled) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE digest_enabled = VALUES(digest_enabled)",
        (user_id, digest_enabled),
    )


def get_digest_recipients() -> list[dict]:
    """Return users with digest enabled and their followed researcher IDs."""
    rows = fetch_all(
        """SELECT u.id, u.email, u.name,
                  np.last_digest_sent, u.created_at
           FROM users u
           JOIN user_notification_prefs np ON np.user_id = u.id
           WHERE np.digest_enabled = TRUE""",
    )
    result = []
    for row in rows:
        researcher_ids = get_followed_researcher_ids(row['id'])
        if researcher_ids:
            result.append({**row, 'researcher_ids': researcher_ids})
    return result


def get_feed_events_for_researchers(
    researcher_ids: list[int], since: datetime
) -> list[dict]:
    """Get feed events for given researchers since a datetime, grouped for digest."""
    if not researcher_ids:
        return []
    placeholders = ",".join(["%s"] * len(researcher_ids))
    return fetch_all(
        f"""SELECT fe.event_type, fe.old_status, fe.new_status, fe.created_at,
                   p.id AS paper_id, p.title, p.status, p.year, p.venue,
                   r.id AS researcher_id, r.first_name, r.last_name
            FROM feed_events fe
            JOIN papers p ON p.id = fe.paper_id
            JOIN authorship a ON a.publication_id = p.id
            JOIN researchers r ON r.id = a.researcher_id
            WHERE a.researcher_id IN ({placeholders})
              AND fe.created_at >= %s
            ORDER BY r.last_name, r.first_name, fe.created_at DESC""",
        (*researcher_ids, since),
    )


def update_last_digest_sent(user_id: int, sent_at: datetime) -> None:
    """Update the last_digest_sent timestamp after sending a digest."""
    execute_query(
        "UPDATE user_notification_prefs SET last_digest_sent = %s WHERE user_id = %s",
        (sent_at, user_id),
    )


def researcher_exists(researcher_id: int) -> bool:
    """Check if a researcher exists."""
    row = fetch_one("SELECT 1 FROM researchers WHERE id = %s", (researcher_id,))
    return row is not None


def generate_unsubscribe_token(user_id: int, secret: str) -> str:
    """Generate an HMAC-signed unsubscribe token."""
    import hmac
    import hashlib
    signature = hmac.new(
        secret.encode(), str(user_id).encode(), hashlib.sha256
    ).hexdigest()
    return f"{user_id}.{signature}"


def verify_unsubscribe_token(token: str, secret: str) -> int | None:
    """Verify an unsubscribe token and return user_id, or None if invalid."""
    import hmac
    import hashlib
    parts = token.split(".", 1)
    if len(parts) != 2:
        return None
    user_id_str, signature = parts
    try:
        user_id = int(user_id_str)
    except ValueError:
        return None
    expected = hmac.new(
        secret.encode(), str(user_id).encode(), hashlib.sha256
    ).hexdigest()
    if hmac.compare_digest(signature, expected):
        return user_id
    return None
