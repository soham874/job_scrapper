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
