"""Passwordless email-code login: code lifecycle, limits, enumeration, linking."""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from jose import jwt

import backend.database.email_login as real_store
import backend.email_login as email_login

NEXTAUTH_SECRET = "test-nextauth-secret-for-ci"
T0 = datetime(2026, 11, 27, 12, 0, 0)


class FakeStore:
    """In-memory stand-in for backend.database.email_login.

    Attempt outcomes come from the real ``evaluate_attempt``, so only the
    SQL plumbing is faked.
    """

    def __init__(self):
        self.codes: list[dict] = []
        self.users: list[dict] = []

    # -- codes --
    def latest_code_created_at(self, email):
        times = [c["created_at"] for c in self.codes if c["email"] == email]
        return max(times) if times else None

    def count_codes_for_email_since(self, email, since):
        return sum(1 for c in self.codes if c["email"] == email and c["created_at"] >= since)

    def count_codes_since(self, since):
        return sum(1 for c in self.codes if c["created_at"] >= since)

    def store_code(self, email, code_hash, now, expires_at):
        for c in self.codes:
            if c["email"] == email and c["consumed_at"] is None:
                c["consumed_at"] = now
        self.codes.append({
            "id": len(self.codes) + 1, "email": email, "code_hash": code_hash,
            "created_at": now, "expires_at": expires_at, "attempts": 0, "consumed_at": None,
        })
        return len(self.codes)

    def discard_code(self, code_id, now):
        for c in self.codes:
            if c["id"] == code_id and c["consumed_at"] is None:
                c["consumed_at"] = now

    def failed_attempts_for_email_since(self, email, since):
        return sum(c["attempts"] for c in self.codes if c["email"] == email and c["created_at"] >= since)

    def failed_attempts_since(self, since):
        return sum(c["attempts"] for c in self.codes if c["created_at"] >= since)

    def consume_code(self, email, candidate_hash, now, max_attempts):
        live = [c for c in self.codes if c["email"] == email and c["consumed_at"] is None]
        row = live[-1] if live else None
        result, attempts, consume = real_store.evaluate_attempt(row, candidate_hash, now, max_attempts)
        if row is not None:
            row["attempts"] = attempts
            if consume:
                row["consumed_at"] = now
        return result

    # -- users --
    def get_or_create_email_user(self, email, now):
        for u in self.users:
            if u["email"].lower() == email:
                return u
        user = {"id": len(self.users) + 1, "google_id": None, "email": email,
                "name": None, "picture_url": None}
        self.users.append(user)
        return user


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(email_login, "store", fake)
    monkeypatch.setenv("NEXTAUTH_SECRET", NEXTAUTH_SECRET)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.delenv("EMAIL_LOGIN_DEV_LOG_CODES", raising=False)
    monkeypatch.delenv("EMAIL_LOGIN_HOURLY_LIMIT", raising=False)
    monkeypatch.delenv("EMAIL_LOGIN_MAX_FAILURES_PER_HOUR", raising=False)
    monkeypatch.setattr(email_login, "_circuit_logged_at", -1e9)
    monkeypatch.delenv("LOGIN_FROM_EMAIL", raising=False)
    monkeypatch.setenv("DIGEST_FROM_EMAIL", "Econ Newsfeed <digest@econnewsfeed.org>")
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "re_test")
    return fake


@pytest.fixture
def outbox(monkeypatch):
    sent: list[dict] = []

    def fake_send(to, subject, html, *, text=None, from_email=None):
        sent.append({"to": to, "subject": subject, "html": html, "text": text, "from": from_email})
        return True

    monkeypatch.setattr(email_login.digest, "_send_email", fake_send)
    return sent


def _code_from(message: dict) -> str:
    return re.match(r"(\d{6}) ", message["subject"]).group(1)


# ---------------------------------------------------------------------------
# Code lifecycle
# ---------------------------------------------------------------------------

def test_request_sends_six_digit_code_stored_only_as_hash(store, outbox):
    result = email_login.request_code("  Alice@Example.COM ", now=T0)

    assert result.expires_in == 600
    assert result.resend_after == 60
    assert len(outbox) == 1
    msg = outbox[0]
    assert msg["to"] == "alice@example.com"
    code = _code_from(msg)
    assert msg["subject"] == f"{code} is your Econ Newsfeed sign-in code"
    assert "Econ Newsfeed" in msg["text"] and code in msg["text"] and code in msg["html"]
    stored = store.codes[0]
    assert stored["email"] == "alice@example.com"
    assert code not in stored["code_hash"]
    assert stored["code_hash"] == email_login.hash_code("alice@example.com", code)
    assert stored["expires_at"] == T0 + timedelta(minutes=10)


