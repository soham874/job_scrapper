"""Run-slot scheduling shared by every borg cron loop.

The borgs are independent processes, so nothing coordinates them at runtime.
Left to themselves they all scrape on startup and then every
CRON_INTERVAL_SECONDS from there — four providers hit at once, a wall of
Telegram messages, then a long silence. Instead each borg owns a slot inside
the interval, derived from its position in BORG_ORDER, and sleeps until the
next wall-clock time that lands on its slot.

Anchoring the slots to wall-clock time rather than to process start is what
keeps the stagger honest: a run that takes ten minutes, or a borg that is
restarted mid-cycle, still comes back to the same slot instead of dragging
the whole schedule along behind it.
"""

import hashlib
import threading
import time
from datetime import datetime, timedelta

from common.config import BORG_ORDER, BORG_STAGGER_WINDOW_SECONDS, CRON_INTERVAL_SECONDS
from common.logger import get_logger

logger = get_logger("scheduling")


def stagger_offset_seconds(borg_name: str) -> float:
    """Seconds past the top of each interval at which this borg runs.

    Slots are spread evenly across the stagger window, which is capped at the
    cron interval — a window longer than the interval would wrap borgs back
    onto each other's slots.
    """
    window = min(BORG_STAGGER_WINDOW_SECONDS, CRON_INTERVAL_SECONDS)
    if borg_name in BORG_ORDER:
        return window * BORG_ORDER.index(borg_name) / len(BORG_ORDER)

    # A borg nobody registered still deserves a slot of its own rather than
    # piling onto the first one, so derive a stable pseudo-slot from the name.
    logger.warning("Borg %r is not in BORG_ORDER — falling back to a hashed slot. "
                   "Add it to common.config.BORG_ORDER for an evenly spaced one.",
                   borg_name)
    digest = hashlib.md5(borg_name.encode()).hexdigest()
    return int(digest, 16) % window


def seconds_until_next_run(borg_name: str, now: float = None) -> float:
    """Seconds to wait before this borg's next slot arrives."""
    now = time.time() if now is None else now
    elapsed = (now - stagger_offset_seconds(borg_name)) % CRON_INTERVAL_SECONDS
    return CRON_INTERVAL_SECONDS - elapsed


def wait_for_next_run(borg_name: str, borg_logger) -> None:
    """Block until this borg's next slot. Logs the wait so the offset is
    visible in the borg's own log rather than only here."""
    delay = seconds_until_next_run(borg_name)
    borg_logger.info("Sleeping %.0f seconds until next run (slot +%.0fs into a %ds interval)...",
                     delay, stagger_offset_seconds(borg_name), CRON_INTERVAL_SECONDS)
    threading.Event().wait(delay)


def seconds_until_daily_slot(hour: int, minute: int = 0, now: datetime = None) -> float:
    """Seconds to wait for the next local-time occurrence of hour:minute.

    Used by the loops that mean "once a day" rather than "every 86400 seconds".
    The difference shows up across restarts: an interval loop re-anchors itself
    to whenever the process last came up, so a bot restarted each afternoon
    would run a nightly pass at a different time every day — or, if it is
    restarted more often than the interval, never. Aiming at a wall-clock time
    keeps the pass where it was put.

    Returns a full day when the slot has just gone by, never 0, so a loop that
    calls this immediately after running cannot spin.
    """
    now = datetime.now() if now is None else now
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot <= now:
        slot += timedelta(days=1)
    return (slot - now).total_seconds()
