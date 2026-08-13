"""Telegram callback-query handlers — business logic for Apply/Reject decisions.

Keeps the FastAPI routing layer (bot/app.py) thin and focused on HTTP wiring.
"""

import json
from datetime import date

from common.logger import get_logger
from common.db.repository import (
    get_job_analysis,
    get_job_by_id,
    insert_application_status,
    update_job_decision,
)
from common.notifications.formatter import format_decided_message
from common.notifications.telegram import answer_callback_query, edit_message
from common.referral.service import (
    build_linkedin_search_url,
    build_referral_text,
    send_referral_text,
)
from common.resume.dispatch import dispatch_async

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
    referral_url = (
        build_linkedin_search_url(job.get("company", ""), job.get("linkedin_company_ids"))
        if decision == "applied" else None
    )
    new_text = format_decided_message(job, decision, referral_search_url=referral_url)
    edit_message(message_id, new_text)

    label = "Applied ✅" if decision == "applied" else "Rejected ❌"
    answer_callback_query(callback_id, label)

    if decision == "applied":
        # Record in application_status
        insert_application_status(
            company_id=job["company_id"],
            job_id=job_id,
            applied_on=date.today().isoformat(),
            status="applied",
        )

        # Read the stored analysis here, on the request thread, so resume
        # generation needs no DB access of its own.
        analysis = get_job_analysis(job_id) or {}

        # Jobs scraped before migration V012 have no stored description, and some
        # ATS feeds never provide one. The analyzer's matched keywords are always
        # present though, and stand in as the tailoring signal.
        try:
            keywords = json.loads(analysis.get("positive_matches") or "[]")
        except (TypeError, ValueError):
            logger.warning("Could not parse positive_matches for job %d", job_id)
            keywords = []

        # The referral message is sent as the resume's caption so the user can
        # forward text and attachment in one go. That means waiting on the
        # generation thread, which is why nothing is sent here.
        referral_text = build_referral_text(job)
        started = dispatch_async(
            job,
            description=analysis.get("job_description") or "",
            reply_to_message_id=message_id,
            keywords=keywords,
            referral_text=referral_text,
        )
        if started is None:
            # Resume generation is off — nothing to attach it to.
            send_referral_text(referral_text, reply_to_message_id=message_id)

    logger.info("Job %d marked as %s", job_id, decision)
    return {"ok": True}
