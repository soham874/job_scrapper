#!/usr/bin/env bash
# Run the Greenhouse borg standalone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

export PYTHONPATH="$PROJECT_ROOT"

echo "[run_greenhouse] Starting Greenhouse borg..."
python3 -m borgs.greenhouse.api
