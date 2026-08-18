"""Database repository — all domain-specific queries (jobs, companies, analysis, applications).

Every function checks a connection out of the pool for its own call and hands it
back before returning. Cursors are buffered so a result set is never left
half-read on a connection that is about to be reused.
"""

from typing import Optional

import mysql.connector

from common.logger import get_logger
from common.db.connection import get_connection

logger = get_logger("db.repository")


def get_company_id(company_name: str) -> Optional[int]:
    """Look up a company's id by name. Returns None if not found."""
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "SELECT id FROM company_info WHERE company_name = %s", (company_name,)
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    return row[0] if row else None


def insert_job(company_id: int, ats_job_id: str, title: str,
               location: str, application_link: str) -> Optional[int]:
    """
    Insert a job into job_info. If the job already exists (duplicate on
    company_id + ats_job_id), log an info message and return None.
    Returns the new row's id if a row was inserted, None otherwise.
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "INSERT INTO job_info (company_id, ats_job_id, title, location, application_link) "
                "VALUES (%s, %s, %s, %s, %s)",
                (company_id, ats_job_id, title, location, application_link),
            )
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
                        experience_matches: str,
                        job_description: Optional[str] = None) -> bool:
    """
    Insert an analysis row for a job. The match columns expect JSON-encoded strings.
    job_description holds the plain-text description, used later by the resume
    tailoring module. Returns True on success, False on failure.
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "INSERT INTO job_analysis (job_id, relevance_score, positive_matches, "
                "negative_matches, experience_matches, job_description) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (job_id, relevance_score, positive_matches, negative_matches,
                 experience_matches, job_description),
            )
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
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "UPDATE job_info SET user_decision = %s WHERE id = %s",
                (decision, job_id),
            )
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
    Keys: id, title, location, application_link, user_decision, company, ats_job_id,
    company_id, linkedin_company_ids (comma-separated string, None if not set).
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "SELECT j.id, j.title, j.location, j.application_link, j.user_decision, "
                "c.company_name, j.ats_job_id, j.company_id, c.linkedin_company_ids "
                "FROM job_info j JOIN company_info c ON j.company_id = c.id "
                "WHERE j.id = %s",
                (job_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()

    if not row:
        logger.warning("Job %d not found", job_id)
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
        "linkedin_company_ids": row[8],
    }


def get_job_analysis(job_id: int) -> Optional[dict]:
    """
    Return the analysis row for a job.
    Keys: relevance_score, positive_matches, negative_matches, experience_matches,
    job_description. The match values are JSON-encoded strings as stored.
    job_description is None for jobs scraped before migration V012.
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "SELECT relevance_score, positive_matches, negative_matches, "
                "experience_matches, job_description FROM job_analysis WHERE job_id = %s",
                (job_id,),
            )
            row = cursor.fetchone()
        except Exception:
            logger.exception("Failed to load analysis for job_id %d", job_id)
            return None
        finally:
            cursor.close()

    if not row:
        return None
    return {
        "relevance_score": row[0],
        "positive_matches": row[1],
        "negative_matches": row[2],
        "experience_matches": row[3],
        "job_description": row[4],
    }


def insert_application_status(company_id: int, job_id: int,
                              applied_on: str, status: str = "applied") -> bool:
    """
    Insert a row into application_status when the user accepts a job.
    Returns True on success, False on failure.
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "INSERT INTO application_status (company_id, job_id, applied_on, status) "
                "VALUES (%s, %s, %s, %s)",
                (company_id, job_id, applied_on, status),
            )
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
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "SELECT j.user_decision, ja.positive_matches, ja.negative_matches "
                "FROM job_info j "
                "JOIN job_analysis ja ON ja.job_id = j.id "
                "WHERE j.user_decision IS NOT NULL"
            )
            rows = cursor.fetchall()
        finally:
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
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
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
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
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
            return True
        except Exception:
            conn.rollback()
            logger.exception("Failed to upsert keyword weight override for '%s'", keyword)
            return False
        finally:
            cursor.close()


def load_companies_by_ats(ats_name: str) -> list:
    """
    Return companies from the DB that use the given ATS and are enabled for
    tracking. `enabled` mirrors the "Enable in tracker" column of the sheet.
    Each dict has keys: id, name, url.
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "SELECT id, company_name, ats_link FROM company_info "
                "WHERE ats = %s AND ats_link != '' AND enabled = 1",
                (ats_name,),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
    return [{"id": r[0], "name": r[1], "url": r[2]} for r in rows]


def upsert_company(company_name: str, base_country: str, target_location: str,
                   ats: str, ats_link: str, enabled: bool,
                   linkedin_company_ids: Optional[str] = None) -> str:
    """
    Insert or update a company by name, stamping synced_at.

    linkedin_company_ids is an optional comma-separated string (a company can
    exist as multiple distinct entities on LinkedIn) — pass None to
    clear/leave it unset when the sheet has no value for a company.

    Returns 'inserted', 'updated' or 'unchanged'. Raises on failure so the
    caller can abort the sync rather than half-applying the sheet.
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "INSERT INTO company_info "
                "(company_name, base_country, target_location, ats, ats_link, enabled, "
                "linkedin_company_ids, synced_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) "
                "ON DUPLICATE KEY UPDATE "
                "base_country = VALUES(base_country), "
                "target_location = VALUES(target_location), "
                "ats = VALUES(ats), "
                "ats_link = VALUES(ats_link), "
                "enabled = VALUES(enabled), "
                "linkedin_company_ids = VALUES(linkedin_company_ids), "
                "synced_at = NOW()",
                (company_name, base_country, target_location, ats, ats_link,
                 int(enabled), linkedin_company_ids),
            )
            # MySQL reports 1 for a fresh insert, 2 when an existing row changed,
            # and 0 when the row already matched.
            if cursor.rowcount == 1:
                return "inserted"
            return "updated" if cursor.rowcount == 2 else "unchanged"
        except Exception:
            logger.exception("Failed to upsert company '%s'", company_name)
            raise
        finally:
            cursor.close()


def disable_companies_absent_from(company_names: list) -> list:
    """
    Disable every currently-enabled company whose name is not in the given list.

    Used to honour deletions from the sheet. Job history is left untouched.
    Returns the names that were disabled.
    """
    if not company_names:
        raise ValueError("Refusing to disable all companies from an empty name list")

    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            placeholders = ", ".join(["%s"] * len(company_names))
            cursor.execute(
                f"SELECT company_name FROM company_info "
                f"WHERE enabled = 1 AND company_name NOT IN ({placeholders})",
                tuple(company_names),
            )
            stale = [r[0] for r in cursor.fetchall()]
            if stale:
                cursor.execute(
                    f"UPDATE company_info SET enabled = 0 "
                    f"WHERE enabled = 1 AND company_name NOT IN ({placeholders})",
                    tuple(company_names),
                )
            return stale
        except Exception:
            logger.exception("Failed to disable companies absent from the sheet")
            raise
        finally:
            cursor.close()


def seconds_since_last_sync() -> Optional[int]:
    """
    Seconds since any company row was last reconciled against the sheet,
    or None if no sync has ever run.
    """
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute(
                "SELECT TIMESTAMPDIFF(SECOND, MAX(synced_at), NOW()) FROM company_info"
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    return row[0] if row and row[0] is not None else None


def count_companies() -> int:
    """Total rows in company_info, used as a sanity check before syncing."""
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            cursor.execute("SELECT COUNT(*) FROM company_info")
            row = cursor.fetchone()
        finally:
            cursor.close()
    return row[0] if row else 0
