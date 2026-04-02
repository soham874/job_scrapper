#!/usr/bin/env bash
source ../venv/bin/activate

# Run ALL borgs in parallel. Ctrl-C stops everything.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv if it exists
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# Run migrations once before starting any borgs
echo "[run_all] Running database migrations..."
python3 "$PROJECT_ROOT/run_scripts/run_migrations.py"
echo "[run_all] Migrations complete."

# Clear any leftover test data from previous runs
echo "[run_all] Clearing test data..."
python3 "$PROJECT_ROOT/run_scripts/clear_test_data.py"
echo "[run_all] Test data cleared."

# Run initial keyword weight recalibration from past decisions
echo "[run_all] Running keyword weight recalibration..."
python3 "$PROJECT_ROOT/run_scripts/run_learner.py"
echo "[run_all] Recalibration complete."

# Array of child PIDs
CHILD_PIDS=()
SHUTTING_DOWN=false

cleanup() {
    # Guard against re-entry (EXIT trap fires after INT/TERM handler)
    if $SHUTTING_DOWN; then
        return
    fi
    SHUTTING_DOWN=true

    echo ""
    echo "[run_all] Stopping all borgs..."
    for pid in "${CHILD_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "[run_all] Sending SIGTERM to PID $pid"
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait for each child to exit
    for pid in "${CHILD_PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    echo "[run_all] All borgs stopped."
}
trap cleanup INT TERM EXIT

echo "[run_all] Starting Telegram bot (port 5000)..."
python3 -m uvicorn common.bot.app:app --host 0.0.0.0 --port 5000 &
CHILD_PIDS+=($!)

echo "[run_all] Starting Workday borg (port 5001)..."
python3 -m uvicorn borgs.workday.api:app --host 0.0.0.0 --port 5001 &
CHILD_PIDS+=($!)

echo "[run_all] Starting Greenhouse borg (port 5002)..."
python3 -m uvicorn borgs.greenhouse.api:app --host 0.0.0.0 --port 5002 &
CHILD_PIDS+=($!)

echo "[run_all] All services running. PIDs: ${CHILD_PIDS[*]}"
echo "[run_all] Press Ctrl-C to stop all."

# Wait for all children — exits when they all finish or when interrupted
wait "${CHILD_PIDS[@]}" 2>/dev/null || true
