"""Standalone migration runner — call before starting any borgs."""
from common.db import run_migrations

if __name__ == "__main__":
    run_migrations()
