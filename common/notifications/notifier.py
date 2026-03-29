"""Job notification orchestrator — the single entry-point borgs call to announce new jobs.

Delegates formatting to notifications.formatter and transport to notifications.telegram.
"""

from typing import List, Dict

from common.logger import get_logger
from common.notifications.formatter import format_job_message, make_inline_keyboard
from common.notifications.telegram import is_configured, send_message

logger = get_logger("notifications.notifier")


def notify_new_jobs(borg_name: str, jobs: List[Dict[str, str]]) -> None:
    """
    Send one Telegram message per job with inline Apply/Reject buttons.
    Skips silently if there are no jobs or Telegram is not configured.
    Each job dict must include a 'job_id' key (the DB row id).
    """
    if not jobs:
        return

    if not is_configured():
        return

    total = len(jobs)
    for i, job in enumerate(jobs, start=1):
        text = format_job_message(job, index=i, total=total, borg_name=borg_name)
        keyboard = make_inline_keyboard(job["job_id"])
        send_message(text, reply_markup=keyboard)
