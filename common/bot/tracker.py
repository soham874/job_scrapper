"""Application tracker — the menu that runs after a job has been applied to.

handlers.py covers the single moment of deciding to apply. Everything here is
what happens over the following weeks: moving an application through its
statuses, setting a follow-up, recording who you spoke to, re-cutting the
resume.

Every screen edits the *same* Telegram message rather than posting a new one,
so an application occupies one card in the chat no matter how often it is
touched. Nothing is held between taps — see notifications.formatter for the
callback grammar and why it is stateless.
"""

from datetime import date, timedelta
from math import ceil
from typing import Optional

from common.applications import (
    ACTIVE_STATUSES,
    REMINDER_PRESETS,
    STALE_AFTER_DAYS,
    TASK_PRESETS,
    status_label,
)
from common.bot.handlers import deliver_resume_bundle
from common.db.repository import (
    count_active_applications,
    get_active_applications,
    get_application_by_job_id,
    get_due_applications,
    get_status_counts,
    set_next_action,
    set_poc,
    update_application_status,
)
from common.logger import get_logger
from common.notifications.formatter import (
    format_application_list,
    format_due_message,
    format_job_card,
    format_poc_prompt,
    format_stats,
    make_force_reply,
    make_job_card_keyboard,
    make_list_keyboard,
    make_reminder_keyboard,
    make_status_keyboard,
    make_task_keyboard,
    status_from_code,
)
from common.notifications.telegram import (
    answer_callback_query,
    edit_message,
    send_message,
)

logger = get_logger("bot.tracker")

# Rows per page in the /active list. Eight keeps the keyboard inside one
# screenful on a phone, which is where this is read.
PAGE_SIZE = 8

# Default follow-up horizon when a task is chosen before a date is.
_DEFAULT_REMINDER_DAYS = 7

_OK = {"ok": True}


def _deliver(message_id: Optional[int], text: str, markup: Optional[dict]) -> None:
    """Edit the message in place, or post a new one when there is none.

    Commands and reminders arrive without a card to rewrite; button taps always
    have one. Same rendering either way.
    """
    if message_id:
        edit_message(message_id, text, reply_markup=markup)
    else:
        send_message(text, reply_markup=markup)


def _load(job_id: int, callback_id: str = "") -> Optional[dict]:
    """Fetch an application, answering the callback if it is missing."""
    app = get_application_by_job_id(job_id)
    if not app and callback_id:
        answer_callback_query(callback_id, "No application found for that job", show_alert=True)
    return app


def render_card(job_id: int, message_id: Optional[int] = None,
                callback_id: str = "") -> dict:
    """Draw the job card — the tracker's home screen."""
    app = _load(job_id, callback_id)
    if not app:
        if not callback_id:
            send_message("That job has no application recorded against it.")
        return _OK
    if callback_id:
        answer_callback_query(callback_id)
    _deliver(message_id, format_job_card(app), make_job_card_keyboard(job_id))
    return _OK


def show_status_picker(callback_id: str, message_id: int, job_id: int) -> dict:
    app = _load(job_id, callback_id)
    if not app:
        return _OK
    answer_callback_query(callback_id)
    _deliver(message_id, format_job_card(app),
             make_status_keyboard(job_id, app.get("status") or ""))
    return _OK


def set_status(callback_id: str, message_id: int, job_id: int, code: str) -> dict:
    status = status_from_code(code)
    if not status:
        answer_callback_query(callback_id, "Unknown status", show_alert=True)
        return _OK
    if not update_application_status(job_id, status):
        answer_callback_query(callback_id, "No application found for that job", show_alert=True)
        return _OK
    answer_callback_query(callback_id, status_label(status))
    logger.info("Job %d moved to status '%s'", job_id, status)
    return render_card(job_id, message_id)


def show_reminder_picker(callback_id: str, message_id: int, job_id: int) -> dict:
    app = _load(job_id, callback_id)
    if not app:
        return _OK
    answer_callback_query(callback_id)
    _deliver(message_id, format_job_card(app), make_reminder_keyboard(job_id))
    return _OK


def set_reminder(callback_id: str, message_id: int, job_id: int, code: str) -> dict:
    app = _load(job_id, callback_id)
    if not app:
        return _OK

    if code == "clr":
        ok = set_next_action(job_id, None, None)
        note = "Reminder cleared"
    else:
        preset = REMINDER_PRESETS.get(code)
        if not preset:
            answer_callback_query(callback_id, "Unknown reminder", show_alert=True)
            return _OK
        label, days = preset
        when = date.today() + timedelta(days=days)
        # Keep whatever the reminder was already for; a bare date with no task
        # is a nudge with nothing to act on.
        task = app.get("next_important_task") or TASK_PRESETS["fu"]
        ok = set_next_action(job_id, when, task)
        note = f"Reminder set {label}"

    if not ok:
        answer_callback_query(callback_id, "Could not save the reminder", show_alert=True)
        return _OK
    answer_callback_query(callback_id, note)
    return render_card(job_id, message_id)


