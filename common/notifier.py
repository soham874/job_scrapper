import os
from typing import List, Dict

import requests

from common.logger import get_logger

logger = get_logger("common.notifier")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

_TELEGRAM_MAX_LENGTH = 4096


def _is_configured() -> bool:
    """Return True if Telegram credentials are present."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False
    return True


def send_telegram_message(text: str) -> bool:
    """
    Send a single message via the Telegram Bot API.
    Returns True on success, False on failure.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.ok:
            logger.info("Telegram message sent (%d chars)", len(text))
            return True
        logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
        return False
    except Exception:
        logger.exception("Failed to send Telegram message")
        return False


def _format_job(job: Dict[str, str]) -> str:
    """Format a single job entry as an HTML block."""
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    location = job.get("location", "Unknown")
    keywords = job.get("keywords", [])
    link = job.get("application_link", "")

    lines = [
        f"🏢 <b>{company}</b>",
        f"📌 {title}",
        f"📍 {location}",
        f"🏷 {', '.join(keywords) if keywords else 'none'}",
    ]
    if link:
        lines.append(f'🔗 <a href="{link}">Apply</a>')
    return "\n".join(lines)


def notify_new_jobs(borg_name: str, jobs: List[Dict[str, str]]) -> None:
    """
    Send a batch summary of newly found jobs to Telegram.
    Skips silently if there are no jobs or Telegram is not configured.
    Splits into multiple messages if the content exceeds 4096 chars.
    """
    if not jobs:
        return

    if not _is_configured():
        return

    header = f"🔎 <b>{borg_name.capitalize()} run: {len(jobs)} new job{'s' if len(jobs) != 1 else ''}</b>\n"

    messages: List[str] = []
    current = header

    for job in jobs:
        entry = "\n" + _format_job(job) + "\n"
        if len(current) + len(entry) > _TELEGRAM_MAX_LENGTH:
            messages.append(current)
            current = header + entry
        else:
            current += entry

    if current.strip():
        messages.append(current)

    for msg in messages:
        send_telegram_message(msg)
