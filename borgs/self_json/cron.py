import html
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.analyzer import analyze_description
from common.companies.sync import sync_companies_if_stale
from common.config import CRON_INTERVAL_SECONDS
from common.constants import DESC_SCORE_THRESHOLD
from common.db.repository import load_self_json_companies, insert_job, insert_job_analysis
from common.logger import get_logger
from common.notifications.notifier import notify_new_jobs
from common.notifications.telegram import is_configured, send_message
from borgs.self_json.scraper import SelfJsonScraper

logger = get_logger("self_json")

BORG_NAME = "self_json"
MAX_WORKERS = 8

# Signature of the last reported set of broken companies. A row that stays
# broken must not re-nag every CRON_INTERVAL_SECONDS, so an alert only goes out
# when the set of problems actually changes.
_last_error_signature = None
_error_lock = threading.Lock()


def _scrape_and_save(company: dict):
    """Scrape a single company and write results to DB immediately.

    Returns (new_jobs, config_error). config_error is a string when the row
    cannot be scraped as configured — the sheet is the source of truth here, so
    a typo has to surface rather than look like a quiet day.
    """
    name = company["name"]
    company_id = company["id"]
    logger.info("Processing company: %s", name)
    new_jobs = []

    if not (company.get("curl") or "").strip():
        return new_jobs, "no curl in the sheet's 'ATS Link'"
    if not (company.get("spec") or "").strip():
        return new_jobs, "no 'Job Spec' in the sheet"

    try:
        scraper = SelfJsonScraper(
            curl_text=company["curl"],
            spec_text=company["spec"],
            company_name=name,
        )
    except Exception as exc:
        logger.error("%s | bad configuration: %s", name, exc)
        return new_jobs, str(exc)

    try:
        results = scraper.run()
    except Exception as exc:
        logger.exception("Error scraping company %s", name)
        return new_jobs, f"scrape failed: {exc}"

    if scraper.raw_count == 0:
        # Parseable config that returns nothing at all is the signature of a
        # stale curl — pinned client-hint versions expire, request bodies get
        # new required keys — and both APIs this was built against answer 200
        # while doing it. A request that never landed is a different problem
        # and often just transient, so the two are reported separately.
        if scraper.responses_ok == 0:
            return new_jobs, "no successful response from the endpoint (see logs)"
        return new_jobs, "API returned 0 jobs before filtering — the curl may be stale"

    saved = 0
    discarded = 0
    try:
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
        return new_jobs, None
    except Exception:
        logger.exception("Error processing company %s", name)
        return new_jobs, None


def _report_config_errors(errors: dict) -> None:
    """Telegram the set of misconfigured companies, but only when it changes."""
    global _last_error_signature

    signature = tuple(sorted(errors.items()))
    with _error_lock:
        if signature == _last_error_signature:
            return
        _last_error_signature = signature

    if not errors:
        logger.info("All self_json sources are healthy again")
        return

    logger.warning("%d self_json source(s) need attention: %s",
                   len(errors), ", ".join(sorted(errors)))

    if not is_configured():
        return

    lines = ["⚠️ <b>Self JSON borg — sources need attention</b>", ""]
    for name in sorted(errors):
        lines.append(f"• <b>{html.escape(name)}</b>: {html.escape(errors[name])}")
    lines.append("")
    lines.append("Fix the row in the company sheet — no jobs are being read from it.")
    send_message("\n".join(lines))


def run_once():
    """Single execution: scrape all qualifying companies in parallel and save results."""
    logger.info("=== Self JSON borg run started ===")
    sync_companies_if_stale()
    companies = load_self_json_companies()
    all_new_jobs = []
    errors = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_scrape_and_save, company): company["name"]
            for company in companies
        }
        for future in as_completed(futures):
            company_name = futures[future]
            try:
                jobs, error = future.result()
                all_new_jobs.extend(jobs)
                if error:
                    errors[company_name] = error
            except Exception as exc:
                logger.exception("Unhandled error for company %s", company_name)
                errors[company_name] = f"unhandled error: {exc}"

    logger.info("=== Self JSON borg run finished | new jobs: %d | broken sources: %d ===",
                len(all_new_jobs), len(errors))
    notify_new_jobs(BORG_NAME, all_new_jobs)
    _report_config_errors(errors)
    return all_new_jobs


def _cron_loop():
    """Blocking loop that runs run_once every CRON_INTERVAL_SECONDS."""
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Unhandled error in self_json cron loop")

        logger.info("Sleeping %d seconds until next run...", CRON_INTERVAL_SECONDS)
        threading.Event().wait(CRON_INTERVAL_SECONDS)


def start_cron():
    """Start the cron loop in a daemon thread."""
    t = threading.Thread(target=_cron_loop, daemon=True, name="self_json-cron")
    t.start()
    logger.info("Self JSON cron thread started (interval=%ds)", CRON_INTERVAL_SECONDS)
    return t
