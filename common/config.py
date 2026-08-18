"""Process-wide constants.

Company data is no longer configured here. It comes from the Google Sheet via
common.companies.sync and is read back out of company_info by
load_companies_by_ats().
"""

CRON_INTERVAL_SECONDS = 1800  # 1 hour
