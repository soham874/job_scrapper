"""MySQL connection management — singleton connection and schema bootstrapping."""

import os
from pathlib import Path

import mysql.connector
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

_connection = None


def get_connection():
    """Return a module-level MySQL connection (created once)."""
    global _connection
    if _connection is None or not _connection.is_connected():
        _connection = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            charset="utf8mb4",
            autocommit=False,
        )
        logger.info(
            "MySQL connection opened: %s@%s:%d/%s",
            MYSQL_USER, MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE,
        )
    return _connection


def ensure_schema():
    """Create the job_scrapper schema if it doesn't exist (before normal connection)."""
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
