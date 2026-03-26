#!/usr/bin/env bash
# Run the Greenhouse borg standalone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv if it exists
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

echo "[run_greenhouse] Running database migrations..."
python3 "$PROJECT_ROOT/run_scripts/run_migrations.py"
echo "[run_greenhouse] Migrations complete."

echo "[run_greenhouse] Starting Greenhouse borg on port 5002..."
python3 -m uvicorn borgs.greenhouse.api:app --host 0.0.0.0 --port 5002
