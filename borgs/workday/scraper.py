import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from common.logger import get_logger
from common.constants import INDIA_LOCATION_KEYWORDS, SEARCH_TEXT

logger = get_logger("workday")

LANG_PATTERN = re.compile(r"^[a-z]{2}-[A-Z]{2}$")

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0",
}


def clean_path_segments(path):
    parts = path.strip("/").split("/")
    return [p for p in parts if not LANG_PATTERN.match(p)]


class WorkdayScraper:

    FACETS = {
        "locationCountry": ["c4f78be1a8f14da0ab49ce1162348a5e"],
        "locationRegionStateProvince": ["701eb5584934425d930bc84b9e8b04eb"],
    }

    MAX_PAGES = 20  # Cap pagination to avoid crawling thousands of pages

    def __init__(self, base_url, company_name="unknown", search_text=None, facets=None, limit=20):
        self.company_name = company_name
        self.limit = limit

        self.payload = {"limit": limit, "offset": 0}

        if search_text:
            self.payload["searchText"] = search_text

        if facets is not None:
            self.payload["appliedFacets"] = facets

        parsed = urlparse(base_url)
        host = parsed.netloc
        tenant = host.split(".")[0]

        path_parts = clean_path_segments(parsed.path)
        if len(path_parts) == 0:
            raise ValueError(f"Invalid Workday URL: {base_url}")

        org = path_parts[0]
        base = f"https://{host}"

        self.base_url = base
        self.detail_url = f"{base}/wday/cxs/{tenant}/{org}"
        self.list_url = f"{self.detail_url}/jobs"

        logger.debug(
            "WorkdayScraper initialized for %s | list_url=%s",
            company_name,
            self.list_url,
        )

    # ------------------------------------------------------------------
    # Location & recency helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_india_location(loc_text):
        if not loc_text:
            return False
        loc_text = loc_text.lower()
        return any(kw in loc_text for kw in INDIA_LOCATION_KEYWORDS)

    @staticmethod
    def is_within_24h(posted_text):
        if not posted_text:
            return False
        posted_text = posted_text.lower()
        if "today" in posted_text:
            return True
        if "yesterday" in posted_text:
            return True
        m = re.search(r"(\d+)", posted_text)
        if m:
            days = int(m.group(1))
            return days <= 1
        return False

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def fetch_jobs(self):
        jobs = []
        seen = set()
        page = 0
        payload = dict(self.payload)  # local copy so the instance stays clean

        while page < self.MAX_PAGES:
            try:
                r = requests.post(self.list_url, headers=HEADERS, json=payload, timeout=30)
            except requests.RequestException:
                logger.exception("Network error fetching jobs for %s", self.company_name)
                break

            if r.status_code != 200:
                logger.warning(
                    "Non-200 response (%s) for %s at offset %d",
                    r.status_code,
                    self.company_name,
                    payload["offset"],
                )
                break

            data = r.json()
            postings = data.get("jobPostings", [])

            if not postings:
                break

            new_jobs = 0
            for job in postings:
                key = job.get("externalPath")
                posted = job.get("postedOn")
                loc_text = job.get("locationsText")

                if not (self.is_within_24h(posted) and self.is_india_location(loc_text)):
                    continue

                if key and key not in seen:
                    seen.add(key)
                    jobs.append(job)
                    new_jobs += 1

            logger.debug(
                "%s | page %d offset %d -> %d new jobs",
                self.company_name,
                page,
                payload["offset"],
                new_jobs,
            )

            payload["offset"] += self.limit
            page += 1

            if len(postings) < self.limit:
                break

            time.sleep(0.4)

        if page >= self.MAX_PAGES:
            logger.warning("%s | hit max page cap (%d), stopping pagination", self.company_name, self.MAX_PAGES)

        return jobs

    def fetch_job_detail(self, external_path):
        url = f"{self.detail_url}/{external_path}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException:
            logger.exception("Network error fetching detail %s", external_path)
            return None

        if r.status_code != 200:
            logger.warning("Failed detail fetch (%s): %s", r.status_code, external_path)
            return None

        return r.json()

    @staticmethod
    def clean_html(html):
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n").strip()

    # ------------------------------------------------------------------
    # Main entry point — returns structured results
    # ------------------------------------------------------------------

    def run(self):
        """
        Returns a list of dicts with keys:
        title, job_id, location, posted, description, company
        """
        jobs = self.fetch_jobs()
        logger.info("%s | %d candidate jobs found", self.company_name, len(jobs))

        results = []
        for job in jobs:
            path = job.get("externalPath")
            if not path:
                continue

            detail = self.fetch_job_detail(path)
            if not detail:
                continue

            info = detail.get("jobPostingInfo", {})
            results.append(
                {
                    "company": self.company_name,
                    "title": info.get("title", ""),
                    "job_id": info.get("jobReqId", ""),
                    "location": info.get("location", ""),
                    "posted": info.get("postedOn", ""),
                    "description": self.clean_html(info.get("jobDescription", "")),
                }
            )
            time.sleep(0.3)

        logger.info("%s | %d jobs with details fetched", self.company_name, len(results))
        return results
