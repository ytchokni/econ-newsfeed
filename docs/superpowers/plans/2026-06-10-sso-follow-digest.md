# SSO + Follow Researchers + Weekly Digest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users sign in with Google, follow researchers, and receive a weekly email digest of new publications from followed researchers.

**Architecture:** NextAuth.js handles Google OAuth on the Next.js frontend (JWT strategy). The FastAPI backend verifies JWTs via a shared secret and lazily creates user records. A weekly APScheduler job queries followed researchers' feed events and sends digest emails via Resend.

**Tech Stack:** NextAuth.js v5, python-jose, Resend (Python SDK), MySQL, FastAPI, SWR

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `database/users.py` | User CRUD, follows, notification prefs, digest queries |
| `auth.py` | FastAPI JWT verification dependencies (`get_current_user`, `get_optional_user`) |
| `digest.py` | Weekly digest: query events, render HTML email, send via Resend |
| `app/src/app/api/auth/[...nextauth]/route.ts` | NextAuth.js Google provider config |
| `app/src/lib/auth.tsx` | React context provider wrapping `SessionProvider`, `useAuth` hook |
| `app/src/components/FollowButton.tsx` | Follow/unfollow toggle component |
| `app/src/components/UserMenu.tsx` | Signed-in avatar + dropdown |
| `tests/test_auth.py` | Backend auth dependency tests |
| `tests/test_users_api.py` | Follow/unfollow/notifications API tests |
| `tests/test_digest.py` | Digest query + email rendering tests |

### Modified files

| File | Changes |
|------|---------|
| `database/schema.py` | Add `users`, `user_follows`, `user_notification_prefs` tables + migration |
| `database/__init__.py` | Re-export new user functions + add to Database facade |
| `api.py` | Add `/api/users/*` endpoints, add optional auth to `/api/publications` |
| `scheduler.py` | Register weekly digest job |
| `pyproject.toml` | Add `python-jose[cryptography]`, `resend` |
| `app/package.json` | Add `next-auth` |
| `app/src/app/layout.tsx` | Wrap children with auth provider |
| `app/src/components/Header.tsx` | Add sign-in button / user menu |
| `app/src/components/ResearcherCard.tsx` | Add follow button |
| `app/src/app/researchers/[id]/ResearcherDetailContent.tsx` | Add follow button |
| `app/src/app/NewsfeedContent.tsx` | Add "My Feed" toggle |
| `app/src/lib/api.ts` | Add auth-aware fetch helpers, follow/notification hooks |
| `app/src/lib/types.ts` | Add user + notification pref types |
| `app/next.config.mjs` | Update CSP `connect-src` for Google OAuth |
| `.env.example` | Add new env vars |
| `.dockerignore` | Add `!auth.py`, `!digest.py` |
| `docker-compose.prod.yml` | Add `NEXTAUTH_SECRET`, `RESEND_API_KEY`, `DIGEST_FROM_EMAIL` |
| `tests/conftest.py` | Add `NEXTAUTH_SECRET` to test env |

---

## Task 1: Install dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/package.json`

- [ ] **Step 1: Add Python dependencies**

```bash
cd /Users/yogamtchokni/Documents/Projects/econ-newsfeed
poetry add "python-jose[cryptography]" resend
```

- [ ] **Step 2: Add NextAuth.js**

```bash
cd /Users/yogamtchokni/Documents/Projects/econ-newsfeed/app
npm install next-auth@4
```

We use next-auth v4 (stable with Next.js 14 App Router support via route handlers).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml poetry.lock app/package.json app/package-lock.json
git commit -m "chore: add next-auth, python-jose, resend dependencies"
```

---

## Task 2: Database schema — users, follows, notification prefs

**Files:**
- Modify: `database/schema.py`

- [ ] **Step 1: Add table definitions to `_TABLE_DEFINITIONS` in `database/schema.py`**

Add these three entries after the `"paper_links"` entry in the `_TABLE_DEFINITIONS` dict (around line 346):

```python
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            google_id VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL,
            name VARCHAR(255),
            picture_url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_google_id (google_id),
            INDEX idx_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "user_follows": """
        CREATE TABLE IF NOT EXISTS user_follows (
            user_id INT NOT NULL,
            researcher_id INT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, researcher_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "user_notification_prefs": """
        CREATE TABLE IF NOT EXISTS user_notification_prefs (
            user_id INT PRIMARY KEY,
            digest_enabled BOOLEAN DEFAULT TRUE,
            last_digest_sent DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
```

- [ ] **Step 2: Add new tables to `_ALL_TABLES` list in `create_tables()` migration block**

In the `_ALL_TABLES` list (around line 453), add at the end (before the closing `]`):

```python
                        "users",
                        "user_follows",
                        "user_notification_prefs",
```

- [ ] **Step 3: Commit**

```bash
git add database/schema.py
git commit -m "feat: add users, user_follows, user_notification_prefs tables"
```

---

## Task 3: Database layer — user CRUD, follows, digest queries

**Files:**
- Create: `database/users.py`
- Modify: `database/__init__.py`

- [ ] **Step 1: Create `database/users.py`**

```python
"""User accounts, follows, and notification preferences."""
from __future__ import annotations

from datetime import datetime, timezone

from database.connection import execute_query, fetch_all, fetch_one, get_connection


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
```

- [ ] **Step 2: Update `database/__init__.py` — add re-exports**

Add this import block after the existing `from database.admin import ...` line (around line 90):

```python
from database.users import (
    get_or_create_user,
    get_user_by_google_id,
    add_follow,
    remove_follow,
    get_followed_researcher_ids,
    get_notification_prefs,
    update_notification_prefs,
    get_digest_recipients,
    get_feed_events_for_researchers,
    update_last_digest_sent,
    researcher_exists,
    generate_unsubscribe_token,
    verify_unsubscribe_token,
)
```

Add these to the `Database` facade class (after the `# Admin` section, around line 174):

