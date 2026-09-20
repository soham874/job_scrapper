"""Standalone script to run the ghosting sweep once.

The bot process runs this daily already (common.ghosting.start_ghoster). This
is for running it by hand — after changing GHOST_AFTER_DAYS, or to clear a
backlog of long-dead applications without waiting for tonight's pass.

Usage: python3 run_scripts/run_ghoster.py
"""

from common.ghosting import run_once

if __name__ == "__main__":
    run_once()
