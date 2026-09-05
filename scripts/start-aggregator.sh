#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# Rowbutt Dashboard — Start Aggregator (one-shot)
# Pulls data from all configured agents and generates report.
# Meant to be run via cron (e.g. daily at 23:50).
# ────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="${PROJECT_DIR}/.venv"

if [ ! -d "${VENV}" ]; then
    echo "✗ Virtual environment not found. Run bootstrap.sh first."
    exit 1
fi

cd "${PROJECT_DIR}"

echo "== Rowbutt Aggregator =="
echo "Pulling data from agents..."
"${VENV}/bin/python3" -m cli.main aggregator pull-all

echo "Computing costs..."
"${VENV}/bin/python3" -m cli.main aggregator compute-costs

echo "Generating today's report..."
"${VENV}/bin/python3" -m cli.main report --today

echo "✓ Aggregator run complete."
