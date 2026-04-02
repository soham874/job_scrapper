"""Database repository — all domain-specific queries (jobs, companies, analysis, applications)."""

from typing import Optional

import mysql.connector

from common.logger import get_logger
from common.db.connection import get_connection

logger = get_logger("db.repository")


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
    Keys: id, title, location, application_link, user_decision, company, ats_job_id, company_id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT j.id, j.title, j.location, j.application_link, j.user_decision, "
        "c.company_name, j.ats_job_id, j.company_id "
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
        "ats_job_id": row[6],
        "company_id": row[7],
    }


def insert_application_status(company_id: int, job_id: int,
                              applied_on: str, status: str = "applied") -> bool:
    """
    Insert a row into application_status when the user accepts a job.
    Returns True on success, False on failure.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO application_status (company_id, job_id, applied_on, status) "
            "VALUES (%s, %s, %s, %s)",
            (company_id, job_id, applied_on, status),
        )
        conn.commit()
        return True
    except mysql.connector.IntegrityError:
        conn.rollback()
        logger.info("Application status for job_id %d already exists — skipping", job_id)
        return False
    except Exception:
        conn.rollback()
        logger.exception("Failed to insert application status for job_id %d", job_id)
        return False
    finally:
        cursor.close()


def get_decided_jobs_with_keywords() -> list:
    """
    Return all jobs that have a user_decision and an analysis row.
    Each dict has: user_decision, positive_matches, negative_matches (JSON strings).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT j.user_decision, ja.positive_matches, ja.negative_matches "
        "FROM job_info j "
        "JOIN job_analysis ja ON ja.job_id = j.id "
        "WHERE j.user_decision IS NOT NULL"
    )
    rows = cursor.fetchall()
    cursor.close()
    return [
        {
            "user_decision": r[0],
            "positive_matches": r[1],
            "negative_matches": r[2],
        }
        for r in rows
    ]


def load_keyword_weight_overrides() -> dict:
    """
    Return a dict of keyword -> multiplier from the keyword_weight_overrides table.
    Returns an empty dict if the table is empty or doesn't exist yet.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT keyword, multiplier FROM keyword_weight_overrides")
        rows = cursor.fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        logger.debug("keyword_weight_overrides table not available yet")
        return {}
    finally:
        cursor.close()


def upsert_keyword_weight_override(keyword: str, multiplier: float,
                                   accept_count: int, reject_count: int,
                                   sample_count: int, lift: float) -> bool:
    """
    Insert or update a keyword weight override row.
    Returns True on success, False on failure.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO keyword_weight_overrides "
            "(keyword, multiplier, accept_count, reject_count, sample_count, lift) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "multiplier = VALUES(multiplier), accept_count = VALUES(accept_count), "
            "reject_count = VALUES(reject_count), sample_count = VALUES(sample_count), "
            "lift = VALUES(lift)",
            (keyword, multiplier, accept_count, reject_count, sample_count, lift),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception("Failed to upsert keyword weight override for '%s'", keyword)
        return False
    finally:
        cursor.close()


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
