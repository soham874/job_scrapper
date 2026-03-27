#!/usr/bin/env bash
# Run the Telegram bot webhook server standalone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv if it exists
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

echo "[run_bot] Running database migrations..."
python3 "$PROJECT_ROOT/run_scripts/run_migrations.py"
echo "[run_bot] Migrations complete."

echo "[run_bot] Starting Telegram bot webhook on port 5000..."
python3 -m uvicorn common.bot:app --host 0.0.0.0 --port 5000
