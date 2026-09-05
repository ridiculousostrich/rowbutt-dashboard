#!/usr/bin/env bash
# ─── Rowbutt Aggregator — Run the full pipeline ─────────────────────
# Pulls summaries from all agents, computes costs, and saves a report.
#
# Usage:
#   bash start-aggregator.sh             # pipeline via systemd timer
#   bash start-aggregator.sh --now       # run pipeline immediately
#   bash start-aggregator.sh status      # check timer status
# ──────────────────────────────────────────────────────────────────────────

set -euo pipefail

VENV_DIR="$HOME/.rowbutt/venv"
ROWBUTT="$VENV_DIR/bin/rowbutt"

case "${1:-}" in
    status)
        echo "── Timer ──"
        systemctl --user status rowbutt-aggregator.timer --no-pager 2>&1 | head -8 || echo "(no systemd user session — aggregator not installed as a service)"
        echo ""
        echo "── Last run ──"
        systemctl --user status rowbutt-aggregator.service --no-pager 2>&1 | head -10 || echo "(no systemd user session)"
        exit 0
        ;;
    --now)
        echo "Running aggregator pipeline now..."
        "$ROWBUTT" aggregator pull-all
        "$ROWBUTT" aggregator compute-costs
        "$ROWBUTT" report today --save
        echo "Done."
        exit 0
        ;;
    *)
        # Default: ensure timer is enabled (idempotent)
        if systemctl --user is-enabled rowbutt-aggregator.timer &>/dev/null 2>&1; then
            systemctl --user status rowbutt-aggregator.timer --no-pager 2>&1 | head -6
            echo ""
            echo "Aggregator timer is active — runs daily at 23:55."
            echo "Run 'bash start-aggregator.sh --now' to trigger immediately."
        else
            echo "Aggregator timer not installed or no systemd user session."
            echo "Run the pipeline manually:"
            echo "  rowbutt aggregator pull-all && \\"
            echo "  rowbutt aggregator compute-costs && \\"
            echo "  rowbutt report today --save"
        fi
        exit 0
        ;;
esac