```python
    # Users
    get_or_create_user = staticmethod(get_or_create_user)
    get_user_by_google_id = staticmethod(get_user_by_google_id)
    add_follow = staticmethod(add_follow)
    remove_follow = staticmethod(remove_follow)
    get_followed_researcher_ids = staticmethod(get_followed_researcher_ids)
    get_notification_prefs = staticmethod(get_notification_prefs)
    update_notification_prefs = staticmethod(update_notification_prefs)
    get_digest_recipients = staticmethod(get_digest_recipients)
    get_feed_events_for_researchers = staticmethod(get_feed_events_for_researchers)
    update_last_digest_sent = staticmethod(update_last_digest_sent)
    researcher_exists = staticmethod(researcher_exists)
    generate_unsubscribe_token = staticmethod(generate_unsubscribe_token)
    verify_unsubscribe_token = staticmethod(verify_unsubscribe_token)
```

- [ ] **Step 3: Commit**

```bash
git add database/users.py database/__init__.py
git commit -m "feat: add database layer for users, follows, notification prefs"
```

---

## Task 4: Backend auth dependencies

**Files:**
- Create: `auth.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Add `NEXTAUTH_SECRET` to test env in `tests/conftest.py`**

Add this line after the existing `os.environ.setdefault` calls (around line 18):

```python
os.environ.setdefault("NEXTAUTH_SECRET", "test-nextauth-secret-for-ci")
```

- [ ] **Step 2: Create `auth.py`**

```python
"""FastAPI authentication dependencies for NextAuth.js JWT verification."""
import logging
import os

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from database import Database, connection_scope

logger = logging.getLogger(__name__)

NEXTAUTH_SECRET = os.environ.get("NEXTAUTH_SECRET", "")
ALGORITHM = "HS256"


def _extract_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _decode_jwt(token: str) -> dict:
    """Decode and verify a NextAuth.js JWT."""
    return jwt.decode(token, NEXTAUTH_SECRET, algorithms=[ALGORITHM])


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency: require a valid JWT and return the user record.

    On first valid JWT, lazily creates the user row in MySQL.
    Raises 401 if no token or invalid token.
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = _decode_jwt(token)
    except JWTError as e:
        logger.debug("JWT verification failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")

    google_id = payload.get("sub")
    email = payload.get("email", "")
    name = payload.get("name")
    picture = payload.get("picture")

    if not google_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    with connection_scope():
        user = Database.get_or_create_user(
            google_id=google_id, email=email, name=name, picture_url=picture,
        )
    return user


async def get_optional_user(request: Request) -> dict | None:
    """FastAPI dependency: return user if valid JWT present, None otherwise.

    Never raises 401 — used for endpoints that behave differently when
    authenticated but don't require it.
    """
    token = _extract_token(request)
    if not token:
        return None

    try:
        payload = _decode_jwt(token)
    except JWTError:
        return None

    google_id = payload.get("sub")
    if not google_id:
        return None

    with connection_scope():
        user = Database.get_user_by_google_id(google_id)
    return user
```

- [ ] **Step 3: Write tests — `tests/test_auth.py`**

```python
"""Tests for auth.py JWT verification dependencies."""
import os
from unittest.mock import patch, MagicMock

import pytest
from jose import jwt

# Must be set before imports
NEXTAUTH_SECRET = os.environ.get("NEXTAUTH_SECRET", "test-nextauth-secret-for-ci")

from auth import _extract_token, _decode_jwt, get_current_user, get_optional_user


def _make_token(payload: dict) -> str:
    return jwt.encode(payload, NEXTAUTH_SECRET, algorithm="HS256")


class FakeRequest:
    def __init__(self, token: str | None = None):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self.headers = headers


def test_extract_token_present():
    req = FakeRequest("abc123")
    assert _extract_token(req) == "abc123"


def test_extract_token_missing():
    req = FakeRequest()
    assert _extract_token(req) is None


def test_decode_jwt_valid():
    token = _make_token({"sub": "google123", "email": "a@b.com"})
    payload = _decode_jwt(token)
    assert payload["sub"] == "google123"


def test_decode_jwt_invalid():
    from jose import JWTError
    with pytest.raises(JWTError):
        _decode_jwt("not.a.valid.token")


@pytest.mark.asyncio
async def test_get_current_user_no_token():
    from fastapi import HTTPException
    req = FakeRequest()
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_valid():
    token = _make_token({"sub": "g123", "email": "test@example.com", "name": "Test"})
    req = FakeRequest(token)
    fake_user = {"id": 1, "google_id": "g123", "email": "test@example.com", "name": "Test"}

    with patch("auth.Database") as mock_db, \
         patch("auth.connection_scope", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())):
        mock_db.get_or_create_user.return_value = fake_user
        user = await get_current_user(req)
        assert user["id"] == 1
        mock_db.get_or_create_user.assert_called_once()


@pytest.mark.asyncio
async def test_get_optional_user_no_token():
    req = FakeRequest()
    user = await get_optional_user(req)
    assert user is None


@pytest.mark.asyncio
async def test_get_optional_user_invalid_token():
    req = FakeRequest("bad.token")
    user = await get_optional_user(req)
    assert user is None
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/test_auth.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add auth.py tests/test_auth.py tests/conftest.py
git commit -m "feat: add FastAPI JWT auth dependencies with tests"
```

---

## Task 5: User API endpoints

**Files:**
- Modify: `api.py`
- Create: `tests/test_users_api.py`

- [ ] **Step 1: Add imports and user endpoints to `api.py`**

Add to the imports at the top of `api.py` (after the existing `from database import Database, connection_scope` line):

```python
from auth import get_current_user, get_optional_user
```

Add these endpoints after the existing admin endpoints section (after the `/api/admin/reactivate-url` endpoint, around line 443):

```python
# ---------------------------------------------------------------------------
# User endpoints (require authentication)
# ---------------------------------------------------------------------------

class NotificationPrefsUpdate(BaseModel):
    digest_enabled: bool


@app.get("/api/users/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return the current user's profile."""
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name"),
        "picture_url": user.get("picture_url"),
        "created_at": user.get("created_at"),
    }


