"""The per-company Job Spec: what to pull out of an arbitrary JSON response.

Every borg before this one knew the shape of its ATS. This one knows nothing,
so the sheet has to say everything: where the job array lives, which path holds
each field, how the posting date is encoded, how to page, and where the apply
link comes from.

Nothing is inferred. A spec that does not name a required path is rejected by
name and the company is skipped — the alternative is a scrape that quietly
returns nothing, which is exactly what a stale curl also looks like.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

from common.constants import INDIA_LOCATION_KEYWORDS
from common.logger import get_logger

logger = get_logger("self_json")


class SpecError(Exception):
    """Raised when a Job Spec is missing or malformed. Names the offending key."""


# Date encodings we accept. Anything starting with '%' is handed to strptime.
#
# "none" is for boards that publish no date at all — Juspay's is one. There the
# 24h gate has nothing to filter on, so every job reaches the title filter on
# every run and the (company_id, ats_job_id) dedupe in insert_job is what makes
# each one notify exactly once, the first time it is seen. That is still the
# signal worth having, but it must be asked for explicitly: turning it on by
# accident would mean a board's entire back catalogue arrives as new.
_NAMED_FORMATS = {"iso8601", "epoch_seconds", "epoch_millis", "relative_text", "none"}

_REQUIRED_FIELDS = ("job_id", "title", "location", "posted")

# With posted_format "none" there is no date to map.
_REQUIRED_FIELDS_UNDATED = tuple(f for f in _REQUIRED_FIELDS if f != "posted")

_PAGINATION_TYPES = {"none", "offset", "page"}
_PAGINATION_TARGETS = {"json", "query"}

# Placeholders in link_template, e.g. "https://x.com/{job_id}/{slug}".
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

# Workday-style "Posted 3 Days Ago".
_RELATIVE_DAYS = re.compile(r"(\d+)\s*day", re.IGNORECASE)
_RELATIVE_HOURS = re.compile(r"(\d+)\s*hour", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _step(current: Any, key: str) -> Any:
    """Take one segment: a dict key, or a numeric index into a list."""
    if isinstance(current, dict):
        return current.get(key)
    if isinstance(current, list):
        try:
            return current[int(key)]
        except (ValueError, IndexError):
            return None
    return None


def _resolve(current: Any, segments: List[str]) -> Any:
    for position, segment in enumerate(segments):
        if current is None:
            return None

        if segment.endswith("[]"):
            key = segment[:-2]
            if key:
                current = _step(current, key)
            if not isinstance(current, list):
                return None
            remainder = segments[position + 1:]
            if not remainder:
                return current
            # Map the rest of the path over every element. Nested [] therefore
            # produces a nested list, which flatten() later collapses.
            return [_resolve(item, remainder) for item in current]

        current = _step(current, segment)

    return current


def resolve_path(obj: Any, path: str) -> Any:
    """Resolve a dotted path.

    Grammar, deliberately tiny — no wildcards, no filters, no fallbacks:
        a.b.c   dict keys
        a.0.b   list index
        a[].b   map the remaining segments over a list
        ""      the object itself

    KLM's Elasticsearch envelope needs 'hits.hits[]._source' just to reach the
    job array, and its Portable Text descriptions need the nested form
    'description[].children[].text'.
    """
    if path == "":
        return obj
    return _resolve(obj, path.split("."))


def flatten(value: Any) -> List[str]:
    """Collapse a resolved value to a depth-first list of non-empty strings."""
    if value is None or isinstance(value, bool):
        return [] if value is None else [str(value)]
    if isinstance(value, list):
        collected = []
        for item in value:
            collected.extend(flatten(item))
        return collected
    text = str(value).strip()
    return [text] if text else []


def resolve_field(obj: Any, value: Union[str, List[str]], joiner: str) -> str:
    """Resolve a field mapping to a string.

    A list of paths means concatenate them in order — KLM splits one
    description across description/what/profile/offer. It is not a fallback
    chain: a missing part contributes nothing rather than triggering a retry on
    the next path.
    """
    paths = value if isinstance(value, list) else [value]
    pieces: List[str] = []
    for path in paths:
        pieces.extend(flatten(resolve_path(obj, path)))
    return joiner.join(pieces)


def resolve_scalar(obj: Any, path: str) -> Optional[str]:
    """Resolve a path that must land on a single scalar.

    Used for job_id and posted. job_id is the dedupe key, so silently joining a
    list into it would corrupt every future comparison — better to drop the job
    and say so.
    """
    value = resolve_path(obj, path)
    if value is None or isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def parse_posted(value: Any, fmt: str) -> Optional[datetime]:
    """Parse a posting date into an aware UTC datetime, or None if it won't.

    The format is declared in the spec and never sniffed: Apple's
    postDateInGMT is ISO-8601 while KLM's publishedDate is epoch seconds, and
    both are plausible readings of a bare number otherwise.
    """
    if value is None or value == "":
        return None

    try:
        if fmt == "iso8601":
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        elif fmt == "epoch_seconds":
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif fmt == "epoch_millis":
            parsed = datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        elif fmt == "relative_text":
            return _parse_relative(str(value))
        else:
            parsed = datetime.strptime(str(value).strip(), fmt)
    except (ValueError, TypeError, OSError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_relative(text: str) -> Optional[datetime]:
    """Parse 'Posted Today' / 'Yesterday' / 'Posted 3 Days Ago' style values."""
    now = datetime.now(timezone.utc)
    lowered = text.lower()

    if "today" in lowered or "just posted" in lowered:
        return now
    if "yesterday" in lowered:
        return now - timedelta(days=1)

    hours = _RELATIVE_HOURS.search(lowered)
    if hours:
        return now - timedelta(hours=int(hours.group(1)))

    days = _RELATIVE_DAYS.search(lowered)
    if days:
        return now - timedelta(days=int(days.group(1)))

    return None


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------

@dataclass
class Pagination:
    type: str = "none"
    target: str = "json"
    page_field: Optional[str] = None
    offset_field: Optional[str] = None
    limit_field: Optional[str] = None
    page_size: Optional[int] = None
    start: int = 1
    max_pages: int = 10
    delay_seconds: float = 0.4
    sorted_by_posted_desc: bool = False


@dataclass
class Detail:
    url_template: str
    fields: Dict[str, Union[str, List[str]]]
    delay_seconds: float = 0.3


@dataclass
class Spec:
    jobs_path: str
    fields: Dict[str, Union[str, List[str]]]
    posted_format: str
    link_template: Optional[str] = None
    location_filter: Union[str, List[str]] = "india"
    pagination: Pagination = field(default_factory=Pagination)
    detail: Optional[Detail] = None

    def location_keywords(self) -> Optional[List[str]]:
        """Keywords the location blob must hit, or None to skip the check."""
        if self.location_filter == "any":
            return None
        if self.location_filter == "india":
            return list(INDIA_LOCATION_KEYWORDS)
        return [str(k).lower() for k in self.location_filter]


def _require(mapping: dict, key: str, where: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise SpecError(f"{where} is missing required key '{key}'")
    return mapping[key]


def _check_path_value(value: Any, where: str):
    """A field mapping is a path, or a list of paths to concatenate."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return value
    raise SpecError(
        f"{where} must be a path string or a non-empty list of path strings, "
        f"got {value!r}"
    )


