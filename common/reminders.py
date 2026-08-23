"""Follow-up reminders — the nudge for applications the user is sitting on.

The sweeper reads silence from the user and turns it into a rejection. This
reads silence from the *company*, or from the user's own follow-up date, and
turns it into a message.

Two things bring an application up:

  * its next_important_date has arrived, or
  * its status has not moved for longer than that status is meant to sit
    quietly (common.applications.STALE_AFTER_DAYS).

Unlike the sweeper this does send to Telegram, and each nudge carries the job
card's own buttons — so the reply to "this has gone quiet" is one tap on the
message itself.
"""

import threading

from common.applications import ACTIVE_STATUSES, STALE_AFTER_DAYS
from common.bot.tracker import notify_due
from common.config import (
    REMINDER_INTERVAL_SECONDS,
    REMINDER_MAX_PER_RUN,
    REMINDER_RENOTIFY_HOURS,
)
from common.db.repository import get_due_applications, mark_applications_notified
from common.logger import get_logger

logger = get_logger("common.reminders")


def run_once() -> int:
    """
    Send one round of nudges. Returns how many went out.

    Rows are marked notified after the sends rather than before, so a crash
    mid-round re-nudges rather than silently swallowing a follow-up. Sending
    twice is a nuisance; never sending is the failure that matters.
    """
    logger.info("=== Reminder pass started (renotify=%dh) ===", REMINDER_RENOTIFY_HOURS)
    due = get_due_applications(ACTIVE_STATUSES, STALE_AFTER_DAYS, REMINDER_RENOTIFY_HOURS)
    if not due:
        logger.info("=== Reminder pass finished | nothing due ===")
        return 0

    batch = due[:REMINDER_MAX_PER_RUN]
    if len(due) > len(batch):
        logger.info("%d application(s) due — sending %d this pass", len(due), len(batch))

    sent = []
    for app in batch:
        try:
            notify_due(app)
            sent.append(app["job_id"])
        except Exception:
            logger.exception("Failed to send reminder for job %s", app.get("job_id"))

    mark_applications_notified(sent)
    logger.info("=== Reminder pass finished | %d nudge(s) sent ===", len(sent))
    return len(sent)


def _reminder_loop():
    """Blocking loop that runs run_once every REMINDER_INTERVAL_SECONDS."""
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Unhandled error in reminder loop")

        logger.info("Sleeping %d seconds until next reminder pass...", REMINDER_INTERVAL_SECONDS)
        threading.Event().wait(REMINDER_INTERVAL_SECONDS)


def start_reminders():
    """Start the reminder loop in a daemon thread."""
    t = threading.Thread(target=_reminder_loop, daemon=True, name="application-reminders")
    t.start()
    logger.info("Reminder thread started (interval=%ds, renotify=%dh, max=%d)",
                REMINDER_INTERVAL_SECONDS, REMINDER_RENOTIFY_HOURS, REMINDER_MAX_PER_RUN)
    return t
