#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# Rowbutt Dashboard — Bootstrap
# Initialises directories, venv, dependencies, and databases.
# Run ONCE per machine. Accepts --mode agent|aggregator|all.
# ────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MODE="${1:-all}"

echo "== Rowbutt Dashboard Bootstrap =="
echo "Mode: ${MODE}"

# 1. Create ~/.rowbutt directory structure
mkdir -p "${HOME}/.rowbutt/reports"

# 2. Create Python virtual environment
if [ ! -d "${PROJECT_DIR}/.venv" ]; then
    echo ">> Creating virtual environment..."
    python3 -m venv "${PROJECT_DIR}/.venv"
fi

# 3. Install dependencies
echo ">> Installing dependencies..."
"${PROJECT_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${PROJECT_DIR}/.venv/bin/pip" install --quiet -r "${PROJECT_DIR}/requirements.txt"

# 4. Initialise database(s)
case "${MODE}" in
    agent|Agent)
        echo ">> Initialising agent database..."
        cd "${PROJECT_DIR}"
        "${PROJECT_DIR}/.venv/bin/python3" -m cli.main agent init
        ;;
    aggregator|Aggregator)
        echo ">> Initialising aggregator database..."
        cd "${PROJECT_DIR}"
        "${PROJECT_DIR}/.venv/bin/python3" -m cli.main aggregator init
        ;;
    all|All)
        echo ">> Initialising both databases..."
        cd "${PROJECT_DIR}"
        "${PROJECT_DIR}/.venv/bin/python3" -m cli.main agent init
        "${PROJECT_DIR}/.venv/bin/python3" -m cli.main aggregator init
        ;;
    *)
        echo "Unknown mode: ${MODE}. Use agent, aggregator, or all."
        exit 1
        ;;
esac

echo ""
echo "✓ Bootstrap complete."
echo "  To start:"
echo "    ${PROJECT_DIR}/scripts/start-agent.sh     (agent mode)"
echo "    ${PROJECT_DIR}/scripts/start-aggregator.sh (aggregator mode)"
