import csv
from pathlib import Path
from typing import List, Dict

from common.logger import get_logger

logger = get_logger("common.config")

BASE_DIR = Path(__file__).resolve().parent.parent
COMPANY_CSV = BASE_DIR / "company_info.csv"
JOBS_DIR = BASE_DIR / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

CRON_INTERVAL_SECONDS = 3600  # 1 hour

# Column names in company_info.csv
WORKDAY_COL = "Workday Carrer Page"
GREENHOUSE_COL = "Greenhouse Carrer Page"


def load_companies(borg_column: str) -> List[Dict[str, str]]:
    """
    Reads company_info.csv and returns a list of dicts
    with keys 'name' and 'url' for companies that have
    a non-empty value in the given column.
    """
    companies = []
    try:
        with open(COMPANY_CSV, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = (row.get(borg_column) or "").strip()
                name = (row.get("Company Name") or "").strip()
                if url and name:
                    companies.append({"name": name, "url": url})
        logger.info("Loaded %d companies for column '%s'", len(companies), borg_column)
    except Exception:
        logger.exception("Failed to load companies from %s", COMPANY_CSV)
    return companies
