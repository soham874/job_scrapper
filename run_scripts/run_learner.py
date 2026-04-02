"""Standalone script to run keyword weight recalibration.

Intended to be called via system cron (e.g. every 6 hours) or once at startup.
Usage: python3 run_scripts/run_learner.py
"""

from common.learner import recalibrate

if __name__ == "__main__":
    recalibrate()