def test_correct_code_signs_in_and_creates_account_once(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    user = email_login.verify_code("ALICE@example.com", _code_from(outbox[0]), now=T0 + timedelta(minutes=1))

    assert user["email"] == "alice@example.com"
    assert len(store.users) == 1

    email_login.request_code("alice@example.com", now=T0 + timedelta(minutes=5))
    again = email_login.verify_code("alice@example.com", _code_from(outbox[1]), now=T0 + timedelta(minutes=6))
    assert again["id"] == user["id"]
    assert len(store.users) == 1


def test_code_is_single_use(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    code = _code_from(outbox[0])
    email_login.verify_code("alice@example.com", code, now=T0 + timedelta(seconds=30))

    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", code, now=T0 + timedelta(seconds=40))
    assert exc.value.code == "invalid_code"


def test_code_expires_after_ten_minutes(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    code = _code_from(outbox[0])

    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", code, now=T0 + timedelta(minutes=10))
    assert exc.value.code == "code_expired"
    assert store.users == []


def test_code_locks_after_five_wrong_attempts(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    code = _code_from(outbox[0])
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    for _ in range(4):
        with pytest.raises(email_login.EmailLoginError) as exc:
            email_login.verify_code("alice@example.com", wrong, now=T0)
        assert exc.value.code == "invalid_code"
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", wrong, now=T0)
    assert exc.value.code == "too_many_attempts"

    # The right code no longer works either.
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", code, now=T0)
    assert exc.value.code == "invalid_code"
    assert store.users == []


def test_new_request_invalidates_previous_code(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    email_login.request_code("alice@example.com", now=T0 + timedelta(seconds=61))
    old, new = _code_from(outbox[0]), _code_from(outbox[1])
    if old == new:  # one-in-a-million collision; the old row is still retired
        pytest.skip("random codes collided")

    with pytest.raises(email_login.EmailLoginError):
        email_login.verify_code("alice@example.com", old, now=T0 + timedelta(seconds=70))
    user = email_login.verify_code("alice@example.com", new, now=T0 + timedelta(seconds=70))
    assert user["email"] == "alice@example.com"


def test_code_for_one_address_does_not_work_for_another(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("bob@example.com", _code_from(outbox[0]), now=T0)
    assert exc.value.code == "invalid_code"


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef"])
def test_malformed_code_rejected_without_using_an_attempt(store, outbox, bad):
    email_login.request_code("alice@example.com", now=T0)
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", bad, now=T0)
    assert exc.value.code == "invalid_code"
    assert store.codes[0]["attempts"] == 0


def test_code_with_spaces_is_accepted(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    code = _code_from(outbox[0])
    user = email_login.verify_code("alice@example.com", f" {code[:3]} {code[3:]} ", now=T0)
    assert user["email"] == "alice@example.com"


@pytest.mark.parametrize("raw", ["", "not-an-email", "a@b", "a b@example.com", "x" * 250 + "@example.com"])
def test_invalid_email_rejected(store, outbox, raw):
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code(raw, now=T0)
    assert exc.value.code == "invalid_email"
    assert outbox == []


def test_send_failure_retires_the_code(store, monkeypatch):
    monkeypatch.setattr(email_login.digest, "_send_email", lambda *a, **k: False)
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=T0)
    assert exc.value.code == "email_send_failed"
    assert store.codes[0]["consumed_at"] == T0


def test_login_sender_defaults_to_digest_sender_and_can_be_overridden(store, outbox, monkeypatch):
    email_login.request_code("alice@example.com", now=T0)
    assert outbox[0]["from"] == "Econ Newsfeed <digest@econnewsfeed.org>"

    monkeypatch.setenv("LOGIN_FROM_EMAIL", "Econ Newsfeed <login@econnewsfeed.org>")
    email_login.request_code("bob@example.com", now=T0)
    assert outbox[1]["from"] == "Econ Newsfeed <login@econnewsfeed.org>"


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------

def test_resend_cooldown(store, outbox):
    email_login.request_code("alice@example.com", now=T0)
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=T0 + timedelta(seconds=20))
    assert exc.value.code == "resend_too_soon"
    assert exc.value.status == 429
    assert 0 < exc.value.retry_after <= 41
    email_login.request_code("alice@example.com", now=T0 + timedelta(seconds=60))
    assert len(outbox) == 2


def test_cooldown_wait_is_capped_when_latest_code_is_ahead_of_clock(store, outbox):
    email_login.request_code("alice@example.com", now=T0 + timedelta(hours=2))
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=T0)
    assert exc.value.code == "resend_too_soon"
    assert exc.value.retry_after == 60
    assert "60 seconds" in exc.value.message


def test_per_email_hourly_limit(store, outbox):
    for i in range(5):
        email_login.request_code("alice@example.com", now=T0 + timedelta(minutes=2 * i))
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=T0 + timedelta(minutes=11))
    assert exc.value.code == "too_many_codes"
    # Other addresses are unaffected, and the window slides.
    email_login.request_code("bob@example.com", now=T0 + timedelta(minutes=11))
    email_login.request_code("alice@example.com", now=T0 + timedelta(minutes=61))
    assert len(outbox) == 7


def test_global_hourly_cap(store, outbox, monkeypatch):
    monkeypatch.setenv("EMAIL_LOGIN_HOURLY_LIMIT", "2")
    email_login.request_code("a@example.com", now=T0)
    email_login.request_code("b@example.com", now=T0)
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("c@example.com", now=T0)
    assert exc.value.code == "email_login_busy"
    assert len(outbox) == 2


# ---------------------------------------------------------------------------
# Guessing limits: per address per day, and the global circuit breaker
# ---------------------------------------------------------------------------

def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _burn_codes(email, n_codes, start, outbox, wrong_per_code=5):
    """Issue n codes (respecting the cooldown) and guess each wrong."""
    t = start
    for _ in range(n_codes):
        email_login.request_code(email, now=t)
        code = _code_from(outbox[-1])
        for _ in range(wrong_per_code):
            with pytest.raises(email_login.EmailLoginError):
                email_login.verify_code(email, _wrong(code), now=t)
        t += timedelta(minutes=13)
    return t


def test_guarded_address_owner_can_still_sign_in(store, outbox):
    """15 wrong codes in a day must not lock the real owner out."""
    _burn_codes("alice@example.com", 3, T0, outbox)  # 15 wrong guesses
    assert store.failed_attempts_for_email_since("alice@example.com", T0) == 15

    # At worst the owner waits for the attacker's codes to leave the hourly window.
    t = T0 + timedelta(hours=1, minutes=30)
    email_login.request_code("alice@example.com", now=t)
    code = _code_from(outbox[-1])
    user = email_login.verify_code("alice@example.com", code, now=t)
    assert user["email"] == "alice@example.com"


def test_guarded_address_gets_one_try_per_code(store, outbox):
    _burn_codes("alice@example.com", 3, T0, outbox)
    t = T0 + timedelta(hours=1, minutes=30)
    email_login.request_code("alice@example.com", now=t)
    code = _code_from(outbox[-1])

    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", _wrong(code), now=t)
    assert exc.value.code == "too_many_attempts"
    # The code is spent: not even the right one works now.
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", code, now=t)
    assert exc.value.code == "invalid_code"


def test_live_code_drops_to_one_try_once_the_address_is_guarded(store, outbox):
    t = _burn_codes("alice@example.com", 2, T0, outbox)  # 10 wrong guesses
    email_login.request_code("alice@example.com", now=t)
    code = _code_from(outbox[-1])
    for _ in range(4):  # 14 in total, code still live
        with pytest.raises(email_login.EmailLoginError):
            email_login.verify_code("alice@example.com", _wrong(code), now=t)
    store.codes[0]["attempts"] += 1  # a 15th wrong guess elsewhere
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.verify_code("alice@example.com", _wrong(code), now=t)
    assert exc.value.code == "too_many_attempts"


def test_guarded_address_gets_two_codes_an_hour(store, outbox):
    t = _burn_codes("alice@example.com", 3, T0, outbox)  # 3 codes issued, 15 wrong
    # The hourly window now holds 3 codes, so a guarded address must wait.
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=t)
    assert exc.value.code == "too_many_codes"
    later = T0 + timedelta(hours=1, minutes=30)  # the earlier codes left the window
    email_login.request_code("alice@example.com", now=later)
    email_login.request_code("alice@example.com", now=later + timedelta(minutes=2))
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=later + timedelta(minutes=4))
    assert exc.value.code == "too_many_codes"
    # Another address keeps the normal 5 an hour.
    for i in range(5):
        email_login.request_code("bob@example.com", now=later + timedelta(minutes=2 * i))


def test_guarded_address_guess_budget_is_small():
    per_day = email_login.MAX_CODES_PER_EMAIL_GUARDED * 24 * email_login.MAX_ATTEMPTS_GUARDED
    assert per_day <= 48


def test_fewer_than_15_wrong_codes_still_allows_sign_in(store, outbox):
    t = _burn_codes("alice@example.com", 2, T0, outbox)  # 10 wrong guesses
    email_login.request_code("alice@example.com", now=t)
    code = _code_from(outbox[-1])
    for _ in range(4):
        with pytest.raises(email_login.EmailLoginError):
            email_login.verify_code("alice@example.com", _wrong(code), now=t)
    assert email_login.verify_code("alice@example.com", code, now=t)["email"] == "alice@example.com"


def test_circuit_breaker_pauses_email_login_and_logs_once(store, outbox, monkeypatch, caplog):
    monkeypatch.setenv("EMAIL_LOGIN_MAX_FAILURES_PER_HOUR", "10")
    for i in range(2):  # 2 addresses x 5 wrong guesses = 10 failures
        _burn_codes(f"victim{i}@example.com", 1, T0, outbox)

    with caplog.at_level("ERROR", logger="backend.email_login"):
        with pytest.raises(email_login.EmailLoginError) as exc:
            email_login.request_code("new@example.com", now=T0 + timedelta(minutes=5))
        assert exc.value.code == "email_login_paused"
        assert exc.value.status == 503
        with pytest.raises(email_login.EmailLoginError) as exc:
            email_login.verify_code("victim0@example.com", "123456", now=T0 + timedelta(minutes=5))
        assert exc.value.code == "email_login_paused"

    alerts = [r for r in caplog.records if "email_login_circuit_open" in r.getMessage()]
    assert len(alerts) == 1  # throttled
    assert "failures=10 limit=10" in alerts[0].getMessage()

    # Once the failures age out of the hour window, sign-in resumes.
    email_login.request_code("new@example.com", now=T0 + timedelta(hours=1, minutes=1))


def test_circuit_breaker_default_is_100(store, monkeypatch):
    assert email_login._max_failures_per_hour() == 100
    monkeypatch.setenv("EMAIL_LOGIN_MAX_FAILURES_PER_HOUR", "nonsense")
    assert email_login._max_failures_per_hour() == 100


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def test_unavailable_without_provider(store, outbox, monkeypatch):
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "")
    assert email_login.is_available() is False
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=T0)
    assert exc.value.code == "email_login_unavailable"
    assert exc.value.status == 503
    assert outbox == [] and store.codes == []


