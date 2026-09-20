"""Ghosting sweep — closes the applications that were never answered.

Three passes read silence in this codebase, and they read different silences.
common.sweeper reads the user's: a job notification nobody acted on becomes a
rejection. common.reminders reads the company's, for a while: an application
that has not moved gets a nudge. This one is what happens when the nudges run
out — an application untouched for GHOST_AFTER_DAYS is moved to 'ghosted'.

That matters beyond tidiness. 'ghosted' is a terminal status, so the move
stops the application's idle clock, drops it out of /active and /today, and
takes it off the reminder loop's list. Without it, an application nobody will
ever hear about again is nudged forever and keeps counting days.

The clock is application_status.updated_at — last activity, not application
date. Any touch restarts it: a status change, a reminder, a recorded contact.
So a job applied to three months ago but moved to 'interview' last week is
nowhere near being ghosted, which is the point.

Unlike the sweeper, this does send to Telegram. A status the user did not set
changing on its own is worth one line in the chat, and the summary carries a
button per application so a wrong call is one tap from being put back.
"""

import threading

from common.applications import ACTIVE_STATUSES
from common.config import (
    GHOST_AFTER_DAYS,
    GHOST_NOTIFY,
    GHOST_RUN_AT_HOUR,
    GHOST_RUN_AT_MINUTE,
)
from common.db.repository import ghost_idle_applications
from common.logger import get_logger
from common.notifications.formatter import format_ghost_summary, make_list_keyboard
from common.notifications.telegram import send_message
from common.scheduling import seconds_until_daily_slot

logger = get_logger("common.ghosting")

# Applications named in the summary message. Past this the list is a wall of
# text and the keyboard stops fitting on a phone; the rest are on /stats and
# the dashboard.
_SUMMARY_LIMIT = 8


def run_once() -> int:
    """
    Ghost every application past the idle window. Returns how many moved.

    Only the statuses still in play are considered. A rejection is a company
    that answered, and a ghost is already a ghost — neither is silence this
    pass has anything to say about.
    """
    logger.info("=== Ghost sweep started (window=%dd) ===", GHOST_AFTER_DAYS)
    ghosted = ghost_idle_applications(GHOST_AFTER_DAYS, ACTIVE_STATUSES)
    if not ghosted:
        logger.info("=== Ghost sweep finished | nothing idle past %d days ===", GHOST_AFTER_DAYS)
        return 0

    for app in ghosted:
        logger.info("Job %s (%s — %s) ghosted after %s idle days, was '%s'",
                    app.get("job_id"), app.get("company"), app.get("title"),
                    app.get("days_idle"), app.get("status"))

    if GHOST_NOTIFY:
        try:
            # The rows still carry the status they had before the sweep, which
            # the summary text names. The buttons show what they are now.
            buttons = [dict(app, status="ghosted") for app in ghosted[:_SUMMARY_LIMIT]]
            markup = make_list_keyboard(buttons, 0, 1)
            send_message(format_ghost_summary(ghosted, GHOST_AFTER_DAYS, _SUMMARY_LIMIT),
                         reply_markup=markup if markup["inline_keyboard"] else None)
        except Exception:
            # The rows are already ghosted. A failed message is worth a log
            # line, not a re-run that would find nothing to do anyway.
            logger.exception("Failed to send the ghost-sweep summary")

    logger.info("=== Ghost sweep finished | %d application(s) ghosted ===", len(ghosted))
    return len(ghosted)


def _ghost_loop():
    """Blocking loop that runs run_once once a day, at the configured hour.

    The first pass runs at startup rather than waiting for the slot. The work
    is idempotent — a row past the window is ghosted once and then no longer
    matches — and a bot restarted more often than daily would otherwise never
    reach its slot at all.
    """
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Unhandled error in ghost sweep loop")

        delay = seconds_until_daily_slot(GHOST_RUN_AT_HOUR, GHOST_RUN_AT_MINUTE)
        logger.info("Sleeping %.0f seconds until the next ghost sweep (%02d:%02d)...",
                    delay, GHOST_RUN_AT_HOUR, GHOST_RUN_AT_MINUTE)
        threading.Event().wait(delay)


def start_ghoster():
    """Start the daily ghost sweep in a daemon thread."""
    t = threading.Thread(target=_ghost_loop, daemon=True, name="application-ghoster")
    t.start()
    logger.info("Ghoster thread started (window=%dd, daily at %02d:%02d, notify=%s)",
                GHOST_AFTER_DAYS, GHOST_RUN_AT_HOUR, GHOST_RUN_AT_MINUTE, GHOST_NOTIFY)
    return t
