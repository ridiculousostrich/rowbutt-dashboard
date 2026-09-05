#!/usr/bin/env bash
# ─── Rowbutt Agent — Start / Status / Stop ─────────────────────────
# Works with both systemd (preferred) and direct foreground mode.
#
# Usage:
#   bash start-agent.sh          # start via systemd (or foreground fallback)
#   bash start-agent.sh --foreground  # run in current terminal
#   bash start-agent.sh status   # check status
#   bash start-agent.sh stop     # stop
# ──────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$HOME/.rowbutt/venv"

FOREGROUND=false
ACTION="start"

for arg in "$@"; do
    case "$arg" in
        --foreground) FOREGROUND=true ;;
        status|stop)  ACTION="$arg" ;;
    esac
done

if [[ "$ACTION" == "status" ]]; then
    if systemctl --user is-active rowbutt-agent.service &>/dev/null; then
        echo "rowbutt-agent.service: active"
        systemctl --user status rowbutt-agent.service --no-pager 2>&1 | head -10
    else
        echo "rowbutt-agent.service: inactive or not installed"
    fi
    exit 0
fi

if [[ "$ACTION" == "stop" ]]; then
    if systemctl --user is-active rowbutt-agent.service &>/dev/null; then
        systemctl --user stop rowbutt-agent.service
        echo "stopped rowbutt-agent.service"
    else
        echo "agent not running via systemd"
    fi
    exit 0
fi

# ── Start ────────────────────────────────────────────────────────────────

if $FOREGROUND || ! command -v systemctl &>/dev/null; then
    echo "Starting Rowbutt agent in foreground on port 5000..."
    exec "$VENV_DIR/bin/rowbutt" agent start --host 0.0.0.0 --port 5000
fi

if systemctl --user enable --now rowbutt-agent.service &>/dev/null; then
    echo "rowbutt-agent.service started via systemd"
    systemctl --user status rowbutt-agent.service --no-pager 2>&1 | head -5
else
    echo "systemd not available, starting in foreground..."
    exec "$VENV_DIR/bin/rowbutt" agent start --host 0.0.0.0 --port 5000
fi
