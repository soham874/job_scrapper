"""Standalone script to run the stale-job sweep once.

The bot process runs this on a timer already (common.sweeper.start_sweeper).
This is for running it by hand — after changing SWEEP_AGE_HOURS, or to clear a
backlog without waiting for the next tick.

Usage: python3 run_scripts/run_sweeper.py
"""

from common.sweeper import run_once

if __name__ == "__main__":
    run_once()
