#!/usr/bin/env bash
# Run ALL borgs in parallel. Ctrl-C stops everything.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv if it exists
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

cleanup() {
    echo "[run_all] Stopping all borgs..."
    kill 0 2>/dev/null
    wait 2>/dev/null
    echo "[run_all] All borgs stopped."
}
trap cleanup EXIT INT TERM

echo "[run_all] Starting Workday borg (port 5001)..."
python3 -m uvicorn borgs.workday.api:app --host 0.0.0.0 --port 5001 &
PID_WD=$!

echo "[run_all] Starting Greenhouse borg (port 5002)..."
python3 -m uvicorn borgs.greenhouse.api:app --host 0.0.0.0 --port 5002 &
PID_GH=$!

echo "[run_all] All borgs running. PIDs: workday=$PID_WD greenhouse=$PID_GH"
echo "[run_all] Press Ctrl-C to stop all."

wait
