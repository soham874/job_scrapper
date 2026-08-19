import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.analyzer import analyze_description
from common.companies.sync import sync_companies_if_stale
from common.config import CRON_INTERVAL_SECONDS
from common.constants import DESC_SCORE_THRESHOLD
from common.db.repository import load_companies_by_ats, insert_job, insert_job_analysis
from common.logger import get_logger
from common.notifications.notifier import notify_new_jobs
from borgs.ashby.scraper import AshbyScraper

logger = get_logger("ashby")

BORG_NAME = "ashby"
MAX_WORKERS = 8


def _scrape_and_save(company: dict) -> list:
    """Scrape a single company and write results to DB immediately.
    Returns a list of dicts for newly saved jobs."""
    name = company["name"]
    url = company["url"]
    company_id = company["id"]
    logger.info("Processing company: %s", name)
    new_jobs = []
    try:
        scraper = AshbyScraper(
            ashby_url=url,
            company_name=name,
        )
        results = scraper.run()
        saved = 0
        discarded = 0
        for r in results:
            analysis = analyze_description(r.get("description", ""))
            if analysis["score"] < DESC_SCORE_THRESHOLD:
                logger.debug("%s | job %s score %d < %d — discarding",
                             name, r["job_id"], analysis["score"], DESC_SCORE_THRESHOLD)
                discarded += 1
                continue
            job_row_id = insert_job(
                company_id=company_id,
                ats_job_id=r["job_id"],
                title=r.get("title", ""),
                location=r.get("location", ""),
                application_link=r.get("application_link", ""),
            )
            if job_row_id:
                insert_job_analysis(
                    job_id=job_row_id,
                    relevance_score=analysis["score"],
                    positive_matches=json.dumps(analysis["positive_matches"]),
                    negative_matches=json.dumps(analysis["negative_matches"]),
                    experience_matches=json.dumps(analysis["experience_matches"]),
                    job_description=r.get("description", ""),
                )
                new_jobs.append({
                    "job_id": job_row_id,
                    "company": name,
                    "title": r.get("title", ""),
                    "location": r.get("location", ""),
                    "keywords": analysis["positive_matches"],
                    "application_link": r.get("application_link", ""),
                })
                saved += 1
        logger.info("%s | saved %d / %d jobs to DB (%d discarded by score)",
                    name, saved, len(results), discarded)
        return new_jobs
    except Exception:
        logger.exception("Error processing company %s", name)
        return new_jobs


def run_once():
    """Single execution: scrape all qualifying companies in parallel and save results."""
    logger.info("=== Ashby borg run started ===")
    sync_companies_if_stale()
    companies = load_companies_by_ats("ashby")
    all_new_jobs = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_scrape_and_save, company): company["name"]
            for company in companies
        }
        for future in as_completed(futures):
            company_name = futures[future]
            try:
                all_new_jobs.extend(future.result())
            except Exception:
                logger.exception("Unhandled error for company %s", company_name)

    logger.info("=== Ashby borg run finished | new jobs: %d ===", len(all_new_jobs))
    notify_new_jobs(BORG_NAME, all_new_jobs)
    return all_new_jobs


def _cron_loop():
    """Blocking loop that runs run_once every CRON_INTERVAL_SECONDS."""
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Unhandled error in ashby cron loop")

        logger.info("Sleeping %d seconds until next run...", CRON_INTERVAL_SECONDS)
        threading.Event().wait(CRON_INTERVAL_SECONDS)


def start_cron():
    """Start the cron loop in a daemon thread."""
    t = threading.Thread(target=_cron_loop, daemon=True, name="ashby-cron")
    t.start()
    logger.info("Ashby cron thread started (interval=%ds)", CRON_INTERVAL_SECONDS)
    return t