def _parse_pagination(raw: Any) -> Pagination:
    if raw is None:
        return Pagination(type="none")
    if not isinstance(raw, dict):
        raise SpecError("'pagination' must be an object")

    kind = raw.get("type", "none")
    if kind not in _PAGINATION_TYPES:
        raise SpecError(
            f"pagination.type must be one of {sorted(_PAGINATION_TYPES)}, got {kind!r}"
        )

    target = raw.get("in", "json")
    if target not in _PAGINATION_TARGETS:
        raise SpecError(
            f"pagination.in must be one of {sorted(_PAGINATION_TARGETS)}, got {target!r}"
        )

    pagination = Pagination(
        type=kind,
        target=target,
        page_field=raw.get("page_field"),
        offset_field=raw.get("offset_field"),
        limit_field=raw.get("limit_field"),
        page_size=raw.get("page_size"),
        start=raw.get("start", 1 if kind == "page" else 0),
        max_pages=int(raw.get("max_pages", 10)),
        delay_seconds=float(raw.get("delay_seconds", 0.4)),
        sorted_by_posted_desc=bool(raw.get("sorted_by_posted_desc", False)),
    )

    if kind == "page" and not pagination.page_field:
        raise SpecError("pagination.type is 'page' but 'page_field' is missing")
    if kind == "offset":
        if not pagination.offset_field:
            raise SpecError("pagination.type is 'offset' but 'offset_field' is missing")
        if not pagination.page_size:
            raise SpecError(
                "pagination.type is 'offset' but 'page_size' is missing — the "
                "offset cannot be advanced without it"
            )
    if pagination.limit_field and not pagination.page_size:
        raise SpecError("pagination.limit_field is set but 'page_size' is missing")
    if pagination.max_pages < 1:
        raise SpecError("pagination.max_pages must be at least 1")

    return pagination


