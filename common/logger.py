import logging
import os
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def get_logger(borg_name: str) -> logging.Logger:
    """
    Returns a logger that writes to both console and a borg-specific log file.
    Log file: logs/<borg_name>.log
    """
    logger = logging.getLogger(borg_name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    log_file = LOG_DIR / f"{borg_name}.log"

    # File handler — captures everything (DEBUG+)
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)

    # Console handler — INFO+
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger
