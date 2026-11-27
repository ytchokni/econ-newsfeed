"""Digest unsubscribe: tokens, RFC 8058 one-click POST, safe GET landing, headers."""
import hashlib
import hmac
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

import backend.database.users as users_mod
import backend.digest as digest
from backend.database.users import (
    generate_unsubscribe_token,
    set_digest_enabled_by_token,
    verify_unsubscribe_token,
)

SECRET = "test-nextauth-secret-for-ci"


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def client():
    with (
        patch("backend.api.create_tables"),
        patch("backend.api.start_scheduler"),
        patch("backend.api.shutdown_scheduler"),
        patch("backend.api.connection_scope", _noop_connection_scope),
        patch("backend.auth.connection_scope", _noop_connection_scope),
    ):
        from backend.api import app, limiter
        from fastapi.testclient import TestClient
        limiter.reset()
        with TestClient(app, follow_redirects=False) as c:
            yield c


@pytest.fixture
def set_digest():
    with patch("backend.api.set_digest_enabled_by_token") as mock_set:
        yield mock_set


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def test_token_round_trip():
    # Stateless HMAC with no timestamp: links in months-old emails keep working
    # for as long as NEXTAUTH_SECRET is unchanged.
    token = generate_unsubscribe_token(42, SECRET)
    assert token.startswith("u2.42.")
    assert verify_unsubscribe_token(token, SECRET) == 42


def test_legacy_tokens_from_already_sent_digests_still_verify():
    legacy_sig = hmac.new(SECRET.encode(), b"42", hashlib.sha256).hexdigest()
    assert verify_unsubscribe_token(f"42.{legacy_sig}", SECRET) == 42


@pytest.mark.parametrize("token", [
    "",
    "garbage",
    "u2.42",
    "u2.42.deadbeef",
    "u2.-1.abc",
    "u2.042.abc",
    "x" * 500,
])
def test_malformed_tokens_are_rejected(token):
    assert verify_unsubscribe_token(token, SECRET) is None


def test_token_is_bound_to_its_user():
    token = generate_unsubscribe_token(42, SECRET)
    signature = token.split(".", 2)[2]
    assert verify_unsubscribe_token(f"u2.43.{signature}", SECRET) is None
    # The new signature is purpose-bound, so it is not a valid legacy token either.
    assert verify_unsubscribe_token(f"42.{signature}", SECRET) is None


def test_token_requires_the_right_secret():
    token = generate_unsubscribe_token(42, SECRET)
    assert verify_unsubscribe_token(token, "another-secret") is None


def test_empty_secret_never_produces_or_accepts_tokens():
    forged = hmac.new(b"", b"42", hashlib.sha256).hexdigest()
    assert verify_unsubscribe_token(f"42.{forged}", "") is None
    with pytest.raises(ValueError):
        generate_unsubscribe_token(42, "")


def test_set_digest_by_token_only_touches_digest_flag_of_existing_user():
    with patch.object(users_mod, "execute_query") as execute:
        set_digest_enabled_by_token(42, False)
    sql, params = execute.call_args.args
    assert sql.startswith("INSERT INTO user_notification_prefs (user_id, digest_enabled)")
    assert "FROM users WHERE id = %s" in sql
    assert "ON DUPLICATE KEY UPDATE digest_enabled = %s" in sql
    assert params == (False, 42, False)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_get_redirects_to_confirmation_page_without_unsubscribing(client, set_digest):
    token = generate_unsubscribe_token(42, SECRET)
    resp = client.get(f"/api/users/unsubscribe?token={token}")
    assert resp.status_code == 303
    location = urlparse(resp.headers["location"])
    assert f"{location.scheme}://{location.netloc}" == "http://localhost:3000"
    assert location.path == "/unsubscribe"
    assert parse_qs(location.query) == {"token": [token]}
    assert resp.headers["cache-control"] == "no-store"
    set_digest.assert_not_called()


def test_get_with_legacy_or_invalid_token_still_lands_on_page(client, set_digest):
    resp = client.get("/api/users/unsubscribe?token=bad%26token")
    assert resp.status_code == 303
    assert parse_qs(urlparse(resp.headers["location"]).query) == {"token": ["bad&token"]}
    resp = client.get("/api/users/unsubscribe")
    assert resp.status_code == 303
    set_digest.assert_not_called()


