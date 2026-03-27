import os
from pathlib import Path
from typing import Optional

import mysql.connector
from dotenv import load_dotenv

from common.logger import get_logger

logger = get_logger("common.db")

BASE_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BASE_DIR / "migrations"

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


def _ensure_schema():
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


def run_migrations():
    """
    Execute all pending version scripts from the migrations/ directory.

    Tracks applied migrations in a `schema_version` table so each
    script runs exactly once, in alphabetical order.
    """
    _ensure_schema()
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


def get_company_id(company_name: str) -> Optional[int]:
    """Look up a company's id by name. Returns None if not found."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM company_info WHERE company_name = %s", (company_name,)
    )
    row = cursor.fetchone()
    cursor.close()
    return row[0] if row else None


def insert_job(company_id: int, ats_job_id: str, title: str,
               location: str, application_link: str) -> Optional[int]:
    """
    Insert a job into job_info. If the job already exists (duplicate on
    company_id + ats_job_id), log an info message and return None.
    Returns the new row's id if a row was inserted, None otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO job_info (company_id, ats_job_id, title, location, application_link) "
            "VALUES (%s, %s, %s, %s, %s)",
            (company_id, ats_job_id, title, location, application_link),
        )
        conn.commit()
        return cursor.lastrowid
    except mysql.connector.IntegrityError:
        conn.rollback()
        logger.info("Job %s already present in table for company_id %d — skipping", ats_job_id, company_id)
        return None
    except Exception:
        conn.rollback()
        logger.exception("Failed to insert job %s for company_id %d", ats_job_id, company_id)
        return None
    finally:
        cursor.close()


def insert_job_analysis(job_id: int, relevance_score: int,
                        positive_matches: str, negative_matches: str,
                        experience_matches: str) -> bool:
    """
    Insert an analysis row for a job. The match columns expect JSON-encoded strings.
    Returns True on success, False on failure.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO job_analysis (job_id, relevance_score, positive_matches, "
            "negative_matches, experience_matches) VALUES (%s, %s, %s, %s, %s)",
            (job_id, relevance_score, positive_matches, negative_matches, experience_matches),
        )
        conn.commit()
        return True
    except mysql.connector.IntegrityError:
        conn.rollback()
        logger.info("Analysis for job_id %d already exists — skipping", job_id)
        return False
    except Exception:
        conn.rollback()
        logger.exception("Failed to insert analysis for job_id %d", job_id)
        return False
    finally:
        cursor.close()


def update_job_decision(job_id: int, decision: str) -> bool:
    """
    Set the user_decision column for a job.
    decision should be 'applied' or 'rejected'.
    Returns True on success, False on failure.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE job_info SET user_decision = %s WHERE id = %s",
            (decision, job_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        logger.exception("Failed to update decision for job_id %d", job_id)
        return False
    finally:
        cursor.close()


def get_job_by_id(job_id: int) -> Optional[dict]:
    """
    Return a job with its company name.
    Keys: id, title, location, application_link, user_decision, company.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT j.id, j.title, j.location, j.application_link, j.user_decision, "
        "c.company_name "
        "FROM job_info j JOIN company_info c ON j.company_id = c.id "
        "WHERE j.id = %s",
        (job_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "location": row[2],
        "application_link": row[3],
        "user_decision": row[4],
        "company": row[5],
    }


def load_companies_by_ats(ats_name: str) -> list:
    """
    Return companies from the DB that use the given ATS.
    Each dict has keys: id, name, url.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, company_name, ats_link FROM company_info WHERE ats = %s AND ats_link != ''",
        (ats_name,),
    )
    rows = cursor.fetchall()
    cursor.close()
    return [{"id": r[0], "name": r[1], "url": r[2]} for r in rows]
