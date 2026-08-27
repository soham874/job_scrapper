"""Scrapes an arbitrary JSON job API described by a curl and a Job Spec.

Unlike the vendor borgs, this one is handed the whole request rather than
building it, and the request is replayed as closely as HTTP allows. The only
part that changes between pages is the single pagination field the spec names;
everything else — headers, body keys, their order — goes out as pasted.
"""

import copy
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from common.filters import title_matches
from common.logger import get_logger
from borgs.self_json.curl import parse_curl
from borgs.self_json.spec import (
    SpecError,
    parse_posted,
    parse_spec,
    resolve_field,
    resolve_path,
    resolve_scalar,
)

logger = get_logger("self_json")

REQUEST_TIMEOUT = 30

# Third-party endpoints reset connections under light load — KLM drops the
# stream outright when it dislikes a request. One retry, and only for faults
# that a retry can actually fix.
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _with_query(url: str, key: str, value: Any) -> str:
    """Replace one query parameter, leaving the rest of the URL alone."""
    parsed = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != key]
    pairs.append((key, str(value)))
    return urlunparse(parsed._replace(query=urlencode(pairs)))


def _set_nested(body: dict, dotted: str, value: Any) -> None:
    """Set a (possibly nested) key in the request body in place."""
    parts = dotted.split(".")
    current = body
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            raise SpecError(
                f"pagination field {dotted!r} does not exist in the request body"
            )
        current = nxt
    current[parts[-1]] = value


