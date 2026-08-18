"""MySQL connection management — pooled connections and schema bootstrapping.

Connections come from a pool and are checked out for the duration of a single
operation, never held for the life of the process. Two reasons:

  * A connection is not thread-safe. The borgs scrape with eight workers and
    the FastAPI apps serve requests on a thread pool, so a shared connection
    means interleaved packets on one socket, and commit()/rollback() from one
    thread landing on another thread's half-written transaction.

  * autocommit is off and MySQL defaults to REPEATABLE READ, so the first
    SELECT opens a transaction and pins a snapshot. A connection that reads
    without ever committing keeps serving that snapshot and stops seeing rows
    other processes commit afterwards. get_connection() always ends the
    transaction before the connection goes back to the pool.
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import mysql.connector
from mysql.connector.errors import PoolError
from mysql.connector.pooling import CNX_POOL_MAXSIZE, MySQLConnectionPool
from dotenv import load_dotenv

from common.logger import get_logger

logger = get_logger("db.connection")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env from project root
load_dotenv(BASE_DIR / ".env")

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "job_scrapper")

# One per borg worker (8) plus headroom for the API thread and for a held
# advisory lock, which occupies a connection while the work it guards runs.
MYSQL_POOL_SIZE = min(int(os.getenv("MYSQL_POOL_SIZE", "10")), CNX_POOL_MAXSIZE)

# A checkout that cannot be served is retried briefly rather than failing the
# whole operation — bursts of eight workers can momentarily drain the pool.
POOL_ACQUIRE_TIMEOUT_SECONDS = 10
_POOL_RETRY_INTERVAL_SECONDS = 0.05

_pool = None
_pool_lock = Lock()


def _get_pool():
    """Build the pool on first use. Safe to call from any thread."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = MySQLConnectionPool(
                    pool_name="job_scrapper",
                    pool_size=MYSQL_POOL_SIZE,
                    # Resets session state (and any stray transaction) when a
                    # connection is handed back.
                    pool_reset_session=True,
                    host=MYSQL_HOST,
                    port=MYSQL_PORT,
                    user=MYSQL_USER,
                    password=MYSQL_PASSWORD,
                    database=MYSQL_DATABASE,
                    charset="utf8mb4",
                    autocommit=False,
                )
                logger.info(
                    "MySQL pool created (size=%d): %s@%s:%d/%s",
                    MYSQL_POOL_SIZE, MYSQL_USER, MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE,
                )
    return _pool


def _checkout():
    """Take a connection from the pool, waiting briefly if all are in use."""
    deadline = time.monotonic() + POOL_ACQUIRE_TIMEOUT_SECONDS
    while True:
        try:
            return _get_pool().get_connection()
        except PoolError:
            if time.monotonic() >= deadline:
                logger.error(
                    "Pool exhausted after %ds — all %d connections are checked out",
                    POOL_ACQUIRE_TIMEOUT_SECONDS, MYSQL_POOL_SIZE,
                )
                raise
            time.sleep(_POOL_RETRY_INTERVAL_SECONDS)


@contextmanager
def get_connection():
    """
    Check a connection out of the pool for the duration of the block.

    The transaction is always closed before the connection is returned:
    committed on a clean exit, rolled back if the block raised. Read-only
    blocks need this too — see the module docstring.

    Usage:
        with get_connection() as conn:
            cursor = conn.cursor(buffered=True)
            ...
    """
    conn = _checkout()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.exception("Rollback failed while unwinding a database error")
        raise
    finally:
        # Returns it to the pool rather than disconnecting.
        conn.close()


@contextmanager
def advisory_lock(name: str, timeout_seconds: int = 5):
    """
    Serialise a section across processes using a MySQL named lock.

    Yields True if the lock was acquired, False if another process holds it.
    Each borg runs in its own process, so this stops three concurrent sheet
    syncs from writing the same rows at once.

    Holds its own connection for the whole block, because a named lock belongs
    to the session that took it. The guarded work checks out its own
    connections, so the pool needs at least one slot spare.
    """
    conn = _checkout()
    cursor = conn.cursor(buffered=True)
    acquired = False
    try:
        cursor.execute("SELECT GET_LOCK(%s, %s)", (name, timeout_seconds))
        rows = cursor.fetchall()
        acquired = bool(rows and rows[0][0] == 1)
        yield acquired
    finally:
        if acquired:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))
                cursor.fetchall()
            except Exception:
                logger.exception("Failed to release advisory lock '%s'", name)
        cursor.close()
        try:
            conn.commit()
        except Exception:
            logger.exception("Failed to close the advisory-lock transaction")
        conn.close()


def ensure_schema():
    """Create the job_scrapper schema if it doesn't exist (before the pool is built)."""
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4",
    )
    cursor = conn.cursor()
    cursor.execute(
        "CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        % MYSQL_DATABASE
    )
    conn.commit()
    cursor.close()
    conn.close()
    logger.info("Ensured schema '%s' exists", MYSQL_DATABASE)