def show_task_picker(callback_id: str, message_id: int, job_id: int) -> dict:
    app = _load(job_id, callback_id)
    if not app:
        return _OK
    answer_callback_query(callback_id)
    _deliver(message_id, format_job_card(app), make_task_keyboard(job_id))
    return _OK


def set_task(callback_id: str, message_id: int, job_id: int, code: str) -> dict:
    task = TASK_PRESETS.get(code)
    if not task:
        answer_callback_query(callback_id, "Unknown task", show_alert=True)
        return _OK
    app = _load(job_id, callback_id)
    if not app:
        return _OK
    # Choosing what to chase implies chasing it — give the task a date if the
    # user has not picked one, otherwise it would never surface.
    when = app.get("next_important_date") or date.today() + timedelta(days=_DEFAULT_REMINDER_DAYS)
    if not set_next_action(job_id, when, task):
        answer_callback_query(callback_id, "Could not save the task", show_alert=True)
        return _OK
    answer_callback_query(callback_id, task)
    return render_card(job_id, message_id)


def prompt_poc(callback_id: str, message_id: int, job_id: int) -> dict:
    """Ask for a contact name with a force-reply.

    A new message is sent rather than editing the card, because the reply has
    to quote something — and quoting the card would lose its buttons.
    """
    app = _load(job_id, callback_id)
    if not app:
        return _OK
    answer_callback_query(callback_id)
    send_message(format_poc_prompt(app), reply_markup=make_force_reply("Recruiter or referrer"))
    return _OK


def save_poc(job_id: int, value: str) -> dict:
    """Store the answer to a POC prompt and re-post the card."""
    value = (value or "").strip()
    if not value:
        return _OK
    if not set_poc(job_id, value):
        send_message("That job has no application recorded against it.")
        return _OK
    logger.info("Job %d contact recorded", job_id)
    return render_card(job_id)


def resend_resume(callback_id: str, message_id: int, job_id: int) -> dict:
    """Re-cut the tailored resume and referral text for an application."""
    app = _load(job_id, callback_id)
    if not app:
        return _OK
    answer_callback_query(callback_id, "Generating resume…")
    # The resume service keys off `id`; the application dict calls it job_id.
    deliver_resume_bundle(dict(app, id=job_id), message_id)
    return _OK


def show_list(page: int = 0, message_id: Optional[int] = None,
              callback_id: str = "") -> dict:
    """Paged list of everything still in play."""
    total = count_active_applications(ACTIVE_STATUSES)
    total_pages = max(1, ceil(total / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    apps = get_active_applications(ACTIVE_STATUSES, PAGE_SIZE, page * PAGE_SIZE)

    if callback_id:
        answer_callback_query(callback_id)
    markup = make_list_keyboard(apps, page, total_pages)
    _deliver(message_id, format_application_list(apps, page, total, PAGE_SIZE),
             markup if markup["inline_keyboard"] else None)
    return _OK


def due_reason(app: dict) -> str:
    """'due' when a follow-up date has arrived, otherwise 'stale'."""
    next_date = app.get("next_important_date")
    return "due" if next_date and next_date <= date.today() else "stale"


def show_due(message_id: Optional[int] = None) -> dict:
    """Everything wanting attention right now, as one message."""
    apps = get_due_applications(ACTIVE_STATUSES, STALE_AFTER_DAYS)
    if not apps:
        _deliver(message_id, "✅ <b>Nothing needs chasing today.</b>", None)
        return _OK
    markup = make_list_keyboard(apps[:PAGE_SIZE], 0, 1)
    header = f"⏰ <b>{len(apps)} application(s) need attention</b>\n"
    body = format_application_list(apps[:PAGE_SIZE], 0, len(apps), PAGE_SIZE)
    _deliver(message_id, f"{header}\n{body}",
             markup if markup["inline_keyboard"] else None)
    return _OK


def show_stats(message_id: Optional[int] = None) -> dict:
    _deliver(message_id, format_stats(get_status_counts()), None)
    return _OK


def notify_due(app: dict) -> None:
    """Push a single nudge, with the card's menu attached. Used by the reminder loop."""
    send_message(format_due_message(app, due_reason(app)),
                 reply_markup=make_job_card_keyboard(app["job_id"]))
