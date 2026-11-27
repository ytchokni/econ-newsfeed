"""get_connection waits briefly for a free pooled connection instead of failing."""
from unittest.mock import MagicMock, patch

import pytest
from mysql.connector.errors import PoolError

from backend.database import connection


def test_retries_until_a_connection_is_free():
    pool = MagicMock()
    conn = object()
    pool.get_connection.side_effect = [PoolError("exhausted"), PoolError("exhausted"), conn]
    with patch.object(connection, "_get_pool", return_value=pool), \
         patch.object(connection.time, "sleep"):
        assert connection.get_connection() is conn
    assert pool.get_connection.call_count == 3


def test_gives_up_after_the_wait():
    pool = MagicMock()
    pool.get_connection.side_effect = PoolError("exhausted")
    clock = iter([0.0, 1.0, 6.0])
    with patch.object(connection, "_get_pool", return_value=pool), \
         patch.object(connection.time, "sleep"), \
         patch.object(connection.time, "monotonic", side_effect=lambda: next(clock)):
        with pytest.raises(PoolError):
            connection.get_connection()
