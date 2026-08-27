"""Process-wide constants.

Company data is no longer configured here. It comes from the Google Sheet via
common.companies.sync and is read back out of company_info by
load_companies_by_ats().
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Loaded here rather than relying on common.db.connection having been imported
# first. This module is read by the sweeper before it touches the database, so
# without its own load_dotenv the settings below would silently fall back to
# their defaults and ignore .env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CRON_INTERVAL_SECONDS = 1800  # 30 minutes

# --- Borg stagger ----------------------------------------------------------
# The borgs run as separate processes with the same interval, so without an
# offset they all wake together: four providers scraped at once and a single
# burst of Telegram messages. Each borg instead takes a slot spread evenly
# across the window below, in this order. Appending a new borg re-spaces the
# existing slots, which is fine — the slots only have to differ, not stay put.
BORG_ORDER = ["workday", "greenhouse", "oracle", "ashby", "self_json"]

# Span the slots are spread over. Capped at CRON_INTERVAL_SECONDS by
# common.scheduling, since a wider window would wrap borgs onto each other.
BORG_STAGGER_WINDOW_SECONDS = int(os.getenv("BORG_STAGGER_WINDOW_SECONDS", "1800"))  # 30 minutes

# --- Stale-job sweep -------------------------------------------------------
# There is no Reject button. A job the user has not applied to within
# SWEEP_AGE_HOURS is taken as unwanted, and the sweeper marks it rejected.
# The window is deliberately aligned with how long the notification itself
# survives in Telegram — once the message is gone the job can no longer be
# acted on, so leaving it undecided serves no purpose.
SWEEP_AGE_HOURS = int(os.getenv("SWEEP_AGE_HOURS", "24"))

# How often the sweeper wakes up. Finer than the window, so a job is rejected
# within roughly this long of crossing it rather than up to a full window late.
SWEEP_INTERVAL_SECONDS = int(os.getenv("SWEEP_INTERVAL_SECONDS", "21600"))  # 6 hours

# --- Follow-up reminders ---------------------------------------------------
# The other half of the tracker: the sweeper decides jobs the user ignored,
# this decides applications the user is sitting on. An application is nudged
# when its follow-up date arrives, or when its status has not moved for longer
# than common.applications.STALE_AFTER_DAYS allows.
REMINDER_INTERVAL_SECONDS = int(os.getenv("REMINDER_INTERVAL_SECONDS", "21600"))  # 6 hours

# Minimum gap between two nudges about the same application. Longer than the
# loop interval on purpose — the loop waking up is not a reason to nag again,
# and a row stays due until it is acted on.
REMINDER_RENOTIFY_HOURS = int(os.getenv("REMINDER_RENOTIFY_HOURS", "24"))

# Cap on nudges per pass. The first run after this ships could otherwise match
# every application at once and arrive as a wall of messages.
REMINDER_MAX_PER_RUN = int(os.getenv("REMINDER_MAX_PER_RUN", "10"))
