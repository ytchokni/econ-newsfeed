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
