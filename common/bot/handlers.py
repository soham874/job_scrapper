"""Telegram callback-query handlers — business logic for Apply/Reject decisions.

Keeps the FastAPI routing layer (bot/app.py) thin and focused on HTTP wiring.
"""

from datetime import date

from common.logger import get_logger
from common.db.repository import get_job_by_id, insert_application_status, update_job_decision
from common.notifications.formatter import format_decided_message
from common.notifications.telegram import answer_callback_query, edit_message
from common.referral.service import send_applied_response

logger = get_logger("bot.handlers")


def handle_decision(callback_id: str, message_id: int, job_id: int, action: str) -> dict:
    """
    Process an apply/reject callback.

    Returns a dict suitable as a FastAPI JSON response.
    """
    decision = "applied" if action == "apply" else "rejected"

    # Look up the job to check current state
    job = get_job_by_id(job_id)
    if not job:
        answer_callback_query(callback_id, "Job not found")
        return {"ok": True}

    if job.get("user_decision"):
        answer_callback_query(callback_id, f"Already marked as {job['user_decision']}")
        return {"ok": True}

    # Update DB
    success = update_job_decision(job_id, decision)
    if not success:
        answer_callback_query(callback_id, "Failed to save decision")
        return {"ok": True}

    # Edit the Telegram message to show the decision and remove buttons
    new_text = format_decided_message(job, decision)
    edit_message(message_id, new_text)

    label = "Applied ✅" if decision == "applied" else "Rejected ❌"
    answer_callback_query(callback_id, label)

    if decision == "applied":
        # Send LinkedIn search link + referral message parts
        send_applied_response(job)

        # Record in application_status
        insert_application_status(
            company_id=job["company_id"],
            job_id=job_id,
            applied_on=date.today().isoformat(),
            status="applied",
        )

    logger.info("Job %d marked as %s", job_id, decision)
    return {"ok": True}