def test_dev_mode_logs_code_instead_of_sending(store, outbox, monkeypatch, caplog):
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "")
    monkeypatch.setenv("EMAIL_LOGIN_DEV_LOG_CODES", "true")
    assert email_login.is_available() is True

    with caplog.at_level("WARNING", logger="backend.email_login"):
        email_login.request_code("alice@example.com", now=T0)
    assert outbox == []
    code = re.search(r"code for alice@example.com is (\d{6})", caplog.text).group(1)
    assert email_login.verify_code("alice@example.com", code, now=T0)["email"] == "alice@example.com"


def test_dev_mode_ignored_outside_localhost(store, monkeypatch):
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "")
    monkeypatch.setenv("EMAIL_LOGIN_DEV_LOG_CODES", "true")
    monkeypatch.setenv("FRONTEND_URL", "https://econnewsfeed.org")
    assert email_login.dev_log_mode() is False
    assert email_login.is_available() is False


@pytest.mark.parametrize("digest_from", ["", "digest@econ-newsfeed.com", "Digest <Digest@econ-newsfeed.com>"])
def test_unavailable_without_explicit_sender(store, outbox, monkeypatch, digest_from):
    """A Resend key alone is not enough: the compose placeholder sender would 502."""
    monkeypatch.setenv("DIGEST_FROM_EMAIL", digest_from)
    assert email_login.is_available() is False
    with pytest.raises(email_login.EmailLoginError) as exc:
        email_login.request_code("alice@example.com", now=T0)
    assert exc.value.code == "email_login_unavailable"
    monkeypatch.setenv("LOGIN_FROM_EMAIL", "Econ Newsfeed <login@econnewsfeed.org>")
    assert email_login.is_available() is True