@app.get("/api/users/following")
async def get_following(user: dict = Depends(get_current_user)):
    """List researcher IDs the current user follows."""
    with connection_scope():
        ids = Database.get_followed_researcher_ids(user["id"])
    return {"researcher_ids": ids}


@app.post("/api/users/follow/{researcher_id}", status_code=204)
async def follow_researcher(researcher_id: int, user: dict = Depends(get_current_user)):
    """Follow a researcher. Idempotent."""
    with connection_scope():
        if not Database.researcher_exists(researcher_id):
            raise HTTPException(status_code=404, detail="Researcher not found")
        Database.add_follow(user["id"], researcher_id)


@app.delete("/api/users/follow/{researcher_id}", status_code=204)
async def unfollow_researcher(researcher_id: int, user: dict = Depends(get_current_user)):
    """Unfollow a researcher. Idempotent."""
    with connection_scope():
        Database.remove_follow(user["id"], researcher_id)


@app.get("/api/users/notifications")
async def get_notifications(user: dict = Depends(get_current_user)):
    """Get the current user's notification preferences."""
    with connection_scope():
        prefs = Database.get_notification_prefs(user["id"])
    return {
        "digest_enabled": prefs["digest_enabled"],
        "last_digest_sent": prefs.get("last_digest_sent"),
    }


@app.patch("/api/users/notifications")
async def update_notifications(
    body: NotificationPrefsUpdate,
    user: dict = Depends(get_current_user),
):
    """Update notification preferences."""
    with connection_scope():
        Database.update_notification_prefs(user["id"], body.digest_enabled)
    return {"digest_enabled": body.digest_enabled}


@app.get("/api/users/unsubscribe")
async def unsubscribe(token: str = Query(...)):
    """One-click email unsubscribe via HMAC-signed token."""
    import os
    secret = os.environ.get("NEXTAUTH_SECRET", "")
    user_id = Database.verify_unsubscribe_token(token, secret)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired unsubscribe link")
    with connection_scope():
        Database.update_notification_prefs(user_id, digest_enabled=False)
    return {"message": "You have been unsubscribed from the weekly digest."}
```

- [ ] **Step 2: Add `following` preset to `/api/publications` endpoint**

In the `list_publications` function (around line 464), update the `valid_presets` set:

```python
    valid_presets = {"top20", "following"}
```

Then add the following block after the `valid_presets` check (around line 482), before the `since_dt` parsing:

```python
    # Handle "following" preset — requires optional auth
    followed_ids = None
    if preset == "following":
        from auth import get_optional_user as _get_opt_user
        opt_user = await _get_opt_user(request)
        if opt_user:
            with connection_scope():
                followed_ids = Database.get_followed_researcher_ids(opt_user["id"])
            if not followed_ids:
                return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}
        else:
            return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}
