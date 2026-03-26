import html
import re

from bs4 import BeautifulSoup

from common.constants import (
    DESC_POSITIVE_KEYWORDS,
    DESC_NEGATIVE_KEYWORDS,
    DESC_EXPERIENCE_PATTERNS,
)
from common.logger import get_logger

logger = get_logger("common.analyzer")

_WHITESPACE = re.compile(r"\s+")

# Pre-compile word-boundary patterns for each keyword to avoid substring false positives
# (e.g. "scala" matching inside "scalability", "go" matching inside "google")
_POSITIVE_PATTERNS = {
    kw: re.compile(r"\b" + re.escape(kw) + r"\b")
    for kw in DESC_POSITIVE_KEYWORDS
}
_NEGATIVE_PATTERNS = {
    kw: re.compile(r"\b" + re.escape(kw) + r"\b")
    for kw in DESC_NEGATIVE_KEYWORDS
}


def _strip_html(raw: str) -> str:
    """Remove HTML tags and decode entities, returning plain lowercase text."""
    if not raw:
        return ""
    text = html.unescape(raw)
    soup = BeautifulSoup(text, "html.parser")
    plain = soup.get_text(separator=" ")
    return _WHITESPACE.sub(" ", plain).strip().lower()


def analyze_description(raw_html: str) -> dict:
    """
    Score a job description against the configured keyword lists.

    Returns a dict with:
        score            – int, clamped to 0-100
        positive_matches – list of matched positive keywords
        negative_matches – list of matched negative keywords
        experience_matches – list of matched experience pattern strings
    """
    text = _strip_html(raw_html)
    if not text:
        return {
            "score": 0,
            "positive_matches": [],
            "negative_matches": [],
            "experience_matches": [],
        }

    positive_matches = []
    positive_score = 0
    for keyword, weight in DESC_POSITIVE_KEYWORDS.items():
        if _POSITIVE_PATTERNS[keyword].search(text):
            positive_matches.append(keyword)
            positive_score += weight

    negative_matches = []
    negative_score = 0
    for keyword, weight in DESC_NEGATIVE_KEYWORDS.items():
        if _NEGATIVE_PATTERNS[keyword].search(text):
            negative_matches.append(keyword)
            negative_score += weight

    experience_matches = []
    experience_bonus = 0
    for pattern, bonus in DESC_EXPERIENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            experience_matches.append(match.group())
            experience_bonus += bonus

    raw_score = positive_score - negative_score + experience_bonus
    final_score = max(0, min(100, raw_score))

    logger.debug(
        "Analysis: pos=%d neg=%d exp=%d -> score=%d | +%s -%s exp%s",
        positive_score, negative_score, experience_bonus, final_score,
        positive_matches, negative_matches, experience_matches,
    )

    return {
        "score": final_score,
        "positive_matches": positive_matches,
        "negative_matches": negative_matches,
        "experience_matches": experience_matches,
    }
