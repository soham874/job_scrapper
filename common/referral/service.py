"""LinkedIn referral helpers — URL building, template formatting, and response dispatch.

Combines template I/O with Telegram transport to send referral messages
after the user accepts a job.
"""

from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from common.logger import get_logger
from common.notifications.telegram import is_configured, send_message

logger = get_logger("referral.service")

_TEMPLATE_PATH = Path(__file__).resolve().parent / "linkedin_message_template.txt"


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
        raw = _TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to read LinkedIn message template")
        return []

    raw = raw.replace("<<TITLE>>", title)
    raw = raw.replace("<<Company>>", company)
    raw = raw.replace("<<JOB-ID>>", job_id)

    parts = [part.strip() for part in raw.split("---") if part.strip()]
    return parts


def send_applied_response(job: dict, reply_to_message_id: Optional[int] = None) -> None:
    """
    After the user clicks Apply, send a single combined message (LinkedIn
    search link + referral template) as a reply to the original job message.
    """
    if not is_configured():
        return

    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    ats_job_id = job.get("ats_job_id", "")

    # Build combined message
    sections = []

    # 1 — LinkedIn search link
    search_url = build_linkedin_search_url(company)
    sections.append(f'🔍 <a href="{search_url}">Find referrers at {company} on LinkedIn</a>')

    # 2 — Referral message parts
    parts = format_referral_messages(title, company, ats_job_id)
    sections.extend(parts)

    combined = "\n\n".join(sections)
    send_message(combined, reply_to_message_id=reply_to_message_id)
