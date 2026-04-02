"""Adaptive keyword weight learner — adjusts scoring weights based on user decisions.

Computes per-keyword lift (acceptance rate vs overall acceptance rate) and
persists multipliers to the keyword_weight_overrides table. The analyzer
reads these multipliers at scoring time to adjust static weights.
"""

import json
from collections import defaultdict
from typing import Dict

from common.db.repository import (
    get_decided_jobs_with_keywords,
    upsert_keyword_weight_override,
)
from common.logger import get_logger

logger = get_logger("common.learner")

# Minimum decided jobs containing a keyword before we adjust its weight
MIN_KEYWORD_SAMPLES = 3

# Minimum total decided jobs before recalibration activates at all
MIN_TOTAL_DECISIONS = 10

# Lift thresholds — keywords within the dead zone stay at 1.0
LIFT_BOOST_THRESHOLD = 1.3
LIFT_SUPPRESS_THRESHOLD = 0.7

# Multiplier bounds
MAX_MULTIPLIER = 2.0
MIN_MULTIPLIER = 0.25


def _extract_keywords(row: dict) -> list:
    """Parse positive_matches and negative_matches JSON into a flat keyword list."""
    keywords = []
    for field in ("positive_matches", "negative_matches"):
        raw = row.get(field)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                keywords.extend(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return keywords


def _compute_multiplier(lift: float) -> float:
    """Map a lift value to a weight multiplier, clamped to [MIN, MAX]."""
    if lift > LIFT_BOOST_THRESHOLD:
        # Scale boost: lift 1.3 -> 1.15, lift 2.0 -> 1.5, etc.
        m = 1.0 + (lift - 1.0) * 0.5
        return min(MAX_MULTIPLIER, m)
    elif lift < LIFT_SUPPRESS_THRESHOLD:
        # Use lift directly as multiplier (already < 0.7)
        return max(MIN_MULTIPLIER, lift)
    else:
        # Dead zone — no adjustment
        return 1.0


def recalibrate() -> Dict[str, float]:
    """
    Analyse user decisions and compute per-keyword weight multipliers.

    Returns a dict of keyword -> multiplier for all adjusted keywords.
    Keywords in the neutral dead zone are not included (implicitly 1.0).
    """
    decided = get_decided_jobs_with_keywords()
    total = len(decided)

    if total < MIN_TOTAL_DECISIONS:
        logger.info("Only %d decided jobs (need %d) — skipping recalibration",
                     total, MIN_TOTAL_DECISIONS)
        return {}

    total_accepted = sum(1 for d in decided if d["user_decision"] == "applied")
    total_rejected = total - total_accepted

    if total_accepted == 0:
        logger.info("No accepted jobs yet — skipping recalibration")
        return {}

    overall_rate = total_accepted / total
    logger.info("Recalibrating: %d decisions (%d accepted, %d rejected, rate=%.2f)",
                total, total_accepted, total_rejected, overall_rate)

    # Aggregate per-keyword accept/reject counts
    keyword_stats = defaultdict(lambda: {"accepted": 0, "rejected": 0})
    for row in decided:
        accepted = row["user_decision"] == "applied"
        for kw in _extract_keywords(row):
            if accepted:
                keyword_stats[kw]["accepted"] += 1
            else:
                keyword_stats[kw]["rejected"] += 1

    # Compute lift and multiplier for each keyword
    multipliers = {}
    for kw, stats in keyword_stats.items():
        sample_count = stats["accepted"] + stats["rejected"]
        if sample_count < MIN_KEYWORD_SAMPLES:
            continue

        kw_rate = stats["accepted"] / sample_count
        lift = kw_rate / overall_rate
        multiplier = _compute_multiplier(lift)

        upsert_keyword_weight_override(
            keyword=kw,
            multiplier=multiplier,
            accept_count=stats["accepted"],
            reject_count=stats["rejected"],
            sample_count=sample_count,
            lift=round(lift, 4),
        )

        if multiplier != 1.0:
            multipliers[kw] = multiplier
            logger.info("  %-25s accept=%d reject=%d lift=%.2f -> multiplier=%.2f",
                        kw, stats["accepted"], stats["rejected"], lift, multiplier)

    logger.info("Recalibration complete: %d keywords adjusted", len(multipliers))
    return multipliers
