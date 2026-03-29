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


def send_message(text: str, reply_markup: Optional[dict] = None) -> Optional[int]:
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


def edit_message(message_id: int, text: str) -> bool:
    """
    Edit an existing Telegram message (remove buttons, update text).
    Returns True on success, False on failure.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
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


def answer_callback_query(callback_query_id: str, text: str = "") -> bool:
    """Answer a callback query to dismiss the loading indicator on the button."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        resp = requests.post(url, json=payload, timeout=15)
        return resp.ok
    except Exception:
        logger.exception("Failed to answer callback query")
        return False
