"""Standalone company sheet sync — pulls the Google Sheet into company_info.

The borgs do this themselves at the top of every run. Run it by hand to apply
a sheet edit immediately, or to check the sheet wiring after setup:

    PYTHONPATH=. python3 run_scripts/sync_companies.py

Unlike the in-cron sync this ignores the freshness TTL and reports failures as
a non-zero exit code.
"""
import sys

import mysql.connector

from common.companies.sheet import SheetError
from common.companies.sync import (
    SYNC_LOCK_NAME,
    SYNC_LOCK_TIMEOUT_SECONDS,
    sync_companies,
)
from common.db.connection import advisory_lock

if __name__ == "__main__":
    try:
        with advisory_lock(SYNC_LOCK_NAME, SYNC_LOCK_TIMEOUT_SECONDS) as acquired:
            if not acquired:
                print(
                    f"[sync_companies] Another sync is in progress "
                    f"(waited {SYNC_LOCK_TIMEOUT_SECONDS}s). Try again shortly.",
                    file=sys.stderr,
                )
                sys.exit(1)
            counts = sync_companies()
    except SheetError as exc:
        print(f"[sync_companies] FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    except mysql.connector.Error as exc:
        # Almost always an unmigrated/unreachable DB — a stack trace here helps nobody.
        print(f"[sync_companies] FAILED: database error: {exc}", file=sys.stderr)
        print(
            "[sync_companies] Run 'PYTHONPATH=. python3 run_scripts/run_migrations.py' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        "[sync_companies] {total} rows | inserted={inserted} updated={updated} "
        "unchanged={unchanged} disabled={disabled}".format(**counts)
    )
