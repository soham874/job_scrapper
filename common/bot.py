import os
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request

from common.db import get_job_by_id, update_job_decision
from common.logger import get_logger
from common.notifier import (
    TELEGRAM_BOT_TOKEN,
    answer_callback_query,
    edit_telegram_message,
    format_decided_message,
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

    logger.info("Job %d marked as %s", job_id, decision)
    return {"ok": True}
