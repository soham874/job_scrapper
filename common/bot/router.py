"""Telegram update router — parses an update and picks the handler.

app.py owns HTTP; this module owns the grammar. Two kinds of update matter:

  * callback_query — a button tap, carrying "<verb>[:<job_id>[:<value>]]"
  * message        — a command, a deep link, or a reply to a force-reply prompt

Every update is checked against TELEGRAM_CHAT_ID before it is acted on. The
webhook is a public endpoint by necessity, and each of these verbs writes to
the database.
"""

from typing import Optional

from common.bot import tracker
from common.bot.deeplinks import parse_start_payload
from common.bot.handlers import handle_apply
from common.logger import get_logger
from common.notifications.formatter import parse_poc_prompt
from common.notifications.telegram import (
    TELEGRAM_CHAT_ID,
    answer_callback_query,
    send_message,
)

logger = get_logger("bot.router")

_OK = {"ok": True}

COMMANDS = [
    {"command": "active", "description": "Open applications"},
    {"command": "today", "description": "What needs chasing now"},
    {"command": "stats", "description": "Counts by status"},
    {"command": "help", "description": "How to use this bot"},
]

_HELP = (
    "🤖 <b>Job tracker</b>\n\n"
    "/active — everything still in play\n"
    "/today — follow-ups due and applications gone quiet\n"
    "/stats — counts by status\n\n"
    "Tap an application to change its status, set a reminder, record a contact, "
    "or re-cut the resume.\n\n"
    "LinkedIn profile URL - https://www.linkedin.com/in/soham-choudhury-bwn/\n\n"
)


def _nav(callback_id: str, message_id: int, job_id: int) -> dict:
    return tracker.render_card(job_id, message_id, callback_id)


# verb -> (screen handler, setter handler). The setter is None for verbs that
# take no value; a value arriving for one of those is treated as unknown.
_ROUTES = {
    "nav": (_nav, None),
    "st": (tracker.show_status_picker, tracker.set_status),
    "rem": (tracker.show_reminder_picker, tracker.set_reminder),
    "tsk": (tracker.show_task_picker, tracker.set_task),
    "poc": (tracker.prompt_poc, None),
    "res": (tracker.resend_resume, None),
}


def is_authorized(update: dict) -> bool:
    """True if the update came from the configured chat.

    Telegram will happily deliver anything posted to the webhook URL, and the
    URL is guessable. Without this check a stranger could drive every button in
    the tracker.
    """
    if not TELEGRAM_CHAT_ID:
        # Unconfigured means the bot cannot reply to anyone anyway.
        return False
    chat = (
        update.get("message", {}).get("chat")
        or update.get("callback_query", {}).get("message", {}).get("chat")
        or {}
    )
    chat_id = chat.get("id")
    if chat_id is None:
        return False
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        logger.warning("Rejected update from unauthorised chat %s", chat_id)
        return False
    return True


def handle_update(update: dict) -> dict:
    """Entry point for a parsed Telegram update."""
    if not is_authorized(update):
        return _OK
    if update.get("callback_query"):
        return _handle_callback(update["callback_query"])
    if update.get("message"):
        return _handle_message(update["message"])
    return _OK


def _handle_callback(callback_query: dict) -> dict:
    callback_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    message_id = callback_query.get("message", {}).get("message_id")

    if not data or not message_id:
        answer_callback_query(callback_id, "Invalid request")
        return _OK

    parts = data.split(":")
    verb = parts[0]

    # The page indicator in the list pager is a button because Telegram has no
    # other way to render a label in a keyboard row.
    if verb == "nop":
        answer_callback_query(callback_id)
        return _OK

    # Apply predates the tracker and keeps its own handler: it is the one verb
    # that creates the application row the rest of these operate on.
    if verb == "apply":
        job_id = _int_or_none(parts[1] if len(parts) > 1 else "")
        if job_id is None:
            answer_callback_query(callback_id, "Invalid job ID")
            return _OK
        return handle_apply(callback_id, message_id, job_id)

    if verb == "lst":
        page = _int_or_none(parts[1] if len(parts) > 1 else "0") or 0
        return tracker.show_list(page, message_id, callback_id)

    route = _ROUTES.get(verb)
    if not route:
        answer_callback_query(callback_id, "Unknown action")
        return _OK

    job_id = _int_or_none(parts[1] if len(parts) > 1 else "")
    if job_id is None:
        answer_callback_query(callback_id, "Invalid job ID")
        return _OK

    screen, setter = route
    if len(parts) > 2:
        if not setter:
            answer_callback_query(callback_id, "Unknown action")
            return _OK
        return setter(callback_id, message_id, job_id, parts[2])
    return screen(callback_id, message_id, job_id)


def _handle_message(message: dict) -> dict:
    text = (message.get("text") or "").strip()
    if not text:
        return _OK

    # A reply to the contact prompt carries the job id in the quoted message,
    # so the association lives in the update rather than in bot state.
    # A command is never a contact name — someone answering the prompt with
    # /stats meant to run /stats, not to record it as their recruiter.
    replied = message.get("reply_to_message") or {}
    if replied and not text.startswith("/"):
        job_id = parse_poc_prompt(replied.get("text") or "")
        if job_id is not None:
            return tracker.save_poc(job_id, text)

    if not text.startswith("/"):
        return _OK

    # Telegram appends @botname to commands in groups.
    command = text.split()[0].split("@")[0].lower()

    if command == "/start":
        job_id = parse_start_payload(text)
        if job_id is not None:
            return tracker.render_card(job_id)
        send_message(_HELP)
        return _OK
    if command == "/active":
        return tracker.show_list(0)
    if command == "/today":
        return tracker.show_due()
    if command == "/stats":
        return tracker.show_stats()
    if command == "/help":
        send_message(_HELP)
        return _OK

    send_message("Unknown command. Try /help.")
    return _OK


def _int_or_none(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