def _parse_detail(raw: Any) -> Optional[Detail]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SpecError("'detail' must be an object")

    url_template = _require(raw, "url_template", "'detail'")
    fields = raw.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise SpecError("'detail.fields' must be a non-empty object")
    for name, value in fields.items():
        _check_path_value(value, f"detail.fields.{name}")

    return Detail(
        url_template=url_template,
        fields=fields,
        delay_seconds=float(raw.get("delay_seconds", 0.3)),
    )


def parse_spec(text: str) -> Spec:
    """Parse and fully validate a Job Spec. Raises SpecError naming what's wrong."""
    if not (text or "").strip():
        raise SpecError("Job Spec is empty")

    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise SpecError(f"Job Spec is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise SpecError("Job Spec must be a JSON object")

    if "jobs_path" not in raw:
        raise SpecError("Job Spec is missing required key 'jobs_path'")
    jobs_path = raw["jobs_path"]
    if not isinstance(jobs_path, str):
        raise SpecError("'jobs_path' must be a string")

    posted_format = _require(raw, "posted_format", "Job Spec")
    if posted_format not in _NAMED_FORMATS and not posted_format.startswith("%"):
        raise SpecError(
            f"posted_format must be one of {sorted(_NAMED_FORMATS)} or a strptime "
            f"format starting with '%', got {posted_format!r}"
        )

    fields = raw.get("fields")
    if not isinstance(fields, dict):
        raise SpecError("Job Spec is missing required key 'fields'")
    required = (
        _REQUIRED_FIELDS_UNDATED if posted_format == "none" else _REQUIRED_FIELDS
    )
    for name in required:
        if name not in fields:
            raise SpecError(f"'fields' is missing required mapping '{name}'")
    for name, value in fields.items():
        _check_path_value(value, f"fields.{name}")

    detail = _parse_detail(raw.get("detail"))

    # The score gate discards anything under DESC_SCORE_THRESHOLD, and an empty
    # description scores 0 — so a spec with no description source would drop
    # every job it ever saw without a single error being logged.
    has_description = "description" in fields or (detail and "description" in detail.fields)
    if not has_description:
        raise SpecError(
            "No description source: set 'fields.description' or a 'detail' block "
            "with a description mapping. Without one every job scores 0 and is "
            "discarded silently."
        )

    link_template = raw.get("link_template")
    has_link_field = "application_link" in fields
    if bool(link_template) == bool(has_link_field):
        raise SpecError(
            "Set exactly one of 'link_template' or 'fields.application_link' — "
            "got both" if link_template else
            "Set exactly one of 'link_template' or 'fields.application_link' — "
            "got neither"
        )
    if link_template:
        unknown = [
            name for name in _PLACEHOLDER.findall(link_template) if name not in fields
        ]
        if unknown:
            raise SpecError(
                f"link_template references {unknown} which are not in 'fields' — "
                "add a mapping for each, they may be extra fields used only for "
                "the URL"
            )

    location_filter = raw.get("location_filter", "india")
    if isinstance(location_filter, str):
        if location_filter not in ("india", "any"):
            raise SpecError(
                "location_filter must be 'india', 'any', or a list of keywords, "
                f"got {location_filter!r}"
            )
    elif isinstance(location_filter, list):
        if not location_filter or not all(isinstance(k, str) for k in location_filter):
            raise SpecError("location_filter list must be non-empty strings")
    else:
        raise SpecError("location_filter must be 'india', 'any', or a list of keywords")

    pagination = _parse_pagination(raw.get("pagination"))
    if posted_format == "none" and pagination.sorted_by_posted_desc:
        raise SpecError(
            "pagination.sorted_by_posted_desc needs dates to compare, but "
            "posted_format is 'none'"
        )

    return Spec(
        jobs_path=jobs_path,
        fields=fields,
        posted_format=posted_format,
        link_template=link_template,
        location_filter=location_filter,
        pagination=pagination,
        detail=detail,
    )
