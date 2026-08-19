import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import requests

from common.constants import INDIA_LOCATION_KEYWORDS
from common.filters import title_matches
from common.logger import get_logger

logger = get_logger("ashby")

# Every Ashby tenant exposes the same unauthenticated posting API.
BASE_API = "https://api.ashbyhq.com/posting-api/job-board"

HEADERS = {
    "accept": "application/json",
    "user-agent": "Mozilla/5.0",
}

# A bare board name, as opposed to a URL to pick one out of.
_BARE_SLUG = re.compile(r"^[A-Za-z0-9_~-]+$")

# Ashby reports FullTime, PartTime, Intern, Contract or Temporary. Only
# full-time is wanted, but the field is left empty on some boards, so a
# missing value is kept rather than silently dropping the whole board.
_ACCEPTED_EMPLOYMENT_TYPES = {"", "fulltime"}


def extract_board_slug(raw: str) -> str:
    """
    Pull the board name out of whatever is stored in company_info.ats_link.

    Tolerates a bare board name, the public board URL, a link to a single
    posting, and a pre-built posting-API URL — the company sheet holds all
    four shapes.
    """
    text = unquote((raw or "").strip())
    if not text:
        raise ValueError("Empty Ashby URL")

    if _BARE_SLUG.match(text):
        return text

    parsed = urlparse(text if "://" in text else f"https://{text}")
    parts = [p for p in parsed.path.strip("/").split("/") if p]

    # https://api.ashbyhq.com/posting-api/job-board/<slug>
    if "job-board" in parts:
        parts = parts[parts.index("job-board") + 1:]

    # https://jobs.ashbyhq.com/<slug>[/<posting id>]
    if not parts:
        raise ValueError(f"No board slug found in Ashby URL: {raw!r}")

    return parts[0]


def _parse_timestamp(value):
    """Parse Ashby's ISO-8601 publishedAt into an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class AshbyScraper:
    """
    Scraper for Ashby job boards (api.ashbyhq.com/posting-api).

    One request returns the whole board with every description inline, so
    unlike the Greenhouse and Oracle borgs there is no per-job detail fetch —
    filtering is pure local work on a single response. Boards run to a few
    hundred postings and the payload is served gzipped, so even the largest
    are a fraction of a second.

    publishedAt is when a posting went live and is never bumped by an edit,
    which makes the 24h window a true "new posting" gate rather than a
    "touched recently" one.
    """

    def __init__(self, ashby_url: str, company_name: str = "unknown"):
        self.company_name = company_name
        self.slug = extract_board_slug(ashby_url)
        self.jobs_url = f"{BASE_API}/{self.slug}"
        logger.debug(
            "AshbyScraper initialized for %s | slug=%s",
            company_name,
            self.slug,
        )

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_board(self):
        """Return the board's job list, or None if the board is unreachable."""
        try:
            r = requests.get(self.jobs_url, headers=HEADERS, timeout=60)
        except requests.RequestException:
            logger.exception("Network error fetching board for %s", self.company_name)
            return None

        if r.status_code == 404:
            logger.warning(
                "Board not found for %s (slug=%s)", self.company_name, self.slug
            )
            return None

        if r.status_code != 200:
            logger.warning(
                "Non-200 response (%s) from %s for %s",
                r.status_code,
                self.jobs_url,
                self.company_name,
            )
            return None

        try:
            return r.json().get("jobs") or []
        except ValueError:
            logger.warning("Non-JSON body from %s for %s", self.jobs_url, self.company_name)
            return None

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    @staticmethod
    def _is_listed(job) -> bool:
        """Skip postings the board is holding back. Absent means listed."""
        return job.get("isListed", True) is not False

    @staticmethod
    def _is_full_time(job) -> bool:
        employment_type = (job.get("employmentType") or "").strip().lower()
        return employment_type in _ACCEPTED_EMPLOYMENT_TYPES

    @staticmethod
    def _location_strings(job) -> list:
        """
        Every location string Ashby exposes for a posting.

        A posting carries a primary location, an optional structured postal
        address, and any number of secondary locations. A role open in both
        Bengaluru and Berlin only names India in one of them, so all of them
        are checked.
        """
        values = []
        for entry in [job] + list(job.get("secondaryLocations") or []):
            values.append(entry.get("location") or "")
            postal = ((entry.get("address") or {}).get("postalAddress")) or {}
            values.extend([
                postal.get("addressCountry") or "",
                postal.get("addressRegion") or "",
                postal.get("addressLocality") or "",
            ])
        return [v for v in values if v]

    @classmethod
    def _is_india_job(cls, job) -> bool:
        blob = " ".join(cls._location_strings(job)).lower()
        return any(kw in blob for kw in INDIA_LOCATION_KEYWORDS)

    @staticmethod
    def _build_description(job) -> str:
        """
        Prefer the plain-text description Ashby ships alongside the HTML one.

        The analyzer strips tags either way, but job_description is stored for
        the resume module, which is better served plain text than markup.
        """
        return job.get("descriptionPlain") or job.get("descriptionHtml") or ""

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self):
        """
        Returns a list of dicts with keys:
        company, title, job_id, location, posted, description, application_link
        """
        jobs = self._fetch_board()
        if jobs is None:
            return []

        logger.debug("%s | total jobs from API: %d", self.company_name, len(jobs))

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        jobs = [j for j in jobs if self._is_listed(j) and self._is_full_time(j)]
        logger.debug("%s | after listing/employment filter: %d", self.company_name, len(jobs))

        recent = []
        for job in jobs:
            published_at = _parse_timestamp(job.get("publishedAt"))
            if published_at and published_at >= cutoff:
                recent.append(job)
        jobs = recent
        logger.debug("%s | after 24h filter: %d", self.company_name, len(jobs))

        jobs = [j for j in jobs if title_matches(j.get("title", ""))]
        logger.debug("%s | after title filter: %d", self.company_name, len(jobs))

        jobs = [j for j in jobs if self._is_india_job(j)]
        logger.info("%s | %d jobs after all filters", self.company_name, len(jobs))

        results = []
        for job in jobs:
            job_id = job.get("id")
            if not job_id:
                continue

            results.append(
                {
                    "company": self.company_name,
                    "title": job.get("title", ""),
                    "job_id": str(job_id),
                    "location": job.get("location") or "Unknown",
                    "posted": job.get("publishedAt", "Unknown"),
                    "description": self._build_description(job),
                    # jobUrl is the posting itself, which is what the Telegram
                    # message links to; applyUrl jumps straight to the form.
                    "application_link": job.get("jobUrl") or job.get("applyUrl") or "",
                }
            )

        logger.info("%s | %d jobs collected", self.company_name, len(results))
        return results
