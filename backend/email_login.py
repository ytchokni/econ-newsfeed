"""Passwordless login: email a one-time code, then exchange it for the account.

The API calls :func:`request_code` and :func:`verify_code`. NextAuth's
credentials provider calls the verify endpoint and mints the session; the
backend never issues tokens itself. Responses never depend on whether an
account already exists for the address.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

import mysql.connector

import backend.digest as digest
from backend.database import email_login as store

logger = logging.getLogger(__name__)

CODE_LENGTH = 6
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = timedelta(seconds=60)
PER_EMAIL_WINDOW = timedelta(hours=1)
MAX_CODES_PER_EMAIL = 5
# Wrong guesses allowed per address per rolling day (5 per code x 5 codes an
# hour would otherwise allow ~600 a day). Counted from email_login_codes,
# whose rows are kept for exactly that day.
MAX_FAILED_PER_EMAIL_PER_DAY = 15
FAILED_PER_EMAIL_WINDOW = timedelta(days=1)
# Once an address is guarded (see _check_guess_limits): codes per hour, tries per code.
MAX_CODES_PER_EMAIL_GUARDED = 2
MAX_ATTEMPTS_GUARDED = 1
CIRCUIT_WINDOW = timedelta(hours=1)
# Seconds between repeated circuit-open log lines (the monitor counts them).
_CIRCUIT_LOG_EVERY = 300.0

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LOCAL_HOSTS = ("http://localhost", "http://127.0.0.1")


class EmailLoginError(Exception):
    """A refusal the API reports with ``code`` and HTTP ``status``."""

    def __init__(self, code: str, message: str, status: int, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class CodeRequest:
    expires_in: int
    resend_after: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _secret() -> str:
    return os.environ.get("NEXTAUTH_SECRET", "")


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:3000")


# docker-compose fills DIGEST_FROM_EMAIL with this when .env leaves it out. It
# is not a domain we send from, so it does not count as a configured sender.
_PLACEHOLDER_SENDER = "digest@econ-newsfeed.com"


def _sender_address(value: str) -> str:
    """The bare address of ``"Name <addr>"`` or ``addr``, lowercased."""
    match = re.search(r"<([^>]+)>", value)
    return (match.group(1) if match else value).strip().lower()


def _from_email() -> str | None:
    """The configured sender for login codes, or None if none is set explicitly.

    LOGIN_FROM_EMAIL wins; DIGEST_FROM_EMAIL is the fallback. Without either
    (or with only the compose placeholder) Resend would reject every send.
    """
    for name in ("LOGIN_FROM_EMAIL", "DIGEST_FROM_EMAIL"):
        value = os.environ.get(name, "").strip()
        if value and _sender_address(value) != _PLACEHOLDER_SENDER:
            return value
    return None


def _hourly_limit() -> int:
    try:
        return max(1, int(os.environ.get("EMAIL_LOGIN_HOURLY_LIMIT", "200")))
    except ValueError:
        return 200


def _max_failures_per_hour() -> int:
    try:
        return max(1, int(os.environ.get("EMAIL_LOGIN_MAX_FAILURES_PER_HOUR", "100")))
    except ValueError:
        return 100


_circuit_logged_at = -_CIRCUIT_LOG_EVERY
_circuit_log_lock = threading.Lock()


def _log_circuit_open(failures: int, limit: int) -> None:
    """ERROR line scripts/monitor.py looks for; at most one per process per 5 min."""
    global _circuit_logged_at
    now = time.monotonic()
    with _circuit_log_lock:
        if now - _circuit_logged_at < _CIRCUIT_LOG_EVERY:
            return
        _circuit_logged_at = now
    logger.error(
        "email_login_circuit_open failures=%d limit=%d window=1h: "
        "email sign-in paused (possible code guessing)", failures, limit,
    )


def _check_guess_limits(email: str, now: datetime) -> bool:
    """Pause on a global guessing spike; report whether this address is guarded.

    An address with MAX_FAILED_PER_EMAIL_PER_DAY wrong codes in the last day is
    "guarded": it is not locked out (a stranger could otherwise lock anyone out
    of email login for a day), but each new code allows a single try and only
    MAX_CODES_PER_EMAIL_GUARDED codes an hour may be issued. That leaves the
    owner, who receives the codes, able to sign in, and caps further guessing
    at about 48 a day.
    """
    limit = _max_failures_per_hour()
    failures = store.failed_attempts_since(now - CIRCUIT_WINDOW)
    if failures >= limit:
        _log_circuit_open(failures, limit)
        raise EmailLoginError(
            "email_login_paused",
            # Failures age out of a rolling hour, so the pause can last up to that long.
            "Email sign-in is paused for up to an hour. Try again later or continue with Google.",
            503, int(CIRCUIT_WINDOW.total_seconds()),
        )
    failed_today = store.failed_attempts_for_email_since(email, now - FAILED_PER_EMAIL_WINDOW)
    return failed_today >= MAX_FAILED_PER_EMAIL_PER_DAY


def pool_busy_error() -> "EmailLoginError":
    """What a request gets when no pooled database connection is free."""
    logger.warning("Email login: database connection pool exhausted")
    return EmailLoginError(
        "email_login_busy", "Email sign-in is busy. Try again in a moment.", 503, 5,
    )


# Deadlock and lock-wait timeout: transient, the client should simply retry.
_TRANSIENT_MYSQL_ERRNOS = (1213, 1205)


@contextmanager
def _one_request_per_address(email: str):
    """Run checks and writes for ``email`` under its lock; map contention to 429/503.

    Without this, parallel requests all passed the cooldown and hourly checks
    before any of them stored a code (one email each), and their writes
    deadlocked into 500s.
    """
    try:
        with store.address_lock(email):
            yield
    except store.AddressBusy:
        raise EmailLoginError(
            "try_again", "Another sign-in request for this address is in progress. Try again in a moment.",
            429, 2,
        )
    except mysql.connector.errors.PoolError:
        raise pool_busy_error()
    except mysql.connector.Error as e:
        if getattr(e, "errno", None) in _TRANSIENT_MYSQL_ERRNOS:
            logger.warning("Email login: transient database error %s", e.errno)
            raise EmailLoginError(
                "try_again", "Sign-in is busy right now. Try again in a moment.", 429, 2,
            )
        raise


def dev_log_mode() -> bool:
    """Log codes instead of emailing them: local development only.

    Requires EMAIL_LOGIN_DEV_LOG_CODES=true, no Resend key, and a localhost
    FRONTEND_URL, so a stray flag cannot leak codes into production logs.
    """
    enabled = os.environ.get("EMAIL_LOGIN_DEV_LOG_CODES", "").lower() in ("1", "true", "yes")
    return (
        enabled
        and not digest.RESEND_API_KEY
        and _frontend_url().startswith(_LOCAL_HOSTS)
    )


def is_available() -> bool:
    """Whether this deployment can deliver login codes.

    Sending needs a Resend key and an explicitly configured sender (whose
    domain must be verified at Resend); dev mode needs neither.
    """
    if not _secret():
        return False
    if digest.RESEND_API_KEY:
        return _from_email() is not None
    return dev_log_mode()


def normalize_email(raw: str) -> str | None:
    """Trim and lowercase an address; None if it is not plausibly an email."""
    email = (raw or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        return None
    return email


def generate_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def hash_code(email: str, code: str) -> str:
    """Keyed digest, so a database leak does not reveal live codes."""
    return hmac.new(_secret().encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _require_email(raw: str) -> str:
    email = normalize_email(raw)
    if email is None:
        raise EmailLoginError("invalid_email", "Enter a valid email address.", 400)
    return email


def _render_email(code: str) -> tuple[str, str, str]:
    minutes = int(CODE_TTL.total_seconds() // 60)
    site = _frontend_url()
    subject = f"{code} is your Econ Newsfeed sign-in code"
    text = (
        f"Your Econ Newsfeed sign-in code is:\n\n{code}\n\n"
        f"It expires in {minutes} minutes and works once.\n\n"
        "If you didn't ask for it, ignore this email. Nobody can sign in "
        "without the code.\n\n"
        f"Econ Newsfeed\n{site}\n"
    )
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a2332;">
  <p style="font-size:18px;font-weight:600;margin:0 0 16px;">Econ Newsfeed</p>
  <p style="margin:0 0 8px;">Your sign-in code is:</p>
  <p style="font-size:32px;font-weight:700;letter-spacing:6px;margin:0 0 16px;font-family:ui-monospace,Menlo,monospace;">{escape(code)}</p>
  <p style="margin:0 0 16px;color:#4b5563;">It expires in {minutes} minutes and works once.</p>
  <p style="font-size:13px;color:#6b7280;margin:0 0 24px;">If you didn't ask for it, ignore this email. Nobody can sign in without the code.</p>
  <p style="font-size:12px;color:#9ca3af;margin:0;"><a href="{escape(site, quote=True)}" style="color:#2563eb;">Econ Newsfeed</a></p>
</body>
</html>"""
    return subject, text, html