def test_rfc8058_one_click_post_unsubscribes_without_login(client, set_digest):
    token = generate_unsubscribe_token(42, SECRET)
    resp = client.post(
        f"/api/users/unsubscribe?token={token}",
        content="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert resp.json()["digest_enabled"] is False
    set_digest.assert_called_once_with(42, False)


def test_one_click_post_accepts_multipart_body(client, set_digest):
    token = generate_unsubscribe_token(42, SECRET)
    resp = client.post(
        f"/api/users/unsubscribe?token={token}",
        files={"List-Unsubscribe": (None, "One-Click")},
    )
    assert resp.status_code == 200
    set_digest.assert_called_once_with(42, False)


def test_post_accepts_legacy_token(client, set_digest):
    legacy_sig = hmac.new(SECRET.encode(), b"42", hashlib.sha256).hexdigest()
    resp = client.post(f"/api/users/unsubscribe?token=42.{legacy_sig}")
    assert resp.status_code == 200
    set_digest.assert_called_once_with(42, False)


def test_post_with_invalid_token_changes_nothing(client, set_digest):
    resp = client.post("/api/users/unsubscribe?token=u2.42.forged")
    assert resp.status_code == 400
    set_digest.assert_not_called()


def test_post_is_idempotent(client, set_digest):
    token = generate_unsubscribe_token(42, SECRET)
    assert client.post(f"/api/users/unsubscribe?token={token}").status_code == 200
    assert client.post(f"/api/users/unsubscribe?token={token}").status_code == 200
    assert set_digest.call_count == 2


def test_resubscribe_with_token(client, set_digest):
    token = generate_unsubscribe_token(42, SECRET)
    with patch("backend.api.scheduler.DIGEST_ENABLED", True):
        resp = client.post(f"/api/users/resubscribe?token={token}")
    assert resp.status_code == 200
    assert resp.json()["digest_enabled"] is True
    set_digest.assert_called_once_with(42, True)


def test_resubscribe_refused_when_delivery_unavailable(client, set_digest):
    token = generate_unsubscribe_token(42, SECRET)
    with patch("backend.api.scheduler.DIGEST_ENABLED", False):
        resp = client.post(f"/api/users/resubscribe?token={token}")
    assert resp.status_code == 503
    set_digest.assert_not_called()


def test_resubscribe_with_invalid_token(client, set_digest):
    resp = client.post("/api/users/resubscribe?token=nope")
    assert resp.status_code == 400
    set_digest.assert_not_called()


def test_unsubscribe_token_is_not_an_api_credential(client):
    token = generate_unsubscribe_token(1, SECRET)
    for method, path in [
        ("GET", "/api/users/me"),
        ("GET", "/api/users/notifications"),
        ("POST", "/api/users/follow/1"),
    ]:
        resp = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, path


# ---------------------------------------------------------------------------
# Digest email
# ---------------------------------------------------------------------------

def _event():
    return {
        "paper_id": 100, "event_id": 10, "title": "Trade & Development",
        "status": "working_paper", "first_name": "Alice", "last_name": "Smith",
    }


def test_unsubscribe_link_and_headers():
    url = digest.unsubscribe_link("u2.42.abc+/=")
    assert url == "http://localhost:3000/api/users/unsubscribe?token=u2.42.abc%2B%2F%3D"
    assert digest.unsubscribe_headers(url) == {
        "List-Unsubscribe": f"<{url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def test_html_footer_has_button_and_preferences_link():
    url = "https://example.test/api/users/unsubscribe?token=u2.1.a&x=1"
    html = digest._render_digest_html(
        [_event()], "Ann", url,
        datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 8, tzinfo=timezone.utc),
    )
    assert 'href="https://example.test/api/users/unsubscribe?token=u2.1.a&amp;x=1"' in html
    assert "Unsubscribe from weekly digest</a>" in html
    assert 'bgcolor="#ffffff"' in html  # bulletproof table-cell button
    assert 'href="http://localhost:3000/settings"' in html
    assert "Manage email preferences" in html


def test_text_part_has_unsubscribe_and_preferences():
    url = "https://example.test/api/users/unsubscribe?token=u2.1.a"
    text = digest._render_digest_text(
        [_event(), _event()], None, url,
        datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 8, tzinfo=timezone.utc),
    )
    assert f"Unsubscribe from the weekly digest: {url}" in text
    assert "Manage email preferences: http://localhost:3000/settings" in text
    assert text.count("Trade & Development") == 1
    assert "http://localhost:3000/papers/100" in text


def test_send_email_passes_headers_and_text_to_resend():
    headers = digest.unsubscribe_headers("https://example.test/u")
    with patch.object(digest, "RESEND_API_KEY", "re_test"), \
         patch("backend.digest.resend.Emails.send") as send:
        assert digest._send_email("a@example.test", "Subject", "<p>x</p>", "x", headers)
    params = send.call_args.args[0]
    assert params["headers"] == headers
    assert params["text"] == "x"
    assert params["to"] == ["a@example.test"]


def test_weekly_digest_sends_unsubscribe_headers():
    recipient = {
        "id": 42, "email": "user@example.test", "name": "Test",
        "last_digest_sent": None,
        "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        "researcher_ids": [1],
    }
    with patch("backend.digest.Database") as db, \
         patch("backend.digest._send_email", return_value=True) as send, \
         patch("backend.digest.record_digest_delivery"):
        db.get_digest_recipients.return_value = [recipient]
        db.get_feed_events_for_researchers.return_value = [_event()]
        db.generate_unsubscribe_token.side_effect = generate_unsubscribe_token
        assert digest.run_weekly_digest() == 1

    to, _subject, html, text, headers = send.call_args.args
    token = generate_unsubscribe_token(42, SECRET)
    expected_url = f"http://localhost:3000/api/users/unsubscribe?token={token}"
    assert headers["List-Unsubscribe"] == f"<{expected_url}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert expected_url in text
    assert expected_url in html


def test_weekly_digest_not_sent_without_signing_secret():
    with patch.object(digest, "NEXTAUTH_SECRET", ""), \
         patch("backend.digest.Database") as db, \
         patch("backend.digest._send_email") as send:
        assert digest.run_weekly_digest() == 0
    db.get_digest_recipients.assert_not_called()
    send.assert_not_called()
