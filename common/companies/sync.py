"""Reconciles company_info against the Google Sheet.

The sheet is what the user edits; company_info is what the borgs read. This
module is the one-way bridge between them. It never deletes: a company that is
switched off or removed from the sheet simply has enabled flipped to 0, so its
job history and application_status rows survive and it can be switched back on
at any time.
"""

from typing import Optional

from common.companies.sheet import SheetError, fetch_companies
from common.db.connection import advisory_lock
from common.db.repository import (
    disable_companies_absent_from,
    seconds_since_last_sync,
    upsert_company,
)
from common.logger import get_logger

logger = get_logger("companies.sync")

SYNC_LOCK_NAME = "job_scrapper_company_sheet_sync"
SYNC_LOCK_TIMEOUT_SECONDS = 30

# All three borgs sync at the top of every run and tend to start together.
# A sync newer than this is considered good enough, so only the first borg
# through the door actually hits the Sheets API.
SYNC_TTL_SECONDS = 300


def sync_companies() -> dict:
    """
    Pull the sheet and apply it to company_info.

    Returns counts: {'inserted', 'updated', 'unchanged', 'disabled', 'total'}.
    Raises SheetError if the sheet cannot be read or is malformed.
    """
    companies = fetch_companies()
    if not companies:
        raise SheetError("Sheet contained no company rows — refusing to sync")

    seen = set()
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "disabled": 0}

    for company in companies:
        name = company["company_name"]
        if name in seen:
            logger.warning(
                "Duplicate row for '%s' in the sheet — the last one wins", name
            )
        seen.add(name)

        outcome = upsert_company(
            company_name=name,
            base_country=company["base_country"],
            target_location=company["target_location"],
            ats=company["ats"],
            ats_link=company["ats_link"],
            enabled=company["enabled"],
            linkedin_company_ids=company["linkedin_company_ids"],
        )
        counts[outcome] += 1

    disabled = disable_companies_absent_from(sorted(seen))
    counts["disabled"] = len(disabled)
    if disabled:
        logger.warning(
            "Disabled %d company/companies no longer present in the sheet: %s",
            len(disabled), ", ".join(disabled),
        )

    counts["total"] = len(companies)
    logger.info(
        "Company sync complete | total=%d inserted=%d updated=%d unchanged=%d disabled=%d",
        counts["total"], counts["inserted"], counts["updated"],
        counts["unchanged"], counts["disabled"],
    )
    return counts


def sync_companies_if_stale(ttl_seconds: int = SYNC_TTL_SECONDS) -> Optional[dict]:
    """
    Sync unless another process already did so within ttl_seconds.

    Never raises: a sheet outage logs an error and leaves company_info at its
    last known good state, so scraping continues rather than stopping dead.
    Returns the sync counts, or None if the sync was skipped or failed.
    """
    try:
        with advisory_lock(SYNC_LOCK_NAME, SYNC_LOCK_TIMEOUT_SECONDS) as acquired:
            if not acquired:
                logger.warning(
                    "Could not acquire the company sync lock within %ds — "
                    "continuing with the companies already in the DB",
                    SYNC_LOCK_TIMEOUT_SECONDS,
                )
                return None

            age = seconds_since_last_sync()
            if age is not None and age < ttl_seconds:
                logger.info(
                    "Company sheet synced %ds ago (< %ds) — skipping", age, ttl_seconds
                )
                return None

            return sync_companies()
    except SheetError as exc:
        logger.error(
            "Company sheet sync failed (%s) — continuing with the companies "
            "already in the DB", exc,
        )
        return None
    except Exception:
        logger.exception(
            "Unexpected error during company sheet sync — continuing with the "
            "companies already in the DB"
        )
        return None
