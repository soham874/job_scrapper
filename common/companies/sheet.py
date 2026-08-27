"""Reads the tracked-company list out of the Google Sheet.

The sheet is the user-facing source of truth. This module knows how to pull
its rows down and turn them into normalised dicts; it does not touch the DB —
see common.companies.sync for that.

Only the columns named in COLUMN_MAP are read. Every other column in the sheet
is ignored, so extra bookkeeping columns can live alongside these freely.
"""

import os
import re
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

from common.logger import get_logger

logger = get_logger("companies.sheet")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

SHEET_ID = os.getenv("COMPANY_SHEET_ID", "")
SHEET_TAB = os.getenv("COMPANY_SHEET_TAB", "Companies")

_credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SERVICE_ACCOUNT_FILE = Path(_credentials_path)
if not SERVICE_ACCOUNT_FILE.is_absolute():
    SERVICE_ACCOUNT_FILE = BASE_DIR / SERVICE_ACCOUNT_FILE

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Maps our internal field name -> the column heading in the sheet.
# Headings are matched loosely (case, punctuation and spacing are ignored), so
# "ATS Link", "ats_link" and "Ats  link" all resolve to the same column.
COLUMN_MAP = {
    "company_name": "Company Name",
    "base_country": "Base Country",
    "target_location": "Target Location",
    "ats": "ATS",
    "ats_link": "ATS Link",
    "enabled": "Enable in tracker",
    # A company can exist as multiple distinct entities on LinkedIn (e.g.
    # "Walmart Global Tech" vs "Walmart Global Tech India") — the cell may
    # hold a comma-separated list of ids to search across all of them.
    "linkedin_company_ids": "LinkedIn Company Id",
    # The self_json borg's entire configuration. Only meaningful when ATS is
    # 'self_json'; every other row leaves both blank. Kept out of
    # REQUIRED_COLUMNS so a sheet without these columns still syncs.
    "job_api_curl": "Job API Curl",
    "job_spec": "Job Spec",
}

# Without these four a row cannot be acted on at all.
REQUIRED_COLUMNS = ("company_name", "ats", "ats_link", "enabled")

TRUTHY = {"yes", "y", "true", "1", "enabled", "on"}
FALSY = {"no", "n", "false", "0", "disabled", "off", ""}


class SheetError(Exception):
    """Raised when the sheet cannot be read or is missing required columns."""


def _normalise_heading(heading: str) -> str:
    """Lowercase a heading and reduce punctuation/whitespace runs to single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", heading.lower()).strip()


def _build_service():
    """Build the Sheets API client.

    The google imports are deliberately local: a missing dependency should
    surface as a logged sync failure rather than stopping a borg from booting.
    """
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise SheetError(
            "Google Sheets client libraries are not installed — "
            "run: pip install -r requirements.txt"
        ) from exc

    if not SHEET_ID:
        raise SheetError("COMPANY_SHEET_ID is not set in .env")
    if not SERVICE_ACCOUNT_FILE.exists():
        raise SheetError(
            f"Service account file not found at {SERVICE_ACCOUNT_FILE}. "
            "Set GOOGLE_SERVICE_ACCOUNT_FILE in .env to its path."
        )

    credentials = Credentials.from_service_account_file(
        str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _fetch_grid() -> List[List[str]]:
    """Return the raw cell grid for the configured tab, header row included."""
    service = _build_service()
    try:
        response = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=SHEET_ID, range=SHEET_TAB)
            .execute()
        )
    except Exception as exc:
        raise SheetError(f"Failed to read sheet '{SHEET_TAB}': {exc}") from exc

    return response.get("values", [])


def _parse_enabled(raw: str, company_name: str) -> bool:
    """Interpret the 'Enable in tracker' cell. Anything unrecognised is treated as off."""
    value = raw.strip().lower()
    if value in TRUTHY:
        return True
    if value not in FALSY:
        logger.warning(
            "%s | unrecognised 'Enable in tracker' value %r — treating as disabled",
            company_name, raw,
        )
    return False


def fetch_companies() -> List[Dict]:
    """
    Return one dict per sheet row with keys: company_name, base_country,
    target_location, ats, ats_link, enabled, linkedin_company_ids,
    job_api_curl, job_spec.

    linkedin_company_ids is optional — blank cells (or a missing column
    entirely) come back as None. The cell may hold a single id or a
    comma-separated list (e.g. "2029, 9390173"); it's normalised here to a
    clean comma-separated string with no surrounding whitespace.

    Rows with a blank company name are skipped. Raises SheetError if the sheet
    is unreachable or a required column is missing.
    """
    grid = _fetch_grid()
    if not grid:
        raise SheetError(f"Sheet tab '{SHEET_TAB}' is empty")

    headings = [_normalise_heading(h) for h in grid[0]]
    positions = {}
    for field, heading in COLUMN_MAP.items():
        wanted = _normalise_heading(heading)
        if wanted in headings:
            positions[field] = headings.index(wanted)

    missing = [COLUMN_MAP[f] for f in REQUIRED_COLUMNS if f not in positions]
    if missing:
        raise SheetError(
            f"Sheet tab '{SHEET_TAB}' is missing required column(s): {', '.join(missing)}. "
            f"Found headings: {', '.join(grid[0])}"
        )

    def cell(row: List[str], field: str) -> str:
        # The API omits trailing empty cells, so short rows are normal.
        index = positions.get(field)
        if index is None or index >= len(row):
            return ""
        return (row[index] or "").strip()

    companies = []
    for line_no, row in enumerate(grid[1:], start=2):
        name = cell(row, "company_name")
        if not name:
            continue

        ats = cell(row, "ats").lower()
        ats_link = cell(row, "ats_link")
        enabled = _parse_enabled(cell(row, "enabled"), name)

        if enabled and not (ats and ats_link):
            logger.warning(
                "%s (row %d) | enabled in the sheet but ATS/ATS Link is blank — "
                "it will not be scraped until both are filled in",
                name, line_no,
            )

        job_api_curl = cell(row, "job_api_curl")
        job_spec = cell(row, "job_spec")
        if enabled and ats == "self_json" and not (job_api_curl and job_spec):
            blank = " and ".join(
                label for label, value in (
                    ("Job API Curl", job_api_curl), ("Job Spec", job_spec)
                ) if not value
            )
            logger.warning(
                "%s (row %d) | ATS is 'self_json' but %s is blank — the self_json borg "
                "cannot scrape it until filled in",
                name, line_no, blank,
            )

        linkedin_ids = [
            part.strip() for part in cell(row, "linkedin_company_ids").split(",")
            if part.strip()
        ]

        companies.append({
            "company_name": name,
            "base_country": cell(row, "base_country"),
            "target_location": cell(row, "target_location"),
            "ats": ats,
            "ats_link": ats_link,
            "enabled": enabled,
            "linkedin_company_ids": ",".join(linkedin_ids) or None,
            "job_api_curl": job_api_curl or None,
            "job_spec": job_spec or None,
        })

    logger.info(
        "Read %d companies from sheet tab '%s' (%d enabled)",
        len(companies), SHEET_TAB, sum(1 for c in companies if c["enabled"]),
    )
    return companies
