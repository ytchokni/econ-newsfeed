"""Concurrency of email-code login against a real MySQL (opt-in).

CI has no MySQL, so these run only with EMAIL_LOGIN_MYSQL_TESTS=1 and DB_*
pointing at a THROWAWAY database (tables are created, test rows deleted):

    docker run -d --rm --name el-mysql -e MYSQL_ROOT_PASSWORD=pw -e MYSQL_DATABASE=el_test \\
        -p 127.0.0.1:3399:3306 mysql:8.0.40
    EMAIL_LOGIN_MYSQL_TESTS=1 DB_HOST=127.0.0.1 DB_PORT=3399 DB_USER=root DB_PASSWORD=pw \\
        DB_NAME=el_test DB_POOL_SIZE=32 poetry run pytest tests/test_email_login_mysql.py -v
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EMAIL_LOGIN_MYSQL_TESTS") != "1",
    reason="needs a throwaway MySQL; set EMAIL_LOGIN_MYSQL_TESTS=1 and DB_*",
)

N_THREADS = int(os.environ.get("EMAIL_LOGIN_RACE_THREADS", "20"))


@pytest.fixture
def mysql_login(monkeypatch):
    import backend.email_login as email_login
    from backend.database import create_tables, execute_query

    create_tables()
    execute_query("DELETE FROM email_login_codes WHERE email LIKE %s", ("%@race.example.com",))
    monkeypatch.setattr(email_login.digest, "RESEND_API_KEY", "")
    monkeypatch.setenv("EMAIL_LOGIN_DEV_LOG_CODES", "true")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("NEXTAUTH_SECRET", "race-test-secret")
    monkeypatch.setenv("EMAIL_LOGIN_HOURLY_LIMIT", "100000")
    monkeypatch.setenv("EMAIL_LOGIN_MAX_FAILURES_PER_HOUR", "100000")
    yield email_login
    execute_query("DELETE FROM email_login_codes WHERE email LIKE %s", ("%@race.example.com",))


def _run_parallel(fn, args_list):
    """Start all calls together; return (results, unexpected exceptions)."""
    barrier = threading.Barrier(len(args_list))
    results, unexpected = [], []
    lock = threading.Lock()

    def worker(args):
        from backend.database import connection_scope
        import backend.email_login as email_login
        import mysql.connector
        barrier.wait()
        try:
            with connection_scope():  # as the API endpoint does
                out = fn(*args)
            with lock:
                results.append(("ok", out))
        except email_login.EmailLoginError as e:
            with lock:
                results.append((e.code, e.status))
        except mysql.connector.errors.PoolError:
            with lock:  # the endpoint answers 503 email_login_busy
                results.append(("email_login_busy", 503))
        except Exception as e:  # anything else would be a 500
            with lock:
                unexpected.append(f"{type(e).__name__}: {e}")

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
    email = "victim@race.example.com"
    results, unexpected = _run_parallel(mysql_login.request_code, [(email,)] * N_THREADS)
    assert unexpected == []
    sent = [r for r in results if r[0] == "ok"]
    # The 60 s cooldown allows exactly one; the hourly cap (5) is the outer bound.
    assert len(sent) == 1
    assert _rows(email) == 1
    assert all(status in (429, 503) for code, status in results if code != "ok")


def test_parallel_requests_for_many_addresses_all_succeed(mysql_login):
    emails = [(f"user{i}@race.example.com",) for i in range(N_THREADS)]
    results, unexpected = _run_parallel(mysql_login.request_code, emails)
    assert unexpected == []
    # Every request gets a code unless the connection pool was momentarily
    # full (503, retryable); none fails with a deadlock.
    assert {r[0] for r in results} <= {"ok", "email_login_busy"}
    assert [r[0] for r in results].count("ok") >= 1


def test_parallel_wrong_guesses_respect_the_attempt_limit(mysql_login):
    email = "guess@race.example.com"
    mysql_login.request_code(email)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    results, unexpected = _run_parallel(
        mysql_login.verify_code, [(email, f"{i:06d}", now) for i in range(N_THREADS)],
    )
    assert unexpected == []
    from backend.database import fetch_one
    row = fetch_one("SELECT attempts FROM email_login_codes WHERE email = %s", (email,))
    assert row["attempts"] <= mysql_login.MAX_ATTEMPTS