def test_unavailable_without_signing_secret(store, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", "")
    assert email_login.is_available() is False


# ---------------------------------------------------------------------------
# evaluate_attempt (shared by the SQL path)
# ---------------------------------------------------------------------------

def test_evaluate_attempt_outcomes():
    row = {"code_hash": "h", "attempts": 0, "expires_at": T0 + timedelta(minutes=10)}
    assert real_store.evaluate_attempt(None, "h", T0, 5) == ("invalid", 0, False)
    assert real_store.evaluate_attempt(row, "h", T0, 5) == ("ok", 0, True)
    assert real_store.evaluate_attempt(row, "x", T0, 5) == ("invalid", 1, False)
    assert real_store.evaluate_attempt({**row, "attempts": 4}, "x", T0, 5) == ("locked", 5, True)
    assert real_store.evaluate_attempt(row, "h", T0 + timedelta(minutes=10), 5) == ("expired", 0, True)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

@contextmanager
def _noop_scope():
    yield None


@pytest.fixture
def client(store, outbox):
    with (
        patch("backend.api.create_tables"),
        patch("backend.api.start_scheduler"),
        patch("backend.api.shutdown_scheduler"),
        patch("backend.api.connection_scope", _noop_scope),
        patch("backend.auth.connection_scope", _noop_scope),
    ):
        from backend.api import app, limiter
        from fastapi.testclient import TestClient
        limiter.reset()
        with TestClient(app) as c:
            yield c
        limiter.reset()


def test_capabilities_report_email_login(client, monkeypatch):
    assert client.get("/api/capabilities").json()["email_login_available"] is True
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "")
    assert client.get("/api/capabilities").json()["email_login_available"] is False