def request_code(raw_email: str, now: datetime | None = None) -> CodeRequest:
    """Issue a code for this address and email it.

    The result is the same whether or not an account exists; refusals depend
    only on the address's recent requests and the deployment-wide cap.
    """
    if not is_available():
        raise EmailLoginError(
            "email_login_unavailable", "Email sign-in is not available right now.", 503,
        )
    email = _require_email(raw_email)
    now = now or _utcnow()

    # Checks and the insert must not interleave with another request for this
    # address; the email itself is sent after the lock is released.
    with _one_request_per_address(email):
        guarded = _check_guess_limits(email, now)

        latest = store.latest_code_created_at(email)
        if latest is not None and now - latest < RESEND_COOLDOWN:
            # Clamped: a code stamped slightly ahead (clock skew) must not ask for hours.
            remaining = (RESEND_COOLDOWN - (now - latest)).total_seconds()
            wait = max(1, min(int(remaining) + 1, int(RESEND_COOLDOWN.total_seconds())))
            raise EmailLoginError(
                "resend_too_soon", f"Wait {wait} seconds before requesting another code.", 429, wait,
            )
        max_codes = MAX_CODES_PER_EMAIL_GUARDED if guarded else MAX_CODES_PER_EMAIL
        if store.count_codes_for_email_since(email, now - PER_EMAIL_WINDOW) >= max_codes:
            raise EmailLoginError(
                "too_many_codes", "Too many codes requested for this address. Try again in an hour.",
                429, int(PER_EMAIL_WINDOW.total_seconds()),
            )
        if store.count_codes_since(now - timedelta(hours=1)) >= _hourly_limit():
            logger.warning("Email login: hourly send cap reached")
            raise EmailLoginError(
                "email_login_busy", "Email sign-in is busy. Try again in a few minutes.", 503, 300,
            )

        code = generate_code()
        code_id = store.store_code(email, hash_code(email, code), now, now + CODE_TTL)
    subject, text, html = _render_email(code)

    if digest.RESEND_API_KEY:
        sent = digest._send_email(email, subject, html, text=text, from_email=_from_email())
    else:
        logger.warning("Email login DEV MODE: code for %s is %s (not emailed)", email, code)
        sent = True
    if not sent:
        with _one_request_per_address(email):
            store.discard_code(code_id, now)
        raise EmailLoginError(
            "email_send_failed", "We couldn't send the email. Try again in a moment.", 502,
        )
    return CodeRequest(
        expires_in=int(CODE_TTL.total_seconds()),
        resend_after=int(RESEND_COOLDOWN.total_seconds()),
    )


