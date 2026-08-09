"""Bridges resume generation to Telegram delivery.

Kept separate from service.py so that module stays free of Telegram and DB
imports and can be exercised standalone by run_scripts/test_resume.py.

Generation runs in a background thread: a Gemini call plus a LaTeX compile takes
tens of seconds, and Telegram re-delivers webhooks that do not return promptly.
"""

import threading
from typing import Optional

from common.logger import get_logger
from common.notifications.telegram import is_configured, send_document, send_message
from common.resume import config
from common.resume.service import generate_resume_for_job

logger = get_logger("resume.dispatch")


def send_resume_for_job(job: dict, description: str = "",
                        reply_to_message_id: Optional[int] = None,
                        keywords: Optional[list] = None) -> None:
    """
    Generate a tailored resume and send it to Telegram.

    Performs no database access, so it is safe to run off the request thread.
    Swallows every error — this is a best-effort enhancement to the Apply flow.
    """
    try:
        company = job.get("company", "Unknown")
        title = job.get("title", "Unknown")

        pdf_path = generate_resume_for_job(job, description=description,
                                           keywords=keywords)
        if not pdf_path:
            logger.error("Resume generation returned no PDF for job %s", job.get("id"))
            if is_configured():
                send_message(
                    "⚠️ Could not generate a tailored resume for "
                    f"<b>{title}</b> at <b>{company}</b>. See logs/resume.*.log.",
                    reply_to_message_id=reply_to_message_id,
                )
            return

        if not is_configured():
            logger.info("Telegram not configured — resume left at %s", pdf_path)
            return

        tailored = config.gemini_available()
        caption = (
            f"📄 Resume for <b>{title}</b> at <b>{company}</b>\n"
            f"{'Tailored to this posting.' if tailored else 'Base resume (tailoring off).'}"
        )
        if tailored:
            caption += "\n\n<i>Read it before you send it — check the verbs and numbers.</i>"

        send_document(str(pdf_path), caption=caption,
                      reply_to_message_id=reply_to_message_id)
    except Exception:
        logger.exception("Unhandled error while producing resume for job %s", job.get("id"))


def dispatch_async(job: dict, description: str = "",
                   reply_to_message_id: Optional[int] = None,
                   keywords: Optional[list] = None) -> Optional[threading.Thread]:
    """
    Kick off resume generation in a daemon thread and return immediately.

    Returns the thread (useful in tests), or None when the feature is switched off.
    """
    if not config.RESUME_ENABLED:
        logger.debug("RESUME_ENABLED is off — skipping resume generation")
        return None

    thread = threading.Thread(
        target=send_resume_for_job,
        args=(job, description, reply_to_message_id, keywords),
        daemon=True,
        name=f"resume-job-{job.get('id')}",
    )
    thread.start()
    logger.info("Resume generation started in background for job %s", job.get("id"))
    return thread
