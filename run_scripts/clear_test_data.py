"""Clear test data (ats_job_id LIKE 'TEST-%') from the database.

Run on every startup so the /test endpoint always gets a clean slate.
"""

from common.db.connection import get_connection
from common.logger import get_logger

logger = get_logger("clear_test_data")


def clear():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM application_status WHERE job_id IN "
            "(SELECT id FROM job_info WHERE ats_job_id LIKE 'TEST-%%')"
        )
        cursor.execute(
            "DELETE FROM job_analysis WHERE job_id IN "
            "(SELECT id FROM job_info WHERE ats_job_id LIKE 'TEST-%%')"
        )
        cursor.execute(
            "DELETE FROM job_info WHERE ats_job_id LIKE 'TEST-%%'"
        )
        conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("Cleared %d test job(s) from DB", deleted)
        else:
            logger.info("No test data to clear")
    except Exception:
        conn.rollback()
        logger.exception("Failed to clear test data")
    finally:
        cursor.close()


if __name__ == "__main__":
    clear()
