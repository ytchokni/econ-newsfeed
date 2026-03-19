"""Connection pool and low-level query execution."""
import os

from mysql.connector.pooling import MySQLConnectionPool

from db_config import db_config

_pool: "MySQLConnectionPool | None" = None
_DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '5'))


def _get_pool() -> "MySQLConnectionPool":
    global _pool
    if _pool is None:
        _pool = MySQLConnectionPool(pool_size=_DB_POOL_SIZE, pool_name="econ_pool", **db_config)
    return _pool


def get_connection():
    """Checkout and return a connection from the connection pool."""
    return _get_pool().get_connection()


def execute_query(query, params=None):
    """Execute a query with optional parameters and commit. Returns lastrowid."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid


def fetch_all(query, params=None):
    """Execute a query and fetch all results as a list of dicts."""
    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()


def fetch_one(query, params=None):
    """Execute a query and fetch one result as a dict, or None."""
    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()
