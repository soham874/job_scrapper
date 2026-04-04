"""Telegram message formatting and inline-keyboard builders.

Pure functions — no I/O, no DB calls, no Telegram API calls.
"""

from typing import Dict, List, Optional


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


def format_decided_message(job: dict, decision: str,
                           referral_search_url: Optional[str] = None) -> str:
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
    if decision == "applied" and referral_search_url:
        lines.append(f'🔍 <a href="{referral_search_url}">Find referrers at {company} on LinkedIn</a>')
    return "\n".join(lines)


def make_inline_keyboard(job_id: int) -> dict:
    """Build an InlineKeyboardMarkup with Apply/Reject buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Apply", "callback_data": f"apply:{job_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{job_id}"},
            ]
        ]
    }
