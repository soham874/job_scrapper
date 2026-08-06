"""Shared job-title filtering.

Matches on word boundaries rather than raw substrings, so entries in
TITLE_EXCLUDE_KEYWORDS only knock out whole words: "intern" no longer
kills "International", "ui" no longer kills "Build".
"""

import re

from common.constants import TITLE_INCLUDE_KEYWORDS, TITLE_EXCLUDE_KEYWORDS


def _compile(keyword: str):
    """Wrap a keyword in \\b anchors, but only on sides that end in a word char.

    Keywords like "jr." end in punctuation, where a trailing \\b would demand a
    following word character and never match.
    """
    left = r"\b" if keyword[:1].isalnum() else ""
    right = r"\b" if keyword[-1:].isalnum() else ""
    return re.compile(left + re.escape(keyword) + right, re.IGNORECASE)


_INCLUDE_PATTERNS = [_compile(kw) for kw in TITLE_INCLUDE_KEYWORDS if kw]
_EXCLUDE_PATTERNS = [_compile(kw) for kw in TITLE_EXCLUDE_KEYWORDS if kw]


def title_matches(title: str) -> bool:
    """True if the title hits an include keyword and no exclude keyword."""
    if not title:
        return False
    if any(p.search(title) for p in _EXCLUDE_PATTERNS):
        return False
    return any(p.search(title) for p in _INCLUDE_PATTERNS)
