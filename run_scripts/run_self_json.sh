#!/usr/bin/env bash
# Run the Self JSON borg standalone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv if it exists
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

echo "[run_self_json] Running database migrations..."
python3 "$PROJECT_ROOT/run_scripts/run_migrations.py"
echo "[run_self_json] Migrations complete."

echo "[run_self_json] Starting Self JSON borg on port 5005..."
python3 -m uvicorn borgs.self_json.api:app --host 0.0.0.0 --port 5005
