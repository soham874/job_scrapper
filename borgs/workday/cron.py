import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from common.config import load_companies, WORKDAY_COL, JOBS_DIR, CRON_INTERVAL_SECONDS
from common.constants import SEARCH_TEXT
from common.logger import get_logger
from borgs.workday.scraper import WorkdayScraper

logger = get_logger("workday")

BORG_NAME = "workday"
CSV_FIELDS = ["company", "title", "job_id", "location", "posted", "description"]
MAX_WORKERS = 8


def _save_results(results: list):
    """Save results to jobs/workday_<timestamp>.csv"""
    if not results:
        logger.info("No results to save — skipping file write")
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{BORG_NAME}_{ts}.csv"
    filepath = JOBS_DIR / filename

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    logger.info("Saved %d jobs to %s", len(results), filepath)
    return filepath


def _scrape_company(company: dict) -> list:
    """Scrape a single company. Designed to run inside a thread pool."""
    name = company["name"]
    url = company["url"]
    logger.info("Processing company: %s", name)
    try:
        scraper = WorkdayScraper(
            base_url=url,
            company_name=name,
            search_text=SEARCH_TEXT,
            facets=None,
        )
        return scraper.run()
    except Exception:
        logger.exception("Error processing company %s", name)
        return []


def run_once():
    """Single execution: scrape all qualifying companies in parallel and save results."""
    logger.info("=== Workday borg run started ===")
    companies = load_companies(WORKDAY_COL)
    all_results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_scrape_company, company): company["name"]
            for company in companies
        }
        for future in as_completed(futures):
            company_name = futures[future]
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                logger.exception("Unhandled error for company %s", company_name)

    _save_results(all_results)
    logger.info("=== Workday borg run finished | total jobs: %d ===", len(all_results))
    return all_results


def _cron_loop():
    """Blocking loop that runs run_once every CRON_INTERVAL_SECONDS."""
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Unhandled error in workday cron loop")

        logger.info("Sleeping %d seconds until next run...", CRON_INTERVAL_SECONDS)
        threading.Event().wait(CRON_INTERVAL_SECONDS)


def start_cron():
    """Start the cron loop in a daemon thread."""
    t = threading.Thread(target=_cron_loop, daemon=True, name="workday-cron")
    t.start()
    logger.info("Workday cron thread started (interval=%ds)", CRON_INTERVAL_SECONDS)
    return t
