"""Concurrency of email-code login against a real MySQL server.

Every other email-login test uses an in-memory store, which cannot show
deadlocks or races between connections. These run N threads through the
real store and the app's connection pool, as the API does.

Runs when TEST_MYSQL_HOST is set (CI starts a mysql:8.0.40 service on 3307);
each test creates and drops a throwaway database. Locally:

    docker run -d --rm --name econ-sql-test -e MYSQL_ROOT_PASSWORD=test \\
        -p 3307:3306 mysql:8.0.40
    TEST_MYSQL_HOST=127.0.0.1 TEST_MYSQL_PORT=3307 poetry run pytest tests/test_email_login_mysql.py

Before the per-address lock, 12 threads already hit 1213 deadlocks (500s)
and 20 parallel requests for one address sent 20 emails.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

from backend.database.schema import _TABLE_DEFINITIONS

HOST = os.environ.get("TEST_MYSQL_HOST")

# (pool size, threads): the largest pool mysql-connector allows, and the API's
# default of 5, where requests that find no free connection get a 503.
POOL_CASES = [(32, 30), (5, 20)]
POOL_IDS = ["pool32-30threads", "pool5-20threads"]


def _server_params() -> dict:
    return {
        "host": HOST,
        "port": int(os.environ.get("TEST_MYSQL_PORT", "3306")),
        "user": os.environ.get("TEST_MYSQL_USER", "root"),
        "password": os.environ.get("TEST_MYSQL_PASSWORD", "test"),
    }


def _admin_connection():
    import mysql.connector
    for _ in range(30):  # the service may still be starting
        try:
            return mysql.connector.connect(**_server_params())
        except mysql.connector.Error:
            time.sleep(2)
    pytest.fail("could not reach TEST_MYSQL_HOST")


@pytest.fixture(params=POOL_CASES, ids=POOL_IDS)
def mysql_login(request, monkeypatch):
    """A throwaway database with the users and email_login_codes tables, and
    the app's connection pool pointed at it."""
    if not HOST:
        pytest.skip("TEST_MYSQL_HOST not set")
    from mysql.connector.pooling import MySQLConnectionPool
    import backend.database.connection as connection
    import backend.email_login as email_login

    pool_size, threads = request.param
    db = f"email_login_{uuid.uuid4().hex[:8]}"
    admin = _admin_connection()
    cur = admin.cursor()
    cur.execute(f"CREATE DATABASE {db} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.execute(f"USE {db}")
    for table in ("users", "email_login_codes"):
        cur.execute(re.sub(r",\s*FOREIGN KEY[^,]*?ON DELETE CASCADE", "", _TABLE_DEFINITIONS[table]))
    admin.commit()

    pool = MySQLConnectionPool(
        pool_name=f"el_{uuid.uuid4().hex[:8]}", pool_size=pool_size,
        database=db, charset="utf8mb4", collation="utf8mb4_unicode_ci", **_server_params(),
    )
    monkeypatch.setattr(connection, "_pool", pool)
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "")
    monkeypatch.setenv("EMAIL_LOGIN_DEV_LOG_CODES", "true")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("NEXTAUTH_SECRET", "race-test-secret")
    monkeypatch.setenv("EMAIL_LOGIN_HOURLY_LIMIT", "100000")
    monkeypatch.setenv("EMAIL_LOGIN_MAX_FAILURES_PER_HOUR", "100000")
    try:
        yield email_login, threads
    finally:
        try:
            pool._remove_connections()
        except Exception:
            pass
        cur.execute(f"DROP DATABASE {db}")
        admin.close()


def _run_parallel(fn, args_list):
    """Start all calls together, each in its own connection scope like the API.

    Returns (results, unexpected exceptions). An exhausted pool counts as the
    503 the endpoint answers; anything else unexpected would be a 500.
    """
    import mysql.connector
    import backend.email_login as email_login
    from backend.database import connection_scope

    barrier = threading.Barrier(len(args_list))
    results, unexpected = [], []
    lock = threading.Lock()

    def worker(args):
        barrier.wait()
        try:
            with connection_scope():
                out = fn(*args)
            outcome = ("ok", out)
        except email_login.EmailLoginError as e:
            outcome = (e.code, e.status)
        except mysql.connector.errors.PoolError:
            outcome = ("email_login_busy", 503)
        except Exception as e:
            with lock:
                unexpected.append(f"{type(e).__name__}: {e}")
            return
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return results, unexpected


def _rows(email):
    from backend.database import fetch_one
    return fetch_one("SELECT COUNT(*) AS n FROM email_login_codes WHERE email = %s", (email,))["n"]


def test_parallel_requests_for_one_address_issue_one_code(mysql_login):
    email_login, n = mysql_login
    email = "victim@race.example.com"
    results, unexpected = _run_parallel(email_login.request_code, [(email,)] * n)
    assert unexpected == []
    # The 60 s cooldown allows exactly one; everything else is a clean 429/503.
    assert [r[0] for r in results].count("ok") == 1
    assert _rows(email) == 1
    assert all(status in (429, 503) for code, status in results if code != "ok")


def test_parallel_requests_for_many_addresses_never_deadlock(mysql_login):
    email_login, n = mysql_login
    emails = [(f"user{i}@race.example.com",) for i in range(n)]
    results, unexpected = _run_parallel(email_login.request_code, emails)
    assert unexpected == []
    assert {r[0] for r in results} <= {"ok", "email_login_busy"}
    assert [r[0] for r in results].count("ok") >= 1


def test_parallel_wrong_guesses_respect_the_attempt_limit(mysql_login):
    email_login, n = mysql_login
    email = "guess@race.example.com"
    from backend.database import connection_scope, fetch_one
    with connection_scope():
        email_login.request_code(email)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    results, unexpected = _run_parallel(
        email_login.verify_code, [(email, f"{i:06d}", now) for i in range(n)],
    )
    assert unexpected == []
    row = fetch_one("SELECT attempts FROM email_login_codes WHERE email = %s", (email,))
    assert row["attempts"] <= email_login.MAX_ATTEMPTS
