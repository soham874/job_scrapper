import os
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

import requests

from common.logger import get_logger

logger = get_logger("common.notifier")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _is_configured() -> bool:
    """Return True if Telegram credentials are present."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False
    return True


def send_telegram_message(text: str, reply_markup: Optional[dict] = None) -> Optional[int]:
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


def edit_telegram_message(message_id: int, text: str) -> bool:
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


def format_job_message(job: Dict[str, str], index: int = 0, total: int = 0,
                       borg_name: str = "") -> str:
    """Format a single job as an HTML message body."""
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    location = job.get("location", "Unknown")
    keywords = job.get("keywords", [])
    link = job.get("application_link", "")

    header = ""
    if borg_name and total:
        header = f"🔎 <b>{borg_name.capitalize()}</b> | {index} of {total}\n\n"

    lines = [
        f"🏢 <b>{company}</b>",
        f"📌 {title}",
        f"📍 {location}",
        f"🏷 {', '.join(keywords) if keywords else 'none'}",
    ]
    if link:
        lines.append(f'🔗 <a href="{link}">Apply</a>')
    return header + "\n".join(lines)


def format_decided_message(job: dict, decision: str) -> str:
    """Format a job message after the user has made a decision."""
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    location = job.get("location", "Unknown")
    link = job.get("application_link", "")

    if decision == "applied":
        status = "✅ APPLIED"
    else:
        status = "❌ REJECTED"

    lines = [
        f"<b>{status}</b>\n",
        f"🏢 <b>{company}</b>",
        f"📌 {title}",
        f"📍 {location}",
    ]
    if decision == "applied" and link:
        lines.append(f'🔗 <a href="{link}">Apply</a>')
    return "\n".join(lines)


def _make_inline_keyboard(job_id: int) -> dict:
    """Build an InlineKeyboardMarkup with Apply/Reject buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Apply", "callback_data": f"apply:{job_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{job_id}"},
            ]
        ]
    }


def notify_new_jobs(borg_name: str, jobs: List[Dict[str, str]]) -> None:
    """
    Send one Telegram message per job with inline Apply/Reject buttons.
    Skips silently if there are no jobs or Telegram is not configured.
    Each job dict must include a 'job_id' key (the DB row id).
    """
    if not jobs:
        return

    if not _is_configured():
        return

    total = len(jobs)
    for i, job in enumerate(jobs, start=1):
        text = format_job_message(job, index=i, total=total, borg_name=borg_name)
        keyboard = _make_inline_keyboard(job["job_id"])
        send_telegram_message(text, reply_markup=keyboard)


_TEMPLATE_DIR = Path(__file__).resolve().parent
_LINKEDIN_MSG_TEMPLATE = _TEMPLATE_DIR / "linkedin_message_template.txt"


def build_linkedin_search_url(company_name: str) -> str:
    """Return a LinkedIn people-search URL for software engineers at the given company in Bangalore."""
    encoded = quote(f"software engineer {company_name} bangalore")
    return (
        f"https://www.linkedin.com/search/results/people/"
        f"?keywords={encoded}&origin=FACETED_SEARCH&pastCompany=%5B%229390173%22%5D"
    )


def format_referral_messages(title: str, company: str, job_id: str) -> List[str]:
    """
    Load the LinkedIn message template, replace placeholders, and split on '---'
    dividers.  Returns a list of message parts ready to be sent as separate
    Telegram messages.
    """
    try:
        raw = _LINKEDIN_MSG_TEMPLATE.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to read LinkedIn message template")
        return []

    raw = raw.replace("<<TITLE>>", title)
    raw = raw.replace("<<Company>>", company)
    raw = raw.replace("<<JOB-ID>>", job_id)

    parts = [part.strip() for part in raw.split("---") if part.strip()]
    return parts


def send_applied_response(job: dict) -> None:
    """
    After the user clicks Apply, send:
    1. LinkedIn people-search hyperlink
    2. Referral message parts (split by '---' from the template)
    """
    if not _is_configured():
        return

    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    ats_job_id = job.get("ats_job_id", "")

    # 1 — LinkedIn search link
    search_url = build_linkedin_search_url(company)
    link_msg = f'🔍 <a href="{search_url}">Find referrers at {company} on LinkedIn</a>'
    send_telegram_message(link_msg)

    # 2 — Referral message parts
    parts = format_referral_messages(title, company, ats_job_id)
    for part in parts:
        send_telegram_message(part)
