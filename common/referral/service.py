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

# LinkedIn's internal id for the user's own organization. Used as pastCompany
# so referral searches surface alumni of that org now working elsewhere.
_OWN_ORG_LINKEDIN_ID = "9390173"


def _encode_id_list(ids: List[str]) -> str:
    """Encode ids as LinkedIn's %5B%22id1%22%2C%22id2%22%5D array-facet format."""
    quoted = "%22%2C%22".join(quote(i) for i in ids)
    return f"%5B%22{quoted}%22%5D"


def build_linkedin_search_url(company_name: str, company_linkedin_ids: Optional[str] = None) -> str:
    """
    Return a LinkedIn people-search URL for software engineers at the given
    company in Bangalore who previously worked at the user's own organization.

    company_linkedin_ids (from company_info.linkedin_company_ids) is an
    optional comma-separated string — a company can exist as multiple
    distinct entities on LinkedIn (e.g. "Walmart Global Tech" vs "Walmart
    Global Tech India"), so all ids are OR'd together via currentCompany. If
    it's None/empty, the search falls back to the keyword-only form.
    """
    encoded = quote(f"software engineer {company_name}")
    url = (
        f"https://www.linkedin.com/search/results/people/"
        f"?keywords={encoded}&origin=FACETED_SEARCH"
        f"&pastCompany={_encode_id_list([_OWN_ORG_LINKEDIN_ID])}"
    )
    ids = [i.strip() for i in (company_linkedin_ids or "").split(",") if i.strip()]
    if ids:
        url += f"&currentCompany={_encode_id_list(ids)}"
    return url


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
    After the user clicks Apply, send the referral template as a single
    message in reply to the original job message.
    """
    if not is_configured():
        return

    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    ats_job_id = job.get("ats_job_id", "")

    # Referral message parts
    parts = format_referral_messages(title, company, ats_job_id)
    if not parts:
        return

    combined = "\n\n".join(parts)
    send_message(combined, reply_to_message_id=reply_to_message_id)
