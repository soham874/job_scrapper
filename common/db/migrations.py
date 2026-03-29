"""Database migration runner — applies versioned SQL scripts exactly once."""

from pathlib import Path

import mysql.connector

from common.logger import get_logger
from common.db.connection import ensure_schema, get_connection

logger = get_logger("db.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def run_migrations():
    """
    Execute all pending version scripts from the migrations/ directory.

    Tracks applied migrations in a `schema_version` table so each
    script runs exactly once, in alphabetical order.
    """
    ensure_schema()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version VARCHAR(255) PRIMARY KEY,"
        "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.commit()

    cursor.execute("SELECT version FROM schema_version")
    applied = {row[0] for row in cursor.fetchall()}

    if not MIGRATIONS_DIR.exists():
        logger.warning("Migrations directory not found: %s", MIGRATIONS_DIR)
        cursor.close()
        return

    scripts = sorted(MIGRATIONS_DIR.glob("V*.sql"))
    for script in scripts:
        version = script.stem  # e.g. V001_create_tables
        if version in applied:
            logger.debug("Migration %s already applied — skipping", version)
            continue

        logger.info("Applying migration: %s", version)
        sql = script.read_text(encoding="utf-8")
        try:
            # Execute each statement individually (MySQL connector
            # does not support multi-statement executescript).
            # Commit after each so DDL with FK references resolves.
            for raw_statement in sql.split(";"):
                # Strip SQL comment lines before checking if anything remains
                lines = [
                    line for line in raw_statement.splitlines()
                    if line.strip() and not line.strip().startswith("--")
                ]
                statement = "\n".join(lines).strip()
                if statement:
                    cursor.execute(statement)
                    conn.commit()
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (%s)", (version,)
            )
            conn.commit()
            logger.info("Migration %s applied successfully", version)
        except mysql.connector.IntegrityError:
            # Another process already applied this migration concurrently — safe to skip
            conn.rollback()
            logger.info("Migration %s already recorded by another process — skipping", version)
        except Exception:
            conn.rollback()
            logger.exception("Migration %s FAILED", version)
            raise
    cursor.close()