```

**Important:** The `list_publications` function must become `async` since it now `await`s. Change its signature from `def list_publications(` to `async def list_publications(`.

Then pass `followed_ids` to `search_feed_events`. Add `followed_ids=followed_ids` to the call:

```python
        rows, total = Database.search_feed_events(
            year=year, researcher_id=researcher_id,
            status_list=status_list or None,
            since=since_dt, institution_list=institution_list or None,
            preset=preset, search=search, event_type=event_type,
            jel_code=jel_code, offset=offset, limit=per_page,
            followed_ids=followed_ids,
        )
```

- [ ] **Step 3: Add `followed_ids` parameter to `search_feed_events` in `database/papers.py`**

Update the function signature (around line 226) to accept the new parameter:

```python
def search_feed_events(
    *,
    year=None,
    researcher_id=None,
    status_list=None,
    since: datetime | None = None,
    institution_list=None,
    preset=None,
    search=None,
    event_type=None,
    jel_code=None,
    offset: int = 0,
    limit: int = 20,
    followed_ids: list[int] | None = None,
) -> tuple[list[dict], int]:
```

Add this filter block after the existing `if researcher_id:` block (around line 265):

```python
    if followed_ids:
        placeholders = ",".join(["%s"] * len(followed_ids))
        conditions.append(
            f"EXISTS (SELECT 1 FROM authorship WHERE publication_id = p.id "
            f"AND researcher_id IN ({placeholders}))"
        )
        params.extend(followed_ids)
```

- [ ] **Step 4: Write tests — `tests/test_users_api.py`**

```python
"""Tests for /api/users/* endpoints."""
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

import pytest
from jose import jwt

NEXTAUTH_SECRET = "test-nextauth-secret-for-ci"


def _make_token(sub="g123", email="test@example.com", name="Test User"):
    return jwt.encode(
        {"sub": sub, "email": email, "name": name},
        NEXTAUTH_SECRET,
        algorithm="HS256",
    )


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def client():
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
        patch("api.connection_scope", _noop_connection_scope),
        patch("auth.connection_scope", _noop_connection_scope),
    ):
        from api import app
        from fastapi.testclient import TestClient
        with TestClient(app) as c:
            yield c


FAKE_USER = {"id": 1, "google_id": "g123", "email": "test@example.com",
             "name": "Test User", "picture_url": None, "created_at": "2026-01-01T00:00:00"}


def _auth_header():
    return {"Authorization": f"Bearer {_make_token()}"}


def test_me_unauthenticated(client):
    resp = client.get("/api/users/me")
    assert resp.status_code == 401


def test_me_authenticated(client):
    with patch("auth.Database") as mock_db:
        mock_db.get_or_create_user.return_value = FAKE_USER
        resp = client.get("/api/users/me", headers=_auth_header())
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


def test_follow_researcher(client):
    with patch("auth.Database") as auth_db, \
         patch("api.Database") as api_db:
        auth_db.get_or_create_user.return_value = FAKE_USER
        api_db.researcher_exists.return_value = True
        resp = client.post("/api/users/follow/42", headers=_auth_header())
    assert resp.status_code == 204


def test_follow_nonexistent_researcher(client):
    with patch("auth.Database") as auth_db, \
         patch("api.Database") as api_db:
        auth_db.get_or_create_user.return_value = FAKE_USER
        api_db.researcher_exists.return_value = False
        resp = client.post("/api/users/follow/999", headers=_auth_header())
    assert resp.status_code == 404


def test_unfollow_researcher(client):
    with patch("auth.Database") as auth_db, \
         patch("api.Database") as api_db:
        auth_db.get_or_create_user.return_value = FAKE_USER
        resp = client.delete("/api/users/follow/42", headers=_auth_header())
    assert resp.status_code == 204


def test_get_following(client):
    with patch("auth.Database") as auth_db, \
         patch("api.Database") as api_db:
        auth_db.get_or_create_user.return_value = FAKE_USER
        api_db.get_followed_researcher_ids.return_value = [1, 5, 12]
        resp = client.get("/api/users/following", headers=_auth_header())
    assert resp.status_code == 200
    assert resp.json()["researcher_ids"] == [1, 5, 12]


def test_get_notifications(client):
    with patch("auth.Database") as auth_db, \
         patch("api.Database") as api_db:
        auth_db.get_or_create_user.return_value = FAKE_USER
        api_db.get_notification_prefs.return_value = {
            "digest_enabled": True, "last_digest_sent": None
        }
        resp = client.get("/api/users/notifications", headers=_auth_header())
    assert resp.status_code == 200
    assert resp.json()["digest_enabled"] is True


def test_update_notifications(client):
    with patch("auth.Database") as auth_db, \
         patch("api.Database") as api_db:
        auth_db.get_or_create_user.return_value = FAKE_USER
        resp = client.patch(
            "/api/users/notifications",
            json={"digest_enabled": False},
            headers=_auth_header(),
        )
    assert resp.status_code == 200
    assert resp.json()["digest_enabled"] is False


def test_unsubscribe_valid(client):
    import database.users as users_mod
    token = users_mod.generate_unsubscribe_token(1, NEXTAUTH_SECRET)
    with patch("api.Database") as api_db:
        api_db.verify_unsubscribe_token.return_value = 1
        resp = client.get(f"/api/users/unsubscribe?token={token}")
    assert resp.status_code == 200


def test_unsubscribe_invalid(client):
    with patch("api.Database") as api_db:
        api_db.verify_unsubscribe_token.return_value = None
        resp = client.get("/api/users/unsubscribe?token=bad.token")
    assert resp.status_code == 400
```

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/test_users_api.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add api.py database/papers.py tests/test_users_api.py
git commit -m "feat: add user API endpoints (follow, notifications, unsubscribe)"
```

---

## Task 6: Weekly digest email

**Files:**
- Create: `digest.py`
- Modify: `scheduler.py`
- Create: `tests/test_digest.py`

- [ ] **Step 1: Create `digest.py`**

```python
"""Weekly digest email — query events, render HTML, send via Resend."""
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

import resend

from database import Database

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DIGEST_FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "digest@econ-newsfeed.com")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
NEXTAUTH_SECRET = os.environ.get("NEXTAUTH_SECRET", "")


def _render_digest_html(events: list[dict], user_name: str | None,
                        unsubscribe_url: str, since: datetime, until: datetime) -> str:
    """Render digest events as an HTML email body grouped by researcher."""
    by_researcher: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        key = f"{ev['first_name']} {ev['last_name']}"
        by_researcher[key].append(ev)

    since_str = since.strftime("%B %d")
    until_str = until.strftime("%B %d, %Y")
    greeting = f"Hi {user_name}" if user_name else "Hi"

    sections = []
    for researcher_name, researcher_events in sorted(by_researcher.items()):
        items = []
        seen_papers = set()
        for ev in researcher_events:
            if ev['paper_id'] in seen_papers:
                continue
            seen_papers.add(ev['paper_id'])
            status_label = (ev.get('status') or 'unknown').replace('_', ' ').title()
            paper_url = f"{FRONTEND_URL}/papers/{ev['paper_id']}"
            items.append(
                f'<li style="margin-bottom:8px;">'
                f'<a href="{paper_url}" style="color:#2563eb;text-decoration:none;">{ev["title"]}</a>'
                f' <span style="color:#6b7280;font-size:13px;">({status_label})</span>'
                f'</li>'
            )
        if items:
            sections.append(
                f'<h3 style="margin:16px 0 8px;color:#1a2332;font-size:16px;">{researcher_name}</h3>'
                f'<ul style="padding-left:20px;margin:0;">{"".join(items)}</ul>'
            )

    body_html = "".join(sections) if sections else "<p>No new activity this week.</p>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#1a2332;">
  <h1 style="font-size:20px;color:#1a2332;">Econ Newsfeed — Weekly Digest</h1>
  <p style="color:#6b7280;font-size:14px;">{since_str} – {until_str}</p>
  <p>{greeting},</p>
  <p>Here's what's new from the researchers you follow:</p>
  {body_html}
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
  <p style="font-size:12px;color:#9ca3af;">
    <a href="{FRONTEND_URL}" style="color:#2563eb;">Manage your follows</a> ·
    <a href="{unsubscribe_url}" style="color:#2563eb;">Unsubscribe</a>
  </p>
</body>
</html>"""


def _send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set, skipping email to %s", to)
        return False
    try:
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": DIGEST_FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        return True
    except Exception as e:
        logger.error("Failed to send digest to %s: %s: %s", to, type(e).__name__, e)
        return False


def run_weekly_digest() -> int:
    """Send weekly digest to all eligible users. Returns number of emails sent."""
    now = datetime.now(timezone.utc)
    recipients = Database.get_digest_recipients()
    if not recipients:
        logger.info("Digest: no eligible recipients")
        return 0

    sent = 0
    for user in recipients:
        since = user.get("last_digest_sent") or user["created_at"]
        if isinstance(since, str):
            since = datetime.fromisoformat(since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        events = Database.get_feed_events_for_researchers(
            user["researcher_ids"], since
        )
        if not events:
            continue

        unsubscribe_token = Database.generate_unsubscribe_token(
            user["id"], NEXTAUTH_SECRET
        )
        unsubscribe_url = (
            f"{FRONTEND_URL}/api/users/unsubscribe?token={unsubscribe_token}"
        )

        since_str = since.strftime("%B %d")
        until_str = now.strftime("%B %d")
        subject = f"Econ Newsfeed — Weekly Digest ({since_str} – {until_str})"

        html = _render_digest_html(
            events, user.get("name"), unsubscribe_url, since, now
        )

        if _send_email(user["email"], subject, html):
            Database.update_last_digest_sent(user["id"], now)
            sent += 1
            logger.info("Digest sent to %s (%d events)", user["email"], len(events))

    logger.info("Weekly digest complete: %d/%d emails sent", sent, len(recipients))
    return sent
```

- [ ] **Step 2: Register digest job in `scheduler.py`**

Add this import at the top of `scheduler.py` (after the existing imports, around line 16):

```python
DIGEST_ENABLED = os.environ.get('RESEND_API_KEY', '') != ''
```

In the `start_scheduler()` function, add the digest job after the scrape job is added (after `_scheduler.start()`, around line 491):

```python
    if DIGEST_ENABLED:
        from digest import run_weekly_digest
        _scheduler.add_job(
            run_weekly_digest,
            'cron',
            day_of_week='mon',
            hour=8,
            minute=0,
            id='weekly_digest',
        )
        logger.info("Weekly digest job scheduled for Mondays 8:00 UTC")
```

- [ ] **Step 3: Write tests — `tests/test_digest.py`**

```python
"""Tests for digest.py — email rendering and digest job."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from digest import _render_digest_html, run_weekly_digest


def _sample_events():
    return [
        {
            "event_type": "new_paper",
            "old_status": None,
            "new_status": "working_paper",
            "created_at": datetime(2026, 6, 8, tzinfo=timezone.utc),
            "paper_id": 100,
            "title": "Monetary Policy in Small Open Economies",
            "status": "working_paper",
            "year": "2026",
            "venue": None,
            "researcher_id": 1,
            "first_name": "Alice",
            "last_name": "Smith",
        },
        {
            "event_type": "status_change",
            "old_status": "working_paper",
            "new_status": "published",
            "created_at": datetime(2026, 6, 9, tzinfo=timezone.utc),
            "paper_id": 101,
            "title": "Trade and Development",
            "status": "published",
            "year": "2025",
            "venue": "AER",
            "researcher_id": 2,
            "first_name": "Bob",
            "last_name": "Jones",
        },
    ]


def test_render_digest_html_groups_by_researcher():
    html = _render_digest_html(
        _sample_events(),
        "Test User",
        "https://example.com/unsub",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 8, tzinfo=timezone.utc),
    )
    assert "Alice Smith" in html
    assert "Bob Jones" in html
    assert "Monetary Policy" in html
    assert "Trade and Development" in html
    assert "Unsubscribe" in html


def test_render_digest_html_empty_events():
    html = _render_digest_html(
        [],
        None,
        "https://example.com/unsub",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 8, tzinfo=timezone.utc),
    )
    assert "No new activity" in html


def test_run_weekly_digest_no_recipients():
    with patch("digest.Database") as mock_db:
        mock_db.get_digest_recipients.return_value = []
        sent = run_weekly_digest()
    assert sent == 0


def test_run_weekly_digest_sends_email():
    recipient = {
        "id": 1,
        "email": "user@example.com",
        "name": "Test",
        "last_digest_sent": None,
        "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        "researcher_ids": [1, 2],
    }
    with patch("digest.Database") as mock_db, \
         patch("digest._send_email", return_value=True) as mock_send:
        mock_db.get_digest_recipients.return_value = [recipient]
        mock_db.get_feed_events_for_researchers.return_value = _sample_events()
        mock_db.generate_unsubscribe_token.return_value = "1.abc123"
        sent = run_weekly_digest()

    assert sent == 1
    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args[0][0] == "user@example.com"
    assert "Weekly Digest" in call_args[0][1]
    mock_db.update_last_digest_sent.assert_called_once()


def test_run_weekly_digest_skips_empty_events():
    recipient = {
        "id": 1,
        "email": "user@example.com",
        "name": "Test",
        "last_digest_sent": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        "researcher_ids": [1],
    }
    with patch("digest.Database") as mock_db:
        mock_db.get_digest_recipients.return_value = [recipient]
        mock_db.get_feed_events_for_researchers.return_value = []
        sent = run_weekly_digest()
    assert sent == 0
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/test_digest.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add digest.py scheduler.py tests/test_digest.py
git commit -m "feat: add weekly digest email with Resend + scheduler integration"
```

---

## Task 7: NextAuth.js setup (frontend auth)

**Files:**
- Create: `app/src/app/api/auth/[...nextauth]/route.ts`
- Create: `app/src/lib/auth.tsx`
- Modify: `app/src/app/layout.tsx`

- [ ] **Step 1: Create NextAuth route handler**

Create directory first:
```bash
mkdir -p app/src/app/api/auth/\[...nextauth\]
```

Create `app/src/app/api/auth/[...nextauth]/route.ts`:

```typescript
import NextAuth from "next-auth";
import GoogleProvider from "next-auth/providers/google";

const handler = NextAuth({
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID || "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET || "",
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account && profile) {
        token.sub = profile.sub;
        token.email = profile.email;
        token.name = profile.name;
        token.picture = (profile as { picture?: string }).picture;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        (session.user as { id?: string }).id = token.sub;
      }
      return session;
    },
  },
});

export { handler as GET, handler as POST };
```

- [ ] **Step 2: Create auth context provider — `app/src/lib/auth.tsx`**

```tsx
"use client";

import { SessionProvider, useSession, getSession } from "next-auth/react";
import type { ReactNode } from "react";

export function AuthProvider({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

export function useAuth() {
  const { data: session, status } = useSession();
  return {
    user: session?.user ?? null,
    isAuthenticated: status === "authenticated",
    isLoading: status === "loading",
  };
}

export async function getAuthToken(): Promise<string | null> {
  const session = await getSession();
  if (!session) return null;
  // NextAuth exposes the JWT as the session token in the cookie.
  // For API calls to the backend, we fetch a fresh token from the session endpoint.
  const res = await fetch("/api/auth/session");
  if (!res.ok) return null;
  const data = await res.json();
  return data?.accessToken ?? null;
}
```

Wait — NextAuth v4 with JWT strategy doesn't expose the raw JWT to the client by default. We need to expose it via a callback. Let me update the route handler and the auth lib.

**Updated `app/src/app/api/auth/[...nextauth]/route.ts`:**

```typescript
import NextAuth from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import { JWT, encode } from "next-auth/jwt";

const NEXTAUTH_SECRET = process.env.NEXTAUTH_SECRET || "";

const handler = NextAuth({
  secret: NEXTAUTH_SECRET,
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID || "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET || "",
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account && profile) {
        token.sub = profile.sub;
        token.email = profile.email;
        token.name = profile.name;
        token.picture = (profile as { picture?: string }).picture;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        (session.user as { id?: string }).id = token.sub;
        (session.user as { accessToken?: string }).accessToken = await encode({
          token,
          secret: NEXTAUTH_SECRET,
        });
      }
      return session;
    },
  },
});

export { handler as GET, handler as POST };
```

**Updated `app/src/lib/auth.tsx`:**

```tsx
"use client";

import { SessionProvider, useSession } from "next-auth/react";
import type { ReactNode } from "react";

export function AuthProvider({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

export function useAuth() {
  const { data: session, status } = useSession();
  const user = session?.user as
    | (typeof session extends { user: infer U } ? U : never) & { id?: string; accessToken?: string }
    | null;

  return {
    user: user ?? null,
    isAuthenticated: status === "authenticated",
    isLoading: status === "loading",
    accessToken: user?.accessToken ?? null,
  };
}
```

- [ ] **Step 3: Wrap layout with `AuthProvider`**

Modify `app/src/app/layout.tsx`. Add the import and wrap children:

```tsx
import type { Metadata } from "next";
import { Source_Serif_4, DM_Sans } from "next/font/google";
import "./globals.css";
import Header from "@/components/Header";
import { AuthProvider } from "@/lib/auth";

const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-source-serif",
  display: "swap",
});

const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-dm-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Econ Newsfeed",
  description:
    "Track new publications from economists' personal websites",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${sourceSerif.variable} ${dmSans.variable}`}>
      <body className="antialiased">
        <AuthProvider>
          <Header />
          <main className="mx-auto max-w-4xl px-4 sm:px-6 lg:px-8 py-8">{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add app/src/app/api/auth/ app/src/lib/auth.tsx app/src/app/layout.tsx
git commit -m "feat: add NextAuth.js Google provider with JWT strategy"
```

---

## Task 8: Frontend — Header auth UI (sign in / user menu)

**Files:**
- Create: `app/src/components/UserMenu.tsx`
- Modify: `app/src/components/Header.tsx`

- [ ] **Step 1: Create `app/src/components/UserMenu.tsx`**

```tsx
"use client";

import { useState, useRef, useEffect } from "react";
import { signOut } from "next-auth/react";
import { useAuth } from "@/lib/auth";

export default function UserMenu() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  if (!user) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-full hover:opacity-80 transition-opacity"
      >
        {user.image ? (
          <img
            src={user.image}
            alt=""
            className="w-7 h-7 rounded-full"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-7 h-7 rounded-full bg-[var(--accent)] flex items-center justify-center text-white text-xs font-bold">
            {(user.name || user.email || "?")[0].toUpperCase()}
          </div>
        )}
        <span className="font-sans text-xs text-[#c5cdd8] hidden sm:inline">
          {user.name?.split(" ")[0]}
        </span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-48 rounded-lg bg-[var(--bg-card)] shadow-lg border border-[var(--border)] py-1 z-50">
          <div className="px-3 py-2 border-b border-[var(--border)]">
            <p className="font-sans text-sm font-medium text-[var(--text-primary)] truncate">
              {user.name}
            </p>
            <p className="font-sans text-xs text-[var(--text-muted)] truncate">
              {user.email}
            </p>
          </div>
          <button
            onClick={() => {
              setOpen(false);
              signOut();
            }}
            className="w-full text-left px-3 py-2 font-sans text-sm text-[var(--text-secondary)] hover:bg-[var(--border-light)] transition-colors"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Update `app/src/components/Header.tsx`**

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signIn } from "next-auth/react";
import { useAuth } from "@/lib/auth";
import UserMenu from "@/components/UserMenu";

