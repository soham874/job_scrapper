import os
from contextlib import asynccontextmanager
from datetime import date

import requests
from fastapi import FastAPI, Request

from common.db import get_company_id, get_job_by_id, insert_job, insert_application_status, update_job_decision
from common.logger import get_logger
from common.notifier import (
    TELEGRAM_BOT_TOKEN,
    _make_inline_keyboard,
    answer_callback_query,
    edit_telegram_message,
    format_decided_message,
    format_job_message,
    send_applied_response,
    send_telegram_message,
)

logger = get_logger("common.bot")

TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "")


def _register_webhook() -> None:
    """Register the Telegram webhook on startup."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_WEBHOOK_URL:
        logger.warning(
            "Telegram webhook not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_URL in .env"
        )
        return
    webhook_url = f"{TELEGRAM_WEBHOOK_URL.rstrip('/')}/telegram/webhook"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    try:
        resp = requests.post(api_url, json={"url": webhook_url}, timeout=15)
        if resp.ok:
            logger.info("Telegram webhook registered: %s", webhook_url)
        else:
            logger.error("Failed to register webhook: %s", resp.text)
    except Exception:
        logger.exception("Error registering Telegram webhook")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_webhook()
    yield


app = FastAPI(title="Job Scrapper Bot", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "bot"}


@app.get("/test")
def test_bot():
    """Send a sample job notification to Telegram for end-to-end testing."""
    company_name = "Stripe"
    company_id = get_company_id(company_name)
    if not company_id:
        return {"ok": False, "error": f"Company '{company_name}' not found in DB"}

    ats_job_id = "TEST-12345"
    title = "Senior Software Engineer, Backend"
    location = "Bangalore, India"
    link = "https://stripe.com/jobs/listing/senior-software-engineer/12345"

    job_id = insert_job(company_id, ats_job_id, title, location, link)
    if not job_id:
        return {"ok": False, "error": "Job already exists or insert failed"}

    sample_job = {
        "company": company_name,
        "title": title,
        "location": location,
        "keywords": ["java", "distributed systems", "kafka"],
        "application_link": link,
        "job_id": job_id,
    }

    text = format_job_message(sample_job, index=1, total=1, borg_name="test")
    keyboard = _make_inline_keyboard(job_id)
    msg_id = send_telegram_message(text, reply_markup=keyboard)

    return {"ok": True, "job_id": job_id, "message_id": msg_id}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram updates (callback queries from inline buttons)."""
    try:
        update = await request.json()
    except Exception:
        logger.exception("Failed to parse Telegram update")
        return {"ok": False}

    callback_query = update.get("callback_query")
    if not callback_query:
        return {"ok": True}

    callback_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id")

    if not data or not message_id:
        answer_callback_query(callback_id, "Invalid request")
        return {"ok": True}

    # Parse callback data: "apply:123" or "reject:123"
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("apply", "reject"):
        answer_callback_query(callback_id, "Unknown action")
        return {"ok": True}

    action = parts[0]
    try:
        job_id = int(parts[1])
    except ValueError:
        answer_callback_query(callback_id, "Invalid job ID")
        return {"ok": True}

    decision = "applied" if action == "apply" else "rejected"

    # Look up the job to check current state
    job = get_job_by_id(job_id)
    if not job:
        answer_callback_query(callback_id, "Job not found")
        return {"ok": True}

    if job.get("user_decision"):
        answer_callback_query(callback_id, f"Already marked as {job['user_decision']}")
        return {"ok": True}

    # Update DB
    success = update_job_decision(job_id, decision)
    if not success:
        answer_callback_query(callback_id, "Failed to save decision")
        return {"ok": True}

    # Edit the Telegram message to show the decision and remove buttons
    new_text = format_decided_message(job, decision)
    edit_telegram_message(message_id, new_text)

    label = "Applied ✅" if decision == "applied" else "Rejected ❌"
    answer_callback_query(callback_id, label)

    if decision == "applied":
        # Send LinkedIn search link + referral message parts
        send_applied_response(job)

        # Record in application_status
        insert_application_status(
            company_id=job["company_id"],
            job_id=job_id,
            applied_on=date.today().isoformat(),
            status="applied",
        )

    logger.info("Job %d marked as %s", job_id, decision)
    return {"ok": True}
