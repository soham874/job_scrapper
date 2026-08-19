#!/usr/bin/env bash
# Run the Ashby borg standalone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv if it exists
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

echo "[run_ashby] Running database migrations..."
python3 "$PROJECT_ROOT/run_scripts/run_migrations.py"
echo "[run_ashby] Migrations complete."

echo "[run_ashby] Starting Ashby borg on port 5004..."
python3 -m uvicorn borgs.ashby.api:app --host 0.0.0.0 --port 5004
