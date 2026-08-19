"""Stale-job sweeper — turns an unanswered notification into a rejection.

There is no Reject button. The only decision the user makes explicitly is
Apply; everything else is decided by silence. This module is what reads that
silence: once a job has gone SWEEP_AGE_HOURS without being applied to, it is
marked 'rejected' so the learner sees it as a negative signal.

Nothing is sent to Telegram from here. The original notification is left
untouched — by the time a job is swept the message has already aged out of the
chat, so there is no live button to reconcile and no reason to spend an API
call editing a message the user will never see again.
"""

import threading

from common.config import SWEEP_AGE_HOURS, SWEEP_INTERVAL_SECONDS
from common.db.repository import expire_stale_jobs
from common.logger import get_logger

logger = get_logger("common.sweeper")


def run_once() -> int:
    """
    Sweep undecided jobs past the response window. Returns the number rejected.

    Safe to run concurrently with itself and with an incoming Apply callback:
    the underlying UPDATE re-checks user_decision IS NULL, so a job is only
    ever claimed once and a late Apply is never overwritten.
    """
    logger.info("=== Sweep started (window=%dh) ===", SWEEP_AGE_HOURS)
    rejected = expire_stale_jobs(SWEEP_AGE_HOURS)
    if rejected:
        logger.info("=== Sweep finished | %d job(s) auto-rejected ===", rejected)
    else:
        logger.info("=== Sweep finished | nothing to reject ===")
    return rejected


def _sweep_loop():
    """Blocking loop that runs run_once every SWEEP_INTERVAL_SECONDS."""
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Unhandled error in sweeper loop")

        logger.info("Sleeping %d seconds until next sweep...", SWEEP_INTERVAL_SECONDS)
        threading.Event().wait(SWEEP_INTERVAL_SECONDS)


def start_sweeper():
    """Start the sweep loop in a daemon thread."""
    t = threading.Thread(target=_sweep_loop, daemon=True, name="job-sweeper")
    t.start()
    logger.info("Sweeper thread started (interval=%ds, window=%dh)",
                SWEEP_INTERVAL_SECONDS, SWEEP_AGE_HOURS)
    return t
