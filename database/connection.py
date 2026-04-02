"""Connection pool and low-level query execution."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from mysql.connector.pooling import MySQLConnectionPool

from db_config import db_config

if TYPE_CHECKING:
    from mysql.connector.pooling import PooledMySQLConnection

_pool: MySQLConnectionPool | None = None
_DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '10'))


def _get_pool() -> MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = MySQLConnectionPool(pool_size=_DB_POOL_SIZE, pool_name="econ_pool", **db_config)
    return _pool


def get_connection() -> PooledMySQLConnection:
    """Checkout and return a connection from the connection pool."""
    return _get_pool().get_connection()


def execute_query(query: str, params: tuple | list | None = None) -> int:
    """Execute a query with optional parameters and commit. Returns lastrowid."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid


def fetch_all(query: str, params: tuple | list | None = None) -> list[dict]:
    """Execute a query and fetch all results as a list of dicts."""
    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()


def fetch_one(query: str, params: tuple | list | None = None) -> dict | None:
    """Execute a query and fetch one result as a dict, or None."""
    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()


@contextmanager
def connection_scope():
    """Hold a single pooled connection for multiple queries."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def fetch_all_with_conn(conn, query: str, params: tuple | list | None = None) -> list[dict]:
    """Execute a query using an existing connection and fetch all results as dicts."""
    with conn.cursor(dictionary=True) as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def fetch_one_with_conn(conn, query: str, params: tuple | list | None = None) -> dict | None:
    """Execute a query using an existing connection and fetch one result as a dict."""
    with conn.cursor(dictionary=True) as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()