export default function Header() {
  const pathname = usePathname();
  const { isAuthenticated, isLoading } = useAuth();

  return (
    <header className="bg-[var(--bg-header)] sticky top-0 z-50 shadow-[0_2px_16px_rgba(26,35,50,0.18)]">
      <div className="mx-auto max-w-4xl px-4 sm:px-6 lg:px-8 py-5 flex items-center justify-between">
        <Link href="/" className="text-xl font-bold tracking-tight">
          <span className="sr-only">Econ Newsfeed</span>
          <span aria-hidden="true">
            <span className="text-[#f0ece4]">Econ</span>{" "}
            <span className="text-[var(--accent)]">Newsfeed</span>
          </span>
        </Link>
        <div className="flex items-center gap-6">
          <nav className="flex gap-8 font-sans text-xs font-semibold uppercase tracking-widest">
            <Link
              href="/"
              className={`py-1 border-b-2 transition-colors ${
                pathname === "/"
                  ? "text-[#f0ece4] border-[var(--accent)]"
                  : "text-[#8896a7] border-transparent hover:text-[#f0ece4]"
              }`}
            >
              Feed
            </Link>
            <Link
              href="/researchers"
              className={`py-1 border-b-2 transition-colors ${
                pathname?.startsWith("/researchers")
                  ? "text-[#f0ece4] border-[var(--accent)]"
                  : "text-[#8896a7] border-transparent hover:text-[#f0ece4]"
              }`}
            >
              Researchers
            </Link>
          </nav>
          {!isLoading && (
            isAuthenticated ? (
              <UserMenu />
            ) : (
              <button
                onClick={() => signIn("google")}
                className="font-sans text-xs font-semibold text-[#c5cdd8] hover:text-[#f0ece4] transition-colors"
              >
                Sign in
              </button>
            )
          )}
        </div>
      </div>
    </header>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add app/src/components/UserMenu.tsx app/src/components/Header.tsx
git commit -m "feat: add sign-in button and user dropdown menu in header"
```

---

## Task 9: Frontend — Follow button + API hooks

**Files:**
- Modify: `app/src/lib/types.ts`
- Modify: `app/src/lib/api.ts`
- Create: `app/src/components/FollowButton.tsx`

- [ ] **Step 1: Add types to `app/src/lib/types.ts`**

Add at the end of the file:

```typescript
export interface UserFollowing {
  researcher_ids: number[];
}

export interface NotificationPrefs {
  digest_enabled: boolean;
  last_digest_sent: string | null;
}
```

- [ ] **Step 2: Add auth-aware API functions to `app/src/lib/api.ts`**

Add these imports at the top (after existing imports):

```typescript
import type { UserFollowing, NotificationPrefs } from "./types";
```

Add these functions at the end of the file:

```typescript
// --- Authenticated API helpers ---

async function fetchJsonAuth<T>(url: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${token}`,
    },
  });
  if (res.status === 401) throw new Error("UNAUTHORIZED");
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

export function useFollowing(token: string | null) {
  return useSWR<UserFollowing>(
    token ? ["/api/users/following", token] : null,
    ([url, t]: [string, string]) => fetchJsonAuth(url, t),
  );
}

export async function followResearcher(researcherId: number, token: string): Promise<void> {
  await fetch(`/api/users/follow/${researcherId}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function unfollowResearcher(researcherId: number, token: string): Promise<void> {
  await fetch(`/api/users/follow/${researcherId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function useNotificationPrefs(token: string | null) {
  return useSWR<NotificationPrefs>(
    token ? ["/api/users/notifications", token] : null,
    ([url, t]: [string, string]) => fetchJsonAuth(url, t),
  );
}

export async function updateNotificationPrefs(
  prefs: { digest_enabled: boolean },
  token: string,
): Promise<void> {
  await fetch("/api/users/notifications", {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(prefs),
  });
}
```

- [ ] **Step 3: Create `app/src/components/FollowButton.tsx`**

```tsx
"use client";

import { useCallback } from "react";
import { mutate } from "swr";
import { useAuth } from "@/lib/auth";
import { useFollowing, followResearcher, unfollowResearcher } from "@/lib/api";

export default function FollowButton({
  researcherId,
  size = "sm",
}: {
  researcherId: number;
  size?: "sm" | "md";
}) {
  const { isAuthenticated, accessToken } = useAuth();
  const { data } = useFollowing(accessToken);

  const isFollowing = data?.researcher_ids?.includes(researcherId) ?? false;

  const handleClick = useCallback(
    async (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!accessToken) return;

      const key = ["/api/users/following", accessToken];
      const currentIds = data?.researcher_ids ?? [];

      // Optimistic update
      const newIds = isFollowing
        ? currentIds.filter((id) => id !== researcherId)
        : [...currentIds, researcherId];
      mutate(key, { researcher_ids: newIds }, false);

      try {
        if (isFollowing) {
          await unfollowResearcher(researcherId, accessToken);
        } else {
          await followResearcher(researcherId, accessToken);
        }
        mutate(key);
      } catch {
        mutate(key);
      }
    },
    [accessToken, data, isFollowing, researcherId],
  );

  if (!isAuthenticated) return null;

  const sizeClasses =
    size === "md"
      ? "px-4 py-1.5 text-sm"
      : "px-2.5 py-0.5 text-xs";

  return (
    <button
      onClick={handleClick}
      className={`relative z-[1] font-sans font-semibold rounded-full transition-all ${sizeClasses} ${
        isFollowing
          ? "bg-[var(--accent)] text-white hover:bg-red-600"
          : "border border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
      }`}
    >
      {isFollowing ? "Following" : "Follow"}
    </button>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add app/src/lib/types.ts app/src/lib/api.ts app/src/components/FollowButton.tsx
git commit -m "feat: add follow button component and auth-aware API hooks"
```

---

## Task 10: Frontend — Integrate follow buttons into researcher pages

**Files:**
- Modify: `app/src/components/ResearcherCard.tsx`
- Modify: `app/src/app/researchers/[id]/ResearcherDetailContent.tsx`

- [ ] **Step 1: Add follow button to `ResearcherCard.tsx`**

```tsx
import Link from "next/link";
import type { Researcher } from "@/lib/types";
import FollowButton from "@/components/FollowButton";

export default function ResearcherCard({
  researcher,
}: {
  researcher: Researcher;
}) {
  return (
    <div className="relative block rounded-lg bg-[var(--bg-card)] shadow-card p-5 hover:shadow-card-hover hover:-translate-y-px transition-all duration-200">
      {/* Stretched link covers the card for navigation */}
      <Link
        href={`/researchers/${researcher.id}`}
        className="absolute inset-0 z-0"
        aria-label={`${researcher.first_name} ${researcher.last_name}`}
        tabIndex={-1}
      />

      <div className="flex items-start justify-between gap-3">
        <h3 className="font-serif font-semibold text-[var(--text-primary)] text-lg">
          <Link href={`/researchers/${researcher.id}`} className="relative z-[1]">
            {researcher.first_name} {researcher.last_name}
          </Link>
        </h3>
        <FollowButton researcherId={researcher.id} size="sm" />
      </div>
      {(researcher.position || researcher.affiliation) && (
        <p className="mt-1 font-sans text-sm text-[var(--text-secondary)]">
          {researcher.position}
          {researcher.position && researcher.affiliation && ", "}
          {researcher.affiliation}
        </p>
      )}
      <p className="mt-1.5 font-sans text-sm text-[var(--text-muted)]">
        {researcher.publication_count} publications tracked
      </p>
      {researcher.fields?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {researcher.fields.map((field) => (
            <span
              key={field.id}
              className="font-sans text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)] bg-[var(--border-light)] px-2 py-0.5 rounded-full"
            >
              {field.name}
            </span>
          ))}
        </div>
      )}
      {researcher.website_url && (
        <p className="relative z-[1] mt-2">
          <a
            href={researcher.website_url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-sans text-sm text-[var(--link)] hover:underline"
          >
            Personal website &rarr;
          </a>
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add follow button to `ResearcherDetailContent.tsx`**

Add the import at the top:

```typescript
import FollowButton from "@/components/FollowButton";
```

In the hero card section, add the follow button next to the researcher name. Replace the existing `<div className="flex items-start justify-between gap-4">` block (around line 53-65) with:

```tsx
        <div className="flex items-start justify-between gap-4">
          <h1 className="font-serif text-2xl font-bold text-[var(--text-primary)]">
            {researcher.first_name} {researcher.last_name}
          </h1>
          <div className="flex items-center gap-3 shrink-0">
            <FollowButton researcherId={id} size="md" />
            {researcher.website_url && (
              <a href={researcher.website_url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 font-sans text-sm text-[var(--link)] hover:text-[var(--accent)] transition-colors">
                Personal website
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
                </svg>
              </a>
            )}
          </div>
        </div>
```

And remove the standalone website link block that follows (the old one outside the flex container, if it was previously after the `</div>`).

- [ ] **Step 3: Commit**

```bash
git add app/src/components/ResearcherCard.tsx app/src/app/researchers/\[id\]/ResearcherDetailContent.tsx
git commit -m "feat: add follow buttons to researcher card and detail page"
```

---

## Task 11: Frontend — "My Feed" toggle on newsfeed

**Files:**
- Modify: `app/src/app/NewsfeedContent.tsx`

- [ ] **Step 1: Add "My Feed" toggle to the filter bar**

At the top of `NewsfeedContent.tsx`, add the import:

```typescript
import { useAuth } from "@/lib/auth";
```

Inside the `NewsfeedContent` component (before the return), add:

```typescript
  const { isAuthenticated } = useAuth();
```

In the `FilterBar` component, add a `showMyFeed` prop and render a toggle button. Add this as the first element inside the filter bar (before the tab row):

In the `FilterBar` function signature, add `showMyFeed: boolean`:

```typescript
function FilterBar({
  filters,
  onChange,
  activeTab,
  onTabChange,
  showMyFeed,
}: {
  filters: FeedFilters;
  onChange: (next: FeedFilters) => void;
  activeTab: TabValue;
  onTabChange: (tab: TabValue) => void;
  showMyFeed: boolean;
}) {
```

Inside the `FilterBar` return, add the "My Feed" toggle before the existing filter row. Add it just before the `<div className="flex items-center gap-3 flex-wrap">` line that contains the "Filter" label:

```tsx
          {showMyFeed && (
            <button
              onClick={() => {
                const isActive = filters.preset === "following";
                onChange({
                  ...filters,
                  preset: isActive ? undefined : "following",
                  institution: isActive ? filters.institution : undefined,
                });
              }}
              className={`font-sans text-xs font-semibold px-3 py-1 rounded-full transition-all ${
                filters.preset === "following"
                  ? "bg-[var(--accent)] text-white"
                  : "border border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
              }`}
            >
              My Feed
            </button>
          )}
```

When passing `FilterBar` from the parent component, add the `showMyFeed` prop:

```tsx
<FilterBar
  filters={filters}
  onChange={handleFilterChange}
  activeTab={activeTab}
  onTabChange={handleTabChange}
  showMyFeed={isAuthenticated}
/>
```

- [ ] **Step 2: Commit**

```bash
git add app/src/app/NewsfeedContent.tsx
git commit -m "feat: add 'My Feed' toggle to newsfeed filter bar"
```

---

## Task 12: Infrastructure — env vars, Docker, CSP

**Files:**
- Modify: `.env.example`
- Modify: `.dockerignore`
- Modify: `docker-compose.prod.yml`
- Modify: `docker-compose.yml`
- Modify: `app/next.config.mjs`

- [ ] **Step 1: Update `.env.example`**

Add these lines at the end:

```bash

# Authentication (Google SSO via NextAuth.js)
NEXTAUTH_SECRET=generate-a-random-secret-here
NEXTAUTH_URL=http://localhost:3000
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Email digest (Resend)
RESEND_API_KEY=
DIGEST_FROM_EMAIL=digest@econ-newsfeed.com
```

- [ ] **Step 2: Update `.dockerignore`**

Add these lines after the existing `!scheduler.py` line:

```
!auth.py
!digest.py
```

- [ ] **Step 3: Update `docker-compose.yml`**

Add to the `api` service `environment` block:

```yaml
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET:-}
```

- [ ] **Step 4: Update `docker-compose.prod.yml`**

Add to the `api` service `environment` block:

```yaml
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET:?NEXTAUTH_SECRET is required}
      RESEND_API_KEY: ${RESEND_API_KEY:-}
      DIGEST_FROM_EMAIL: ${DIGEST_FROM_EMAIL:-digest@econ-newsfeed.com}
```

- [ ] **Step 5: Update CSP in `app/next.config.mjs`**

In the Content-Security-Policy header value, update the `connect-src` directive to allow Google OAuth:

Change:
```javascript
"connect-src 'self'",
```
To:
```javascript
"connect-src 'self' https://accounts.google.com",
```

Also add `img-src` allowance for Google profile pictures. Change:
```javascript
"img-src 'self' data: https:",
```
This already allows `https:` so no change needed for images.

- [ ] **Step 6: Commit**

```bash
git add .env.example .dockerignore docker-compose.yml docker-compose.prod.yml app/next.config.mjs
git commit -m "chore: add auth/digest env vars, Docker config, CSP updates"
```

---

## Task 13: Run full test suite

- [ ] **Step 1: Run Python tests**

```bash
poetry run pytest -v
```

Expected: all existing tests pass, plus new tests from `test_auth.py`, `test_users_api.py`, `test_digest.py`.

- [ ] **Step 2: Run frontend type check**

```bash
cd app && npx tsc --noEmit
```

Expected: no type errors.

- [ ] **Step 3: Fix any failures**

Address any test failures or type errors before proceeding.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve test/type-check issues from SSO feature"
```
