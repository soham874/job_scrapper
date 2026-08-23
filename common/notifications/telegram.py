"""Low-level Telegram Bot API transport.

All functions in this module talk *only* to the Telegram HTTP API.
No business logic, no message formatting, no domain knowledge.
"""

import os
from typing import Optional

import requests

from common.logger import get_logger

logger = get_logger("notifications.telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def is_configured() -> bool:
    """Return True if Telegram credentials are present."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False
    return True


def send_message(text: str, reply_markup: Optional[dict] = None,
                  reply_to_message_id: Optional[int] = None) -> Optional[int]:
    """
    Send a single message via the Telegram Bot API.
    Returns the message_id on success, None on failure.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.ok:
            message_id = resp.json().get("result", {}).get("message_id")
            logger.info("Telegram message sent (id=%s, %d chars)", message_id, len(text))
            return message_id
        logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
        return None
    except Exception:
        logger.exception("Failed to send Telegram message")
        return None


def send_document(file_path: str, caption: str = "",
                  reply_to_message_id: Optional[int] = None) -> Optional[int]:
    """
    Upload a file via sendDocument (multipart).
    Returns the message_id on success, None on failure.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    data = {"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML"}
    if caption:
        # Telegram rejects captions over 1024 characters.
        data["caption"] = caption[:1024]
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id

    try:
        with open(file_path, "rb") as fh:
            resp = requests.post(url, data=data, files={"document": fh}, timeout=60)
    except FileNotFoundError:
        logger.error("Cannot send document — file not found: %s", file_path)
        return None
    except Exception:
        logger.exception("Failed to send Telegram document %s", file_path)
        return None

    if resp.ok:
        message_id = resp.json().get("result", {}).get("message_id")
        logger.info("Telegram document sent (id=%s, file=%s)", message_id, file_path)
        return message_id
    logger.error("Telegram sendDocument error %d: %s", resp.status_code, resp.text)
    return None


def edit_message(message_id: int, text: str,
                 reply_markup: Optional[dict] = None) -> bool:
    """
    Edit an existing Telegram message (update text, replace or remove buttons).
    Returns True on success, False on failure.

    Omitting reply_markup clears the message's inline keyboard — that is
    Telegram's behaviour for editMessageText, not something added here, and it
    is what the Apply flow relies on to retire its button. Pass a keyboard to
    swap one screen of the menu for another in place.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.ok:
            logger.info("Telegram message %d edited", message_id)
            return True
        logger.error("Telegram editMessage error %d: %s", resp.status_code, resp.text)
        return False
    except Exception:
        logger.exception("Failed to edit Telegram message %d", message_id)
        return False


def answer_callback_query(callback_query_id: str, text: str = "",
                          show_alert: bool = False) -> bool:
    """Answer a callback query to dismiss the loading indicator on the button.

    show_alert swaps the transient toast for a dialog the user has to dismiss —
    worth it for failures, too heavy for the ordinary confirmations.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    try:
        resp = requests.post(url, json=payload, timeout=15)
        return resp.ok
    except Exception:
        logger.exception("Failed to answer callback query")
        return False


def get_me() -> Optional[dict]:
    """Return the bot's own account record, or None if the call fails.

    Used at startup to learn the bot's username, which is what dashboard deep
    links are built from. Fetched rather than configured so there is no second
    place for the bot's identity to drift out of sync with its token.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        resp = requests.get(url, timeout=15)
        if resp.ok:
            return resp.json().get("result")
        logger.error("Telegram getMe error %d: %s", resp.status_code, resp.text)
        return None
    except Exception:
        logger.exception("Failed to call getMe")
        return None


def set_my_commands(commands: list) -> bool:
    """Publish the bot's command list so it appears in Telegram's '/' menu.

    commands is a list of {"command": ..., "description": ...} dicts.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands"
    try:
        resp = requests.post(url, json={"commands": commands}, timeout=15)
        if resp.ok:
            logger.info("Registered %d bot command(s)", len(commands))
            return True
        logger.error("Telegram setMyCommands error %d: %s", resp.status_code, resp.text)
        return False
    except Exception:
        logger.exception("Failed to set bot commands")
        return False