def verify_code(raw_email: str, raw_code: str, now: datetime | None = None) -> dict:
    """Exchange a valid code for the account that owns the address.

    The first verified login creates the account; an existing account with
    the same address (including a Google one) is reused.
    """
    if not is_available():
        raise EmailLoginError(
            "email_login_unavailable", "Email sign-in is not available right now.", 503,
        )
    email = _require_email(raw_email)
    code = re.sub(r"\s+", "", raw_code or "")
    if not re.fullmatch(rf"\d{{{CODE_LENGTH}}}", code):
        raise EmailLoginError("invalid_code", f"Enter the {CODE_LENGTH}-digit code from the email.", 400)
    now = now or _utcnow()

    with _one_request_per_address(email):
        guarded = _check_guess_limits(email, now)
        max_attempts = MAX_ATTEMPTS_GUARDED if guarded else MAX_ATTEMPTS
        result = store.consume_code(email, hash_code(email, code), now, max_attempts)
    if result == "expired":
        raise EmailLoginError("code_expired", "This code has expired. Request a new one.", 400)
    if result == "locked":
        raise EmailLoginError(
            "too_many_attempts", "Too many incorrect attempts. Request a new code.", 400,
        )
    if result != "ok":
        raise EmailLoginError("invalid_code", "That code is incorrect or no longer valid.", 400)
    return store.get_or_create_email_user(email, now)
