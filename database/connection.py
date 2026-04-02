"""Connection pool and low-level query execution."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

from mysql.connector.pooling import MySQLConnectionPool

from db_config import db_config

if TYPE_CHECKING:
    from mysql.connector.pooling import PooledMySQLConnection

_pool: MySQLConnectionPool | None = None
_DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '10'))

# Thread-local storage for connection reuse within a scope
_local = threading.local()


def _get_pool() -> MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = MySQLConnectionPool(pool_size=_DB_POOL_SIZE, pool_name="econ_pool", **db_config)
    return _pool


def get_connection() -> PooledMySQLConnection:
    """Checkout and return a connection from the connection pool."""
    return _get_pool().get_connection()


def _get_scoped_or_new_connection():
    """Return the thread-local scoped connection if inside connection_scope(), else checkout a new one."""
    scoped = getattr(_local, 'conn', None)
    if scoped is not None:
        return scoped, False  # (connection, should_close)
    return get_connection(), True


@contextmanager
def connection_scope():
    """Hold a single pooled connection for all queries in this scope.

    While inside this context manager, fetch_all() and fetch_one() will
    automatically reuse the same connection instead of checking out new ones.
    """
    conn = get_connection()
    _local.conn = conn
    try:
        yield conn
    finally:
        _local.conn = None
        conn.close()


def execute_query(query: str, params: tuple | list | None = None) -> int:
    """Execute a query with optional parameters and commit. Returns lastrowid."""
    conn, should_close = _get_scoped_or_new_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid
    finally:
        if should_close:
            conn.close()


def fetch_all(query: str, params: tuple | list | None = None) -> list[dict]:
    """Execute a query and fetch all results as a list of dicts."""
    conn, should_close = _get_scoped_or_new_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()
    finally:
        if should_close:
            conn.close()


def fetch_one(query: str, params: tuple | list | None = None) -> dict | None:
    """Execute a query and fetch one result as a dict, or None."""
    conn, should_close = _get_scoped_or_new_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()
    finally:
        if should_close:
            conn.close()
