import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import requests

from common.constants import INDIA_LOCATION_KEYWORDS
from common.filters import title_matches
from common.logger import get_logger

logger = get_logger("oracle")

# Every Oracle HCM tenant exposes the same unauthenticated CandidateExperience API.
LIST_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DETAIL_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"

HEADERS = {
    "accept": "application/json",
    "user-agent": "Mozilla/5.0",
}

DEFAULT_SITE_NUMBER = "CX_1"

_HOST_PATTERN = re.compile(r"https?://([A-Za-z0-9.\-]+)")
_SITE_IN_QUERY = re.compile(r"siteNumber=([A-Za-z0-9_]+)")
_SITE_IN_PATH = re.compile(r"/sites/([A-Za-z0-9_]+)")

# Keywords tried when probing for the India facet. The API caps the facet list
# at 25 entries, so on boards dominated by other countries India only surfaces
# once the result set has been narrowed.
_FACET_PROBE_KEYWORDS = [None, "software engineer"]


def parse_source_url(raw: str):
    """
    Extract (host, site_number) from whatever is stored in company_info.ats_link.

    Tolerates a bare host, a candidate-experience page URL, a full pre-built API
    URL, and a URL still wrapped in a `curl --location '...'` snippet — the
    company sheet contains all four shapes.
    """
    text = unquote(raw or "")

    host_match = _HOST_PATTERN.search(text)
    if not host_match:
        raise ValueError(f"No host found in Oracle URL: {raw!r}")

    site_match = _SITE_IN_QUERY.search(text) or _SITE_IN_PATH.search(text)
    site_number = site_match.group(1) if site_match else DEFAULT_SITE_NUMBER

    return host_match.group(1), site_number


