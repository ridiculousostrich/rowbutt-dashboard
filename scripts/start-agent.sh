#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# Rowbutt Dashboard — Start Agent
# Launches the per-machine data collection agent.
# ────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="${PROJECT_DIR}/.venv"

if [ ! -d "${VENV}" ]; then
    echo "✗ Virtual environment not found. Run bootstrap.sh first."
    exit 1
fi

echo "== Rowbutt Agent =="
echo "Starting collection loop + HTTP server (foreground)..."
echo "Press Ctrl+C to stop."
echo ""

cd "${PROJECT_DIR}"
exec "${VENV}/bin/python3" -m cli.main agent start