def test_request_code_response_identical_for_new_and_existing_accounts(client, store, outbox):
    store.users.append({"id": 7, "google_id": "g7", "email": "known@example.com",
                        "name": "Known", "picture_url": None})
    known = client.post("/api/email-login/request-code", json={"email": "known@example.com"})
    unknown = client.post("/api/email-login/request-code", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(outbox) == 2


def test_endpoint_flow_and_error_envelope(client, store, outbox):
    ip = {"X-Forwarded-For": "198.51.100.3"}
    assert client.post("/api/email-login/request-code", json={"email": "Carol@Example.com"},
                       headers=ip).status_code == 202
    code = _code_from(outbox[0])

    bad = client.post("/api/email-login/verify-code",
                      json={"email": "carol@example.com", "code": "000000" if code != "000000" else "111111"},
                      headers=ip)
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_code"

    ok = client.post("/api/email-login/verify-code",
                     json={"email": "carol@example.com", "code": code}, headers=ip)
    assert ok.status_code == 200
    assert ok.json() == {"id": 1, "email": "carol@example.com", "name": None, "picture_url": None}

    again = client.post("/api/email-login/request-code", json={"email": "carol@example.com"}, headers=ip)
    assert again.status_code == 429
    assert again.json()["error"]["code"] == "resend_too_soon"
    assert int(again.headers["Retry-After"]) > 0


def test_paused_response_over_http(client, store, monkeypatch):
    monkeypatch.setattr(store, "failed_attempts_since", lambda since: 500)
    for path, body in (("request-code", {"email": "a@example.com"}),
                       ("verify-code", {"email": "a@example.com", "code": "123456"})):
        resp = client.post(f"/api/email-login/{path}", json=body)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "email_login_paused"
        assert resp.headers["Retry-After"] == "600"


def test_request_code_unavailable_returns_503(client, monkeypatch):
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "")
    resp = client.post("/api/email-login/request-code", json={"email": "a@example.com"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "email_login_unavailable"


INTERNAL_KEY = "test-internal-key-0123456789abcdef"


@pytest.fixture
def internal_key(monkeypatch):
    """Configure INTERNAL_API_KEY as in production; restore settings afterwards."""
    from backend import api_protection
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    # Target config once the key is live on both sides: keyless callers are
    # "direct" and keyed by their own edge IP.
    monkeypatch.setenv("API_DIRECT_ACCESS", "limited")
    api_protection.reload_settings()
    yield INTERNAL_KEY
    monkeypatch.delenv("INTERNAL_API_KEY")
    monkeypatch.delenv("API_DIRECT_ACCESS")
    api_protection.reload_settings()


@pytest.fixture
def no_internal_key(monkeypatch):
    """Production today: no INTERNAL_API_KEY, so everything is the fail-open tier."""
    from backend import api_protection
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    monkeypatch.delenv("API_DIRECT_ACCESS", raising=False)
    api_protection.reload_settings()
    yield
    api_protection.reload_settings()


def _via_frontend(visitor_ip: str) -> dict:
    """Headers the NextAuth authorize call / Next proxy add (internalApiHeaders)."""
    return {"X-Internal-Api-Key": INTERNAL_KEY, "X-Visitor-IP": visitor_ip, "X-Visitor-Source": "proxy"}


def _request_statuses(client, headers_for, n):
    return [
        client.post("/api/email-login/request-code", json={"email": f"user{i}@example.com"},
                    headers=headers_for(i)).status_code
        for i in range(n)
    ]


def test_request_code_rate_limited_per_visitor(client, outbox, internal_key):
    statuses = _request_statuses(client, lambda i: _via_frontend("203.0.113.9"), 6)
    assert statuses == [202] * 5 + [429]
    # A different visitor behind the same frontend is not affected.
    other = client.post("/api/email-login/request-code", json={"email": "z@example.com"},
                        headers=_via_frontend("203.0.113.10"))
    assert other.status_code == 202


def test_spoofed_forwarding_headers_do_not_change_rate_limit_key(client, outbox, internal_key):
    """A direct caller rotating forwarding headers stays in one bucket."""
    def spoofed(i):
        return {
            # Caddy writes the real peer as the rightmost entry; the rest is client-supplied.
            "X-Forwarded-For": f"10.{i}.0.1, 192.0.2.{i}, 198.51.100.50",
            "X-Real-IP": f"192.0.2.{i}",
            "X-Vercel-Forwarded-For": f"192.0.2.{i}",
            "X-Visitor-IP": f"192.0.2.{i}",  # ignored without a valid key
            "X-Internal-Api-Key": "wrong-guess-0000000000",
        }

    statuses = _request_statuses(client, spoofed, 6)
    assert statuses == [202] * 5 + [429]


def test_without_internal_key_shared_vercel_ip_is_not_throttled_like_one_visitor(client, outbox, no_internal_key):
    """All site traffic arrives from a few Vercel IPs: per-IP limits stay coarse."""
    vercel = {"X-Forwarded-For": "76.76.21.21"}
    statuses = _request_statuses(client, lambda i: vercel, 12)
    assert statuses == [202] * 12
    verify = [
        client.post("/api/email-login/verify-code", json={"email": f"v{i}@example.com", "code": "123456"},
                    headers=vercel).status_code
        for i in range(25)
    ]
    assert verify == [400] * 25


def test_email_limits_by_tier():
    from backend.api import email_code_request_limit, email_code_verify_limit
    assert email_code_request_limit("visitor:203.0.113.9") == "5/minute;30/hour"
    assert email_code_request_limit("direct:203.0.113.9") == "5/minute;30/hour"
    assert email_code_request_limit("legacy:76.76.21.21") == "60/minute"
    assert email_code_verify_limit("visitor:203.0.113.9") == "20/minute"
    assert email_code_verify_limit("legacy:76.76.21.21") == "240/minute"


def test_rate_limit_key_ignores_spoofed_headers(internal_key):
    from starlette.requests import Request
    from backend.api_protection import rate_limit_key

    def key(headers):
        scope = {"type": "http", "method": "POST", "path": "/api/email-login/request-code",
                 "query_string": b"", "client": ("172.18.0.1", 5000),
                 "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
        return rate_limit_key(Request(scope))

    base = key({"X-Forwarded-For": "198.51.100.50"})
    assert key({"X-Forwarded-For": "6.6.6.6, 198.51.100.50"}) == base
    assert key({"X-Forwarded-For": "198.51.100.50", "X-Real-IP": "6.6.6.6",
                "X-Vercel-Forwarded-For": "6.6.6.6", "X-Visitor-IP": "6.6.6.6"}) == base
    assert key({"X-Forwarded-For": "198.51.100.50", "X-Visitor-IP": "6.6.6.6",
                "X-Internal-Api-Key": "not-the-key-000000"}) == base
    # Only the real key makes the forwarded visitor count.
    assert key({"X-Forwarded-For": "198.51.100.50", **_via_frontend("6.6.6.6")}) != base


def test_verify_code_rate_limited_per_visitor(client, store, outbox, internal_key):
    statuses = [
        client.post("/api/email-login/verify-code", json={"email": f"u{i}@example.com", "code": "123456"},
                    headers=_via_frontend("203.0.113.20")).status_code
        for i in range(21)
    ]
    assert statuses[:20] == [400] * 20
    assert statuses[20] == 429


# ---------------------------------------------------------------------------
# Sessions and account linking
# ---------------------------------------------------------------------------

def _bearer(payload: dict) -> dict:
    return {"Authorization": f"Bearer {jwt.encode(payload, NEXTAUTH_SECRET, algorithm='HS256')}"}


def test_email_session_resolves_account_by_id(client):
    user = {"id": 42, "google_id": None, "email": "dana@example.com", "name": None,
            "picture_url": None, "created_at": "2026-11-27T00:00:00"}
    with patch("backend.auth.Database") as db:
        db.get_user_by_id.return_value = user
        resp = client.get("/api/users/me", headers=_bearer({"sub": "user:42", "email": "dana@example.com"}))
    assert resp.status_code == 200
    assert resp.json()["id"] == 42
    db.get_user_by_id.assert_called_once_with(42)
    db.get_or_create_user.assert_not_called()


@pytest.mark.parametrize("sub", ["user:", "user:abc", "user:-1"])
def test_malformed_email_session_rejected(client, sub):
    with patch("backend.auth.Database") as db:
        resp = client.get("/api/users/me", headers=_bearer({"sub": sub}))
    assert resp.status_code == 401
    db.get_user_by_id.assert_not_called()


def test_email_session_for_deleted_account_rejected(client):
    with patch("backend.auth.Database") as db:
        db.get_user_by_id.return_value = None
        resp = client.get("/api/users/me", headers=_bearer({"sub": "user:9"}))
    assert resp.status_code == 401


def test_google_session_passes_email_verified(client):
    user = {"id": 1, "google_id": "g1", "email": "e@example.com", "name": "E",
            "picture_url": None, "created_at": "2026-11-27T00:00:00"}
    with patch("backend.auth.Database") as db:
        db.get_or_create_user.return_value = user
        client.get("/api/users/me", headers=_bearer({"sub": "g1", "email": "e@example.com",
                                                      "email_verified": True}))
        client.get("/api/users/me", headers=_bearer({"sub": "g1", "email": "e@example.com"}))
    calls = db.get_or_create_user.call_args_list
    assert calls[0].kwargs["email_verified"] is True
    assert calls[1].kwargs["email_verified"] is False


def test_email_login_reuses_existing_google_account(monkeypatch):
    """Email code for an address that already has a Google account → same user."""
    google_user = {"id": 5, "google_id": "g5", "email": "Erin@Example.com", "name": "Erin"}
    monkeypatch.setattr(real_store, "fetch_one", lambda q, p: google_user if "FROM users WHERE email" in q else None)
    inserts = []
    monkeypatch.setattr(real_store, "execute_query", lambda q, p: inserts.append(q))

    assert real_store.get_or_create_email_user("erin@example.com", T0) is google_user
    assert inserts == []


def test_email_login_creates_account_without_google_id(monkeypatch):
    calls = []

    def fake_fetch_one(query, params):
        calls.append(query)
        if "WHERE id = %s" in query:
            return {"id": 11, "google_id": None, "email": "new@example.com"}
        return None

    monkeypatch.setattr(real_store, "fetch_one", fake_fetch_one)
    inserted = []
    monkeypatch.setattr(real_store, "execute_query", lambda q, p: inserted.append((q, p)) or 11)

    user = real_store.get_or_create_email_user("new@example.com", T0)
    assert user["id"] == 11
    assert "google_id" in inserted[0][0] and "NULL" in inserted[0][0]
    assert inserted[0][1] == ("new@example.com", T0)


class _UsersTable:
    """Minimal users table for backend.database.users.get_or_create_user."""

    def __init__(self, rows):
        self.rows = rows

    def fetch_one(self, query, params):
        if "WHERE google_id = %s" in query:
            return next((dict(r) for r in self.rows if r["google_id"] == params[0]), None)
        if "google_id IS NULL" in query:
            return next((dict(r) for r in self.rows
                         if r["google_id"] is None and r["email"].lower() == params[0].lower()), None)
        if "WHERE id = %s" in query:
            return next((dict(r) for r in self.rows if r["id"] == params[0]), None)
        raise AssertionError(query)

    def execute_query(self, query, params):
        if query.startswith("UPDATE users SET google_id"):
            google_id, name, picture, _, user_id = params
            for r in self.rows:
                if r["id"] == user_id and r["google_id"] is None:
                    r.update(google_id=google_id, name=r["name"] or name,
                             picture_url=r["picture_url"] or picture)
            return 0
        if query.startswith("INSERT INTO users"):
            google_id, email, name, picture, _ = params
            new_id = max((r["id"] for r in self.rows), default=0) + 1
            self.rows.append({"id": new_id, "google_id": google_id, "email": email,
                              "name": name, "picture_url": picture})
            return new_id
        if query.startswith("UPDATE users SET email"):
            return 0
        raise AssertionError(query)


@pytest.fixture
def users_table(monkeypatch):
    import backend.database.users as users
    table = _UsersTable([{"id": 3, "google_id": None, "email": "fay@example.com",
                          "name": None, "picture_url": None}])
    monkeypatch.setattr(users, "fetch_one", table.fetch_one)
    monkeypatch.setattr(users, "execute_query", table.execute_query)
    return users, table


def test_google_login_adopts_email_account_when_verified(users_table):
    users, table = users_table
    user = users.get_or_create_user("g-fay", "Fay@Example.com", "Fay", "https://pic", email_verified=True)
    assert user["id"] == 3
    assert user["google_id"] == "g-fay"
    assert user["name"] == "Fay"
    assert len(table.rows) == 1
    # Later Google logins find the same row by Google id.
    assert users.get_or_create_user("g-fay", "fay@example.com", "Fay")["id"] == 3


def test_google_login_without_verified_email_does_not_adopt(users_table):
    users, table = users_table
    user = users.get_or_create_user("g-fay", "fay@example.com", "Fay", None, email_verified=False)
    assert user["id"] != 3
    assert table.rows[0]["google_id"] is None


# ---------------------------------------------------------------------------
# Hourly purge (privacy: code rows are kept about a day)
# ---------------------------------------------------------------------------

def test_purge_old_codes_deletes_rows_older_than_a_day(monkeypatch):
    from unittest.mock import MagicMock
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.rowcount = 7
    monkeypatch.setattr(real_store, "get_connection", lambda: conn)
    conn.__enter__.return_value = conn

    assert real_store.purge_old_codes(T0) == 7
    sql, params = cursor.execute.call_args.args
    assert sql == "DELETE FROM email_login_codes WHERE created_at < %s"
    assert params == (T0 - timedelta(days=1),)
    conn.commit.assert_called_once()


def test_scheduler_purge_logs_and_survives_errors(monkeypatch, caplog):
    from backend.pipeline import scheduler
    calls = []
    monkeypatch.setattr(real_store, "purge_old_codes", lambda now: calls.append(now) or 3)
    with caplog.at_level("INFO", logger=scheduler.logger.name):
        scheduler._purge_email_login_codes()
    assert len(calls) == 1 and calls[0].tzinfo is None
    assert "Purged 3 email login code rows" in caplog.text

    def boom(now):
        raise RuntimeError("db down")
    monkeypatch.setattr(real_store, "purge_old_codes", boom)
    with caplog.at_level("WARNING", logger=scheduler.logger.name):
        scheduler._purge_email_login_codes()  # must not raise
    assert "Email login code purge failed: db down" in caplog.text


def test_scheduler_registers_hourly_purge():
    from unittest.mock import MagicMock
    from backend.pipeline import scheduler
    with patch("backend.pipeline.scheduler.mysql.connector.connect") as connect, \
         patch("backend.pipeline.scheduler._cleanup_stale_scrape_logs"), \
         patch("backend.pipeline.scheduler.BackgroundScheduler") as bg, \
         patch("backend.pipeline.scheduler._start_lock_keepalive"), \
         patch("backend.pipeline.scheduler.signal.signal"), \
         patch.object(scheduler, "ENRICHMENT_WORKER_ENABLED", False), \
         patch.object(scheduler, "_scheduler", None), \
         patch.object(scheduler, "_scheduler_lock_conn", None):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = (1,)
        connect.return_value = conn
        scheduler.start_scheduler()
        jobs = {c.kwargs.get("id"): c for c in bg.return_value.add_job.call_args_list}
    scheduler._scheduler = None
    scheduler._scheduler_lock_conn = None
    purge = jobs["email_login_code_purge"]
    assert purge.args[0] is scheduler._purge_email_login_codes
    assert purge.args[1] == "interval" and purge.kwargs["hours"] == 1
