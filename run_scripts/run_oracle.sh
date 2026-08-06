#!/usr/bin/env bash
# Run the Oracle borg standalone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv if it exists
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

echo "[run_oracle] Running database migrations..."
python3 "$PROJECT_ROOT/run_scripts/run_migrations.py"
echo "[run_oracle] Migrations complete."

echo "[run_oracle] Starting Oracle borg on port 5003..."
python3 -m uvicorn borgs.oracle.api:app --host 0.0.0.0 --port 5003
