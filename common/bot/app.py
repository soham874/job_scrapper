"""FastAPI application — thin HTTP routing layer for the Telegram bot.

All business logic lives in bot.handlers; formatting in notifications.formatter;
transport in notifications.telegram.  This file only wires routes.
"""

import os
import secrets
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request

from common.bot import router
from common.bot.dashboard import router as dashboard_router
from common.bot.deeplinks import resolve_bot_username
from common.logger import get_logger
from common.db.repository import get_company_id, insert_job
from common.notifications.formatter import format_job_message, make_inline_keyboard
from common.notifications.telegram import (
    TELEGRAM_BOT_TOKEN,
    send_message,
    set_my_commands,
)
from common.reminders import start_reminders
from common.sweeper import start_sweeper

logger = get_logger("bot.app")

TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "")

# Shared secret Telegram echoes back on every delivery. Optional, but without
# it the webhook has no way to tell a real update from anything else posted to
# a URL that has to be publicly reachable.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

# /test writes a job row and messages the chat. Harmless locally, not something
# to leave reachable on the public URL the webhook needs.
ENABLE_TEST_ENDPOINT = os.getenv("ENABLE_TEST_ENDPOINT", "false").lower() == "true"


def _register_webhook() -> None:
    """Register the Telegram webhook on startup."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_WEBHOOK_URL:
        logger.warning(
            "Telegram webhook not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_URL in .env"
        )
        return
    webhook_url = f"{TELEGRAM_WEBHOOK_URL.rstrip('/')}/telegram/webhook"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {"url": webhook_url}
    if TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    else:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET is unset — the webhook will accept any well-formed POST"
        )
    try:
        resp = requests.post(api_url, json=payload, timeout=15)
        if resp.ok:
            logger.info("Telegram webhook registered: %s", webhook_url)
        else:
            logger.error("Failed to register webhook: %s", resp.text)
    except Exception:
        logger.exception("Error registering Telegram webhook")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_webhook()
    # Learned from getMe rather than configured, so dashboard deep links always
    # point at whichever bot this token belongs to.
    resolve_bot_username()
    set_my_commands(router.COMMANDS)
    # The bot process owns every user decision — the Apply callbacks it serves
    # and, by timeout, the rejections it never receives. Sweeping here keeps
    # both halves in one place and out of the borgs.
    start_sweeper()
    # Same argument for reminders: the nudges are answered with the same
    # buttons this process serves.
    start_reminders()
    yield


app = FastAPI(title="Job Scrapper Bot", lifespan=lifespan)

# Read-only view of the tracker. Kept in its own module because it shares
# nothing with the webhook but the process it runs in.
app.include_router(dashboard_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "bot"}


@app.get("/test")
def test_bot():
    """Send a sample job notification to Telegram for end-to-end testing."""
    if not ENABLE_TEST_ENDPOINT:
        return {"ok": False, "error": "Disabled. Set ENABLE_TEST_ENDPOINT=true to use."}

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
    """Handle incoming Telegram updates — button taps, commands, and replies.

    Parsing and dispatch live in bot.router; this only unwraps the request and
    checks it actually came from Telegram.
    """
    if TELEGRAM_WEBHOOK_SECRET:
        sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secrets.compare_digest(sent, TELEGRAM_WEBHOOK_SECRET):
            logger.warning("Rejected webhook POST with a bad secret token")
            return {"ok": False}

    try:
        update = await request.json()
    except Exception:
        logger.exception("Failed to parse Telegram update")
        return {"ok": False}

    try:
        return router.handle_update(update)
    except Exception:
        # Returning non-200 makes Telegram redeliver, which for a write-shaped
        # verb means doing the work twice. Log it and take the update.
        logger.exception("Unhandled error while handling update")
        return {"ok": True}
