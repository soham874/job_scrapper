import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.config import CRON_INTERVAL_SECONDS
from common.constants import SEARCH_TEXT
from common.db import load_companies_by_ats, get_company_id, upsert_job
from common.logger import get_logger
from borgs.workday.scraper import WorkdayScraper

logger = get_logger("workday")

BORG_NAME = "workday"
MAX_WORKERS = 8


def _save_results(results: list):
    """Upsert results into the job_info table."""
    if not results:
        logger.info("No results to save — skipping DB write")
        return 0

    saved = 0
    for job in results:
        company_id = get_company_id(job["company"])
        if company_id is None:
            logger.warning("Company '%s' not found in DB — skipping job %s", job["company"], job["job_id"])
            continue
        ok = upsert_job(
            company_id=company_id,
            ats_job_id=job["job_id"],
            title=job.get("title", ""),
            location=job.get("location", ""),
            description=job.get("description", ""),
            application_link=job.get("application_link", ""),
        )
        if ok:
            saved += 1

    logger.info("Saved %d / %d jobs to DB", saved, len(results))
    return saved


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
        results = scraper.run()
        # Attach application_link to each result
        for r in results:
            r["application_link"] = f"{scraper.base_url}{r.get('external_path', '')}"
        return results
    except Exception:
        logger.exception("Error processing company %s", name)
        return []


def run_once():
    """Single execution: scrape all qualifying companies in parallel and save results."""
    logger.info("=== Workday borg run started ===")
    companies = load_companies_by_ats("workday")
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
