import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from common.logger import get_logger
from common.constants import TITLE_INCLUDE_KEYWORDS, TITLE_EXCLUDE_KEYWORDS, INDIA_LOCATION_KEYWORDS

logger = get_logger("greenhouse")

BASE_API = "https://boards-api.greenhouse.io/v1/boards"


def _extract_board_slug(url: str) -> str:
    """
    Extracts the board slug from a greenhouse URL.
    e.g. https://boards-api.greenhouse.io/v1/boards/Agoda/jobs -> Agoda
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    # Expected path: /v1/boards/<slug>/jobs
    try:
        idx = parts.index("boards")
        return parts[idx + 1]
    except (ValueError, IndexError):
        # Fallback: last meaningful segment
        return parts[-1] if parts else url


class GreenhouseScraper:

    def __init__(self, greenhouse_url: str, company_name: str = "unknown"):
        self.company_name = company_name
        self.slug = _extract_board_slug(greenhouse_url)
        self.jobs_url = f"{BASE_API}/{self.slug}/jobs"
        logger.debug(
            "GreenhouseScraper initialized for %s | slug=%s",
            company_name,
            self.slug,
        )

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _board_exists(self):
        try:
            r = requests.get(self.jobs_url, timeout=30)
        except requests.RequestException:
            logger.exception("Network error checking board for %s", self.company_name)
            return None

        if r.status_code == 404:
            logger.warning("Board not found for %s (slug=%s)", self.company_name, self.slug)
            return None

        r.raise_for_status()
        return r.json()

    @staticmethod
    def _filter_last_24_hours(jobs):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        filtered = []
        for job in jobs:
            updated_at = job.get("updated_at")
            if not updated_at:
                continue
            try:
                job_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if job_time >= cutoff:
                    filtered.append(job)
            except Exception:
                continue
        return filtered

    @staticmethod
    def _filter_by_title(jobs):
        filtered = []
        for job in jobs:
            title_lower = job.get("title", "").lower()
            if any(kw in title_lower for kw in TITLE_EXCLUDE_KEYWORDS):
                continue
            if not any(kw in title_lower for kw in TITLE_INCLUDE_KEYWORDS):
                continue
            filtered.append(job)
        return filtered

    @staticmethod
    def _is_india_location(location_str):
        if not location_str:
            return False
        location_str = location_str.lower()
        return any(kw in location_str for kw in INDIA_LOCATION_KEYWORDS)

    @classmethod
    def _filter_india_jobs(cls, jobs):
        filtered = []
        for job in jobs:
            loc = job.get("location", {}).get("name")
            if cls._is_india_location(loc):
                filtered.append(job)
                continue
            offices = job.get("offices", [])
            if any(cls._is_india_location(o.get("location", {}).get("name")) for o in offices):
                filtered.append(job)
        return filtered

    # ------------------------------------------------------------------
    # Detail fetch
    # ------------------------------------------------------------------

    def _fetch_job_detail(self, job_id):
        url = f"{BASE_API}/{self.slug}/jobs/{job_id}"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            logger.exception("Failed to fetch detail for job %s at %s", job_id, self.company_name)
            return None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self):
        """
        Returns a list of dicts with keys:
        company, title, job_id, location, updated_at, description
        """
        data = self._board_exists()
        if data is None:
            return []

        jobs = data.get("jobs", [])
        logger.debug("%s | total jobs from API: %d", self.company_name, len(jobs))

        jobs = self._filter_last_24_hours(jobs)
        logger.debug("%s | after 24h filter: %d", self.company_name, len(jobs))

        jobs = self._filter_by_title(jobs)
        logger.debug("%s | after title filter: %d", self.company_name, len(jobs))

        jobs = self._filter_india_jobs(jobs)
        logger.info("%s | %d jobs after all filters", self.company_name, len(jobs))

        results = []
        for job in jobs:
            job_id = job["id"]
            detail = self._fetch_job_detail(job_id)
            if not detail:
                continue

            results.append(
                {
                    "company": self.company_name,
                    "title": detail.get("title", ""),
                    "job_id": str(job_id),
                    "requisition_id": detail.get("requisition_id", str(job_id)),
                    "location": detail.get("location", {}).get("name", "Unknown"),
                    "posted": detail.get("updated_at", "Unknown"),
                    "description": detail.get("content", ""),
                    "absolute_url": detail.get("absolute_url", ""),
                }
            )

        logger.info("%s | %d jobs with details fetched", self.company_name, len(results))
        return results
