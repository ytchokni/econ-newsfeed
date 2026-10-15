"""Exercise durable reservations with real concurrent SQL transactions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.database import enrichment_budget
from backend.enrichment import openalex


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params=()):
        self.cursor.execute(sql.replace("INSERT IGNORE", "INSERT OR IGNORE").replace("%s", "?"), params)

    def fetchone(self):
        return self.cursor.fetchone()

    @property
    def rowcount(self):
        return self.cursor.rowcount


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return Cursor(self.connection.cursor())

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE enrichment_daily_budget (provider TEXT, budget_day TEXT, "
                           "request_count INTEGER NOT NULL, PRIMARY KEY (provider, budget_day))")
    clock = {"day": "2026-10-15"}

    @contextmanager
    def connect():
        raw = sqlite3.connect(path, timeout=10)
        raw.create_function("UTC_DATE", 0, lambda: clock['day'])
        try:
            yield Connection(raw)
        finally:
            raw.close()

    def rows():
        with sqlite3.connect(path) as raw:
            return raw.execute("SELECT provider, budget_day, request_count FROM enrichment_daily_budget ORDER BY budget_day").fetchall()

    with patch.object(enrichment_budget, "get_connection", side_effect=connect):
        yield clock, rows, connect


def test_concurrent_workers_cannot_spend_same_remaining_slot(ledger):
    _, rows, _ = ledger
    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(lambda _: enrichment_budget.reserve_request("openalex", 7), range(35)))
    assert sum(results) == 7
    assert rows() == [("openalex", "2026-10-15", 7)]


def test_reloading_module_does_not_reset_budget_and_utc_day_rolls_over(ledger):
    clock, rows, connect = ledger
    assert enrichment_budget.reserve_request("openalex", 1)
    importlib.reload(enrichment_budget)
    with patch.object(enrichment_budget, "get_connection", side_effect=connect):
        assert not enrichment_budget.reserve_request("openalex", 1)
        clock['day'] = "2026-10-16"
        assert enrichment_budget.reserve_request("openalex", 1)
    assert rows() == [("openalex", "2026-10-15", 1), ("openalex", "2026-10-16", 1)]


def test_paid_http_failure_remains_charged(ledger):
    _, rows, _ = ledger
    session = MagicMock()
    session.get.side_effect = requests.RequestException("timeout")
    with (
        patch.object(openalex, "_check_budget", return_value=True),
        patch.object(openalex, "_get_session", return_value=session),
        patch.object(openalex, "DAILY_BUDGET", 1),
    ):
        assert openalex.search_work("Trade", "Jane Doe") is None
        assert openalex.search_work("Trade", "Jane Doe") is None
    assert session.get.call_count == 1
    assert rows() == [("openalex", "2026-10-15", 1)]


def test_rate_limit_retry_requires_a_second_atomic_reservation(ledger):
    _, rows, _ = ledger
    session = MagicMock()
    session.get.return_value.status_code = 429
    session.get.return_value.headers = {"Retry-After": "0"}
    with (
        patch.object(openalex, "_check_budget", return_value=True),
        patch.object(openalex, "DAILY_BUDGET", 1),
        patch.object(openalex.time, "sleep"),
    ):
        assert openalex._get_with_retry(session, "https://example.test", {}).status_code == 429
    assert session.get.call_count == 1
    assert rows() == [("openalex", "2026-10-15", 1)]


def test_budget_store_failure_cannot_dispatch_request():
    session = MagicMock()
    with patch.object(enrichment_budget, "get_connection", side_effect=RuntimeError("database unavailable")):
        with pytest.raises(openalex._BudgetExhausted):
            openalex._get_with_retry(session, "https://example.test", {})
    session.get.assert_not_called()


def test_failed_transaction_rolls_back_and_denies_reservation():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("SQL failure")
    with patch.object(enrichment_budget, "get_connection", return_value=connection):
        assert not enrichment_budget.reserve_request("openalex", 1)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_advisory_check_reads_durable_count_and_fails_closed():
    with patch.object(enrichment_budget, "fetch_one", return_value={"request_count": 3}):
        assert not enrichment_budget.has_budget_remaining("openalex", 3)
    with patch.object(enrichment_budget, "fetch_one", side_effect=RuntimeError("DB unavailable")):
        assert not enrichment_budget.has_budget_remaining("openalex", 3)
