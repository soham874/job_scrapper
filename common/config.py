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

CRON_INTERVAL_SECONDS = 1800  # 1 hour

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