def _parse_date(value):
    """Parse the list response's 'YYYY-MM-DD' PostedDate into a date."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_timestamp(value):
    """Parse the detail response's ISO-8601 ExternalPostedStartDate."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class OracleScraper:
    """
    Scraper for Oracle Recruiting Cloud (Oracle HCM CandidateExperience).

    The list response carries a real posting date and a structured country code,
    so recency and location are settled before any detail page is fetched.
    Results come back newest-first, which lets pagination stop at the 24h cutoff
    instead of walking the whole board.
    """

    PAGE_SIZE = 25
    MAX_PAGES = 20

    def __init__(self, base_url, company_name="unknown", search_text=None):
        self.company_name = company_name
        self.search_text = search_text
        self.host, self.site_number = parse_source_url(base_url)

        self.list_url = f"https://{self.host}{LIST_PATH}"
        self.detail_url = f"https://{self.host}{DETAIL_PATH}"
        self.site_url = (
            f"https://{self.host}/hcmUI/CandidateExperience/en/sites/{self.site_number}"
        )

        logger.debug(
            "OracleScraper initialized for %s | host=%s site=%s",
            company_name,
            self.host,
            self.site_number,
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _finder(self, **params) -> str:
        """Build the ORC finder string: findReqs;siteNumber=CX_1,limit=25,..."""
        parts = [f"siteNumber={self.site_number}"]
        parts += [f"{k}={v}" for k, v in params.items() if v is not None]
        return "findReqs;" + ",".join(parts)

    def _get(self, url, finder, expand=None):
        params = {"onlyData": "true", "finder": finder}
        if expand:
            params["expand"] = expand

        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        except requests.RequestException:
            logger.exception("Network error calling %s for %s", url, self.company_name)
            return None

        if r.status_code != 200:
            logger.warning(
                "Non-200 response (%s) from %s for %s",
                r.status_code,
                url,
                self.company_name,
            )
            return None

        try:
            return r.json()
        except ValueError:
            logger.warning("Non-JSON body from %s for %s", url, self.company_name)
            return None

    # ------------------------------------------------------------------
    # Location handling
    # ------------------------------------------------------------------

    def _resolve_india_facet(self):
        """
        Look up this tenant's geography id for India.

        Geography ids differ per tenant, so they cannot be hard-coded. Returns
        None when India is not in the facet list, in which case the caller falls
        back to filtering on the country code client-side.
        """
        for keyword in _FACET_PROBE_KEYWORDS:
            data = self._get(
                self.list_url,
                self._finder(facetsList="LOCATIONS", limit=1, keyword=keyword),
            )
            if not data:
                continue

            items = data.get("items") or []
            if not items:
                continue

            for facet in items[0].get("locationsFacet") or []:
                if (facet.get("Name") or "").strip().lower() == "india":
                    logger.debug(
                        "%s | resolved India facet id=%s (keyword=%r)",
                        self.company_name,
                        facet.get("Id"),
                        keyword,
                    )
                    return facet.get("Id")

        logger.info(
            "%s | India facet not found — falling back to client-side country filter",
            self.company_name,
        )
        return None

    @staticmethod
    def _is_india_job(job) -> bool:
        if (job.get("PrimaryLocationCountry") or "").upper() == "IN":
            return True
        for location in job.get("secondaryLocations") or []:
            if (location.get("CountryCode") or "").upper() == "IN":
                return True
        primary = (job.get("PrimaryLocation") or "").lower()
        return any(kw in primary for kw in INDIA_LOCATION_KEYWORDS)

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_recent(self, cutoff):
        """
        Walk the board newest-first, stopping as soon as postings predate the
        cutoff date. PostedDate is day-granular, so this is a coarse gate — the
        exact timestamp on the detail record makes the final call.
        """
        india_facet_id = self._resolve_india_facet()
        cutoff_date = cutoff.date()

        rows = []
        for page in range(self.MAX_PAGES):
            data = self._get(
                self.list_url,
                self._finder(
                    limit=self.PAGE_SIZE,
                    offset=page * self.PAGE_SIZE,
                    sortBy="POSTING_DATES_DESC",
                    selectedLocationsFacet=india_facet_id,
                ),
                expand="requisitionList.secondaryLocations",
            )
            if not data:
                break

            items = data.get("items") or []
            if not items:
                break

            postings = items[0].get("requisitionList") or []
            if not postings:
                break

            reached_cutoff = False
            for job in postings:
                posted = _parse_date(job.get("PostedDate"))
                if posted and posted < cutoff_date:
                    reached_cutoff = True
                    break
                rows.append(job)

            logger.debug(
                "%s | page %d -> %d rows kept (running total %d)",
                self.company_name,
                page,
                len(postings),
                len(rows),
            )

            if reached_cutoff or len(postings) < self.PAGE_SIZE:
                break

            time.sleep(0.3)
        else:
            logger.warning(
                "%s | hit max page cap (%d), stopping pagination",
                self.company_name,
                self.MAX_PAGES,
            )

        return rows

    def _fetch_detail(self, job_id):
        finder = f'ById;Id="{job_id}",siteNumber={self.site_number}'
        data = self._get(self.detail_url, finder, expand="all")
        if not data:
            return None
        items = data.get("items") or []
        return items[0] if items else None

    @staticmethod
    def _build_description(detail) -> str:
        """
        Join the public description blocks. Returned as raw HTML — the analyzer
        strips tags itself, same as the Greenhouse borg.

        Some tenants (Honeywell, for one) leave the external blocks empty and
        only fill in the summary, so fall back to that rather than handing the
        analyzer an empty string and scoring the job at zero.
        """
        blocks = [
            detail.get("ExternalDescriptionStr"),
            detail.get("ExternalResponsibilitiesStr"),
            detail.get("ExternalQualificationsStr"),
        ]
        description = "\n".join(block for block in blocks if block)
        return description or (detail.get("ShortDescriptionStr") or "")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self):
        """
        Returns a list of dicts with keys:
        company, title, job_id, location, posted, description, application_link
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        candidates = self._fetch_recent(cutoff)
        logger.debug("%s | %d postings within date cutoff", self.company_name, len(candidates))

        candidates = [job for job in candidates if self._is_india_job(job)]
        logger.debug("%s | after India filter: %d", self.company_name, len(candidates))

        candidates = [job for job in candidates if title_matches(job.get("Title", ""))]
        logger.info("%s | %d jobs after all filters", self.company_name, len(candidates))

        results = []
        for job in candidates:
            job_id = job.get("Id")
            if not job_id:
                continue

            detail = self._fetch_detail(job_id)
            if not detail:
                continue

            # The list only exposes a date; this is the real 24h check.
            posted_at = _parse_timestamp(detail.get("ExternalPostedStartDate"))
            if posted_at and posted_at < cutoff:
                logger.debug(
                    "%s | job %s posted %s — older than 24h, skipping",
                    self.company_name,
                    job_id,
                    posted_at.isoformat(),
                )
                continue

            results.append(
                {
                    "company": self.company_name,
                    "title": detail.get("Title", ""),
                    "job_id": str(job_id),
                    "location": detail.get("PrimaryLocation", "Unknown"),
                    "posted": detail.get("ExternalPostedStartDate", "Unknown"),
                    "description": self._build_description(detail),
                    "application_link": f"{self.site_url}/job/{job_id}",
                }
            )
            time.sleep(0.3)

        logger.info("%s | %d jobs with details fetched", self.company_name, len(results))
        return results
