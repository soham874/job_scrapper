"""Turns the curl pasted into the company sheet into a replayable request.

The one rule here is that nothing is cleaned up. Two of the first endpoints
this borg was built against fail *silently* when the request is tidied:

  * careers.klm.com resets the connection outright when the sec-ch-ua and
    sec-fetch headers are missing — no response at all, not a 403.
  * jobs.apple.com answers 200 with an empty result set when a single key is
    dropped from the POST body, which is indistinguishable from "no jobs".

So headers pass through untouched, and the body is re-serialised with every key
intact. The only thing the scraper is allowed to change is the one pagination
field the spec names.
"""

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, Optional

from common.logger import get_logger

logger = get_logger("self_json")


class CurlError(Exception):
    """Raised when a curl command cannot be parsed into a request."""


# Value-less flags that say nothing about the request we replay. requests
# follows redirects and handles gzip on its own, so -L and --compressed are
# already the behaviour we get.
_SKIP = {
    "curl", "-L", "--location", "--compressed", "-s", "--silent", "-S",
    "--show-error", "-i", "--include", "-v", "--verbose", "-k", "--insecure",
    "-g", "--globoff", "--http1.1", "--http2", "-f", "--fail", "-N",
    "--no-buffer", "-#", "--progress-bar",
}

# Flags whose value we read and then discard.
_SKIP_WITH_VALUE = {
    "-o", "--output", "-x", "--proxy", "--connect-timeout", "-m", "--max-time",
    "--retry", "--max-redirs", "-w", "--write-out",
}

_DATA_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii"}

# curl allows the command to be split over lines with a trailing backslash;
# devtools always produces it that way.
_CONTINUATION = re.compile(r"\\\s*\n")


@dataclass
class CurlRequest:
    """A parsed curl, ready to hand to requests.

    url keeps its query string exactly as pasted — it is never split into
    params and rebuilt, because re-encoding is itself a change. Only
    query-string pagination touches it, and only the one named key.
    """

    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    json_body: Optional[dict] = None
    data: Optional[str] = None

    @property
    def has_body(self) -> bool:
        return self.json_body is not None or self.data is not None


def _split_header(raw: str):
    """Split 'Name: value' as curl does — on the first colon only."""
    name, sep, value = raw.partition(":")
    if not sep:
        raise CurlError(f"Header is missing a colon: {raw!r}")
    return name.strip(), value.strip()


def parse_curl(text: str) -> CurlRequest:
    """Parse a curl command line into a CurlRequest.

    Raises CurlError on anything unparseable — a company with a broken curl is
    reported by name and skipped, never scraped with a guessed request.
    """
    if not (text or "").strip():
        raise CurlError("Curl command is empty")

    try:
        tokens = shlex.split(_CONTINUATION.sub(" ", text.strip()))
    except ValueError as exc:
        raise CurlError(f"Could not tokenise the curl command: {exc}") from exc

    url = None
    method = None
    headers: Dict[str, str] = {}
    data_parts = []
    cookies = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1

        if token in _SKIP:
            continue

        if token in _SKIP_WITH_VALUE:
            index += 1
            continue

        if token in ("-X", "--request"):
            method = tokens[index].upper()
            index += 1
            continue

        if token in ("-H", "--header"):
            name, value = _split_header(tokens[index])
            index += 1
            headers[name] = value
            continue

        if token in ("-A", "--user-agent"):
            headers["user-agent"] = tokens[index]
            index += 1
            continue

        if token in ("-e", "--referer"):
            # KLM checks sec-fetch-site/origin against the referer, so this is
            # part of the request rather than a courtesy.
            headers["referer"] = tokens[index]
            index += 1
            continue

        if token in ("-b", "--cookie"):
            cookies.append(tokens[index])
            index += 1
            continue

        if token in _DATA_FLAGS:
            data_parts.append(tokens[index])
            index += 1
            continue

        if token == "--json":
            data_parts.append(tokens[index])
            index += 1
            headers.setdefault("content-type", "application/json")
            headers.setdefault("accept", "application/json")
            continue

        if token == "--url":
            url = tokens[index]
            index += 1
            continue

        if token.startswith("-"):
            logger.debug("Ignoring unrecognised curl flag %r", token)
            continue

        # The first bare token is the URL.
        if url is None:
            url = token

    if not url:
        raise CurlError("No URL found in the curl command")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    if cookies:
        # Multiple -b flags concatenate, same as curl.
        headers["cookie"] = "; ".join(cookies)

    # curl joins repeated -d values with '&'.
    raw_body = "&".join(data_parts) if data_parts else None

    json_body = None
    data = None
    if raw_body is not None:
        try:
            parsed = json.loads(raw_body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            # Held as a dict so pagination can replace one key and leave every
            # other key to re-serialise exactly as it arrived.
            json_body = parsed
        else:
            data = raw_body

    if method is None:
        method = "POST" if raw_body is not None else "GET"

    if any(name.lower() in ("authorization", "cookie") for name in headers):
        logger.warning(
            "Curl carries an authorization or cookie header — these endpoints are "
            "meant to be public, and a session header will expire silently"
        )

    return CurlRequest(
        method=method, url=url, headers=headers, json_body=json_body, data=data
    )