class SelfJsonScraper:
    """Scraper driven entirely by a pasted curl and a Job Spec.

    raw_count is the number of jobs the API returned before any filtering. A
    parseable config that returns zero is the signature of a stale curl, and is
    otherwise indistinguishable from a quiet day, so the cron layer reports it.
    responses_ok separates the two ways that happens — a request that never
    landed, versus one that came back 200 and empty.
    """

    def __init__(self, curl_text: str, spec_text: str, company_name: str = "unknown"):
        self.company_name = company_name
        self.request = parse_curl(curl_text)
        self.spec = parse_spec(spec_text)
        self.raw_count = 0
        self.responses_ok = 0

        pagination = self.spec.pagination
        if pagination.type != "none" and pagination.target == "json" and self.request.json_body is None:
            raise SpecError(
                "pagination.in is 'json' but the curl sends no JSON body — "
                "use \"in\": \"query\" for a GET endpoint"
            )

        self.session = requests.Session()
        logger.debug(
            "SelfJsonScraper initialized for %s | %s %s | pagination=%s",
            company_name, self.request.method, self.request.url, pagination.type,
        )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _send(self, method: str, url: str, json_body=None, data=None) -> Optional[Any]:
        """Send one request, retrying once on a fault a retry can fix."""
        for attempt in (1, 2):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self.request.headers,
                    json=json_body,
                    data=data,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException:
                if attempt == 1:
                    logger.warning(
                        "%s | network error calling %s — retrying once",
                        self.company_name, url,
                    )
                    time.sleep(1)
                    continue
                logger.exception("%s | network error calling %s", self.company_name, url)
                return None

            if response.status_code in RETRY_STATUSES and attempt == 1:
                logger.warning(
                    "%s | %s from %s — retrying once",
                    self.company_name, response.status_code, url,
                )
                time.sleep(1)
                continue

            if response.status_code != 200:
                logger.warning(
                    "%s | non-200 response (%s) from %s",
                    self.company_name, response.status_code, url,
                )
                return None

            try:
                payload = response.json()
            except ValueError:
                logger.warning("%s | non-JSON body from %s", self.company_name, url)
                return None

            self.responses_ok += 1
            return payload

        return None

    def _fetch_page(self, cursor: Optional[int]) -> Optional[Any]:
        """Fetch one page, mutating only the declared pagination field."""
        url = self.request.url
        json_body = copy.deepcopy(self.request.json_body)
        pagination = self.spec.pagination

        if cursor is not None:
            field_name = (
                pagination.page_field if pagination.type == "page"
                else pagination.offset_field
            )
            updates = [(field_name, cursor)]
            if pagination.limit_field and pagination.page_size:
                updates.append((pagination.limit_field, pagination.page_size))

            for name, value in updates:
                if pagination.target == "query":
                    url = _with_query(url, name, value)
                else:
                    _set_nested(json_body, name, value)

        return self._send(self.request.method, url, json_body, self.request.data)

    def _extract(self, payload: Any) -> List[dict]:
        """Pull the job list out of a response payload."""
        jobs = resolve_path(payload, self.spec.jobs_path)
        if jobs is None:
            logger.warning(
                "%s | jobs_path %r matched nothing in the response",
                self.company_name, self.spec.jobs_path,
            )
            return []
        if not isinstance(jobs, list):
            logger.warning(
                "%s | jobs_path %r resolved to %s, expected a list",
                self.company_name, self.spec.jobs_path, type(jobs).__name__,
            )
            return []
        return [job for job in jobs if isinstance(job, dict)]

    def _fetch_all(self, cutoff: datetime) -> List[dict]:
        """Walk every page, or just the one if pagination is off."""
        pagination = self.spec.pagination
        if pagination.type == "none":
            payload = self._fetch_page(None)
            return self._extract(payload) if payload is not None else []

        collected: List[dict] = []
        seen_ids = set()
        first_size: Optional[int] = None
        cursor = pagination.start

        for page in range(pagination.max_pages):
            payload = self._fetch_page(cursor)
            if payload is None:
                break

            batch = self._extract(payload)
            if not batch:
                break

            # Not every API honours the page parameter. Emirates' board
            # returns its whole result set and ignores paging entirely, so
            # without this the same jobs would be re-fetched max_pages times.
            fresh = [
                job for job in batch
                if resolve_scalar(job, self.spec.fields["job_id"]) not in seen_ids
            ]
            seen_ids.update(
                resolve_scalar(job, self.spec.fields["job_id"]) for job in batch
            )
            if not fresh:
                logger.warning(
                    "%s | page %d repeated the previous page — the endpoint looks "
                    "like it ignores %r; set pagination.type to 'none'",
                    self.company_name, page + 1, pagination.page_field
                    or pagination.offset_field,
                )
                break

            collected.extend(fresh)
            logger.debug(
                "%s | page %d (cursor=%s) returned %d jobs (%d new)",
                self.company_name, page + 1, cursor, len(batch), len(fresh),
            )

            # A page shorter than the first is the last one. Apple's
            # totalRecords would be the obvious stop, but it reports 0 on the
            # overrun page, so page length is the only trustworthy signal.
            if first_size is None:
                first_size = len(batch)
            elif len(batch) < first_size:
                break

            if pagination.sorted_by_posted_desc and self._all_older_than(batch, cutoff):
                logger.debug(
                    "%s | whole page older than the cutoff — stopping early",
                    self.company_name,
                )
                break

            cursor += pagination.page_size if pagination.type == "offset" else 1
            time.sleep(pagination.delay_seconds)

        return collected

    def _all_older_than(self, batch: List[dict], cutoff: datetime) -> bool:
        for job in batch:
            posted = parse_posted(
                resolve_scalar(job, self.spec.fields["posted"]), self.spec.posted_format
            )
            if posted is None or posted >= cutoff:
                return False
        return True

    # ------------------------------------------------------------------
    # Field extraction
    # ------------------------------------------------------------------

    def _values_for(self, job: dict) -> Dict[str, str]:
        """Resolve every mapping in fields, so link_template can use any of them."""
        values = {}
        for name, mapping in self.spec.fields.items():
            joiner = "\n\n" if name == "description" else ", " if name == "location" else " "
            values[name] = resolve_field(job, mapping, joiner)
        return values

    def _apply_detail(self, values: Dict[str, str]) -> None:
        """Overlay fields fetched from the per-job detail endpoint."""
        detail = self.spec.detail
        try:
            url = detail.url_template.format(**values)
        except KeyError as exc:
            logger.warning(
                "%s | detail.url_template references unknown field %s",
                self.company_name, exc,
            )
            return

        payload = self._send("GET", url)
        time.sleep(detail.delay_seconds)
        if payload is None:
            return

        for name, mapping in detail.fields.items():
            joiner = "\n\n" if name == "description" else ", " if name == "location" else " "
            resolved = resolve_field(payload, mapping, joiner)
            if resolved:
                values[name] = resolved

    def _location_ok(self, blob: str) -> bool:
        keywords = self.spec.location_keywords()
        if keywords is None:
            return True
        lowered = blob.lower()
        return any(keyword in lowered for keyword in keywords)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> List[dict]:
        """
        Returns a list of dicts with keys:
        company, title, job_id, location, posted, description, application_link
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        jobs = self._fetch_all(cutoff)
        self.raw_count = len(jobs)
        logger.debug("%s | total jobs from API: %d", self.company_name, self.raw_count)

        posted_path = self.spec.fields["posted"]
        recent = []
        undated = 0
        for job in jobs:
            posted = parse_posted(
                resolve_scalar(job, posted_path), self.spec.posted_format
            )
            if posted is None:
                undated += 1
                continue
            if posted >= cutoff:
                recent.append(job)
        if undated:
            logger.info(
                "%s | %d job(s) dropped — posted date at %r could not be read as %s",
                self.company_name, undated, posted_path, self.spec.posted_format,
            )
        logger.debug("%s | after 24h filter: %d", self.company_name, len(recent))

        titled = [
            job for job in recent
            if title_matches(resolve_field(job, self.spec.fields["title"], " "))
        ]
        logger.debug("%s | after title filter: %d", self.company_name, len(titled))

        located = [
            job for job in titled
            if self._location_ok(resolve_field(job, self.spec.fields["location"], ", "))
        ]
        logger.info("%s | %d jobs after all filters", self.company_name, len(located))

        results = []
        for job in located:
            job_id = resolve_scalar(job, self.spec.fields["job_id"])
            if not job_id:
                logger.warning(
                    "%s | job dropped — job_id path %r resolved to nothing or a list; "
                    "it is the dedupe key and must be a single scalar",
                    self.company_name, self.spec.fields["job_id"],
                )
                continue

            values = self._values_for(job)
            if self.spec.detail:
                self._apply_detail(values)

            if self.spec.link_template:
                application_link = self.spec.link_template.format(**values)
            else:
                application_link = values.get("application_link", "")

            results.append({
                "company": self.company_name,
                "title": values.get("title", ""),
                "job_id": job_id,
                "location": values.get("location") or "Unknown",
                "posted": resolve_scalar(job, posted_path) or "Unknown",
                "description": values.get("description", ""),
                "application_link": application_link,
            })

        logger.info("%s | %d jobs collected", self.company_name, len(results))
        return results
