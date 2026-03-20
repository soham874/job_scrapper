#!/usr/bin/env bash
# Run the Workday borg standalone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

export PYTHONPATH="$PROJECT_ROOT"

echo "[run_workday] Starting Workday borg..."
python3 -m borgs.workday.api
