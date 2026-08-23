"""Telegram callback-query handlers — business logic for the Apply decision.

Apply is the only decision the user makes by hand. Rejection is never clicked:
common.sweeper marks a job rejected once it has gone SWEEP_AGE_HOURS without
an Apply, so this module only ever deals with the positive case.

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
from common.notifications.formatter import format_applied_message
from common.notifications.telegram import answer_callback_query, edit_message
from common.referral.service import (
    build_linkedin_search_url,
    build_referral_text,
    send_referral_text,
)
from common.resume.dispatch import dispatch_async

logger = get_logger("bot.handlers")


def handle_apply(callback_id: str, message_id: int, job_id: int) -> dict:
    """
    Process an apply callback.

    Returns a dict suitable as a FastAPI JSON response.
    """
    # Look up the job to check current state
    job = get_job_by_id(job_id)
    if not job:
        answer_callback_query(callback_id, "Job not found")
        return {"ok": True}

    if job.get("user_decision"):
        answer_callback_query(callback_id, f"Already marked as {job['user_decision']}")
        return {"ok": True}

    # Update DB
    success = update_job_decision(job_id, "applied")
    if not success:
        answer_callback_query(callback_id, "Failed to save decision")
        return {"ok": True}

    # Edit the Telegram message to show the decision and remove the button
    referral_url = build_linkedin_search_url(
        job.get("company", ""), job.get("linkedin_company_ids")
    )
    new_text = format_applied_message(job, referral_search_url=referral_url)
    edit_message(message_id, new_text)

    answer_callback_query(callback_id, "Applied ✅")

    # Record in application_status
    insert_application_status(
        company_id=job["company_id"],
        job_id=job_id,
        applied_on=date.today().isoformat(),
        status="applied",
    )

    deliver_resume_bundle(job, message_id)

    logger.info("Job %d marked as applied", job_id)
    return {"ok": True}


def deliver_resume_bundle(job: dict, reply_to_message_id: int) -> None:
    """
    Generate the tailored resume for a job and send it with the referral text.

    Shared with the tracker's Resume button, which re-runs this for a job the
    user applied to earlier — the two paths must stay identical, because the
    thing being produced is the same artifact.

    `job` needs company, title, location, ats_job_id and an `id` key.
    """
    job_id = job.get("id")

    # Read the stored analysis here, on the request thread, so resume
    # generation needs no DB access of its own.
    analysis = get_job_analysis(job_id) or {}

    # Jobs scraped before migration V012 have no stored description, and some
    # ATS feeds never provide one. The analyzer's matched keywords are always
    # present though, and stand in as the tailoring signal.
    try:
        keywords = json.loads(analysis.get("positive_matches") or "[]")
    except (TypeError, ValueError):
        logger.warning("Could not parse positive_matches for job %s", job_id)
        keywords = []

    # The referral message is sent as the resume's caption so the user can
    # forward text and attachment in one go. That means waiting on the
    # generation thread, which is why nothing is sent here.
    referral_text = build_referral_text(job)
    started = dispatch_async(
        job,
        description=analysis.get("job_description") or "",
        reply_to_message_id=reply_to_message_id,
        keywords=keywords,
        referral_text=referral_text,
    )
    if started is None:
        # Resume generation is off — nothing to attach it to.
        send_referral_text(referral_text, reply_to_message_id=reply_to_message_id)
