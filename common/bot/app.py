"""FastAPI application — thin HTTP routing layer for the Telegram bot.

All business logic lives in bot.handlers; formatting in notifications.formatter;
transport in notifications.telegram.  This file only wires routes.
"""

import os
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request

from common.bot.handlers import handle_decision
from common.logger import get_logger
from common.db.repository import get_company_id, insert_job
from common.notifications.formatter import format_job_message, make_inline_keyboard
from common.notifications.telegram import TELEGRAM_BOT_TOKEN, answer_callback_query, send_message

logger = get_logger("bot.app")

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
    keyboard = make_inline_keyboard(job_id)
    msg_id = send_message(text, reply_markup=keyboard)

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

    return handle_decision(callback_id, message_id, job_id, action)
