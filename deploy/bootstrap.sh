#!/usr/bin/env bash
# ─── Rowbutt Dashboard — Bootstrap Installer ───────────────────────────
# One-command install for agent, aggregator, or both.
#
# Usage:
#   bash bootstrap.sh              # interactive (prompts for systemd units)
#   bash bootstrap.sh --help       # show this header
#
# Destructive actions: creates venv, installs deps, creates ~/.rowbutt/.
# Safe to re-run.
# ──────────────────────────────────────────────────────────────────────────

set -euo pipefail

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

STEP=1

info()  { printf "${BOLD}${GREEN}[%s]${NC} %s\n" "$STEP" "$1"; ((STEP++)); }
warn()  { printf "${YELLOW}⚠  %s${NC}\n" "$1"; }
fail()  { printf "${RED}✗  %s${NC}\n" "$1"; exit 1; }
ok()    { printf "  ${GREEN}✓${NC}  %s\n" "$1"; }
cmd()   { printf "  ${CYAN}→${NC}  %s\n" "$1"; }

# Locate the deploy/ directory (script can be called from anywhere)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Help ──────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--help" ]]; then
    head -15 "$0"
    exit 0
fi

echo ""
printf "╔══════════════════════════════════════════════════════════╗\n"
printf "║  ${BOLD}Rowbutt Dashboard — Bootstrap Installer${NC}           ║\n"
printf "║  Project: %-43s║\n" "$PROJECT_DIR"
printf "╚══════════════════════════════════════════════════════════╝\n"
echo ""

# ── 1. Prerequisites ─────────────────────────────────────────────────────
info "Checking prerequisites"

PYTHON=$(command -v python3 || command -v python || fail "Python 3 not found")
ok "Python: $($PYTHON --version 2>&1)"

# Check venv availability
if ! "$PYTHON" -c "import ensurepip" 2>/dev/null; then
    # Some Debian installs don't have ensurepip; try python3-venv package
    apt install -y python3-venv python3-pip 2>/dev/null || true
fi

# Check systemd (optional — warn if missing)
SYSTEMD_AVAILABLE=false
if command -v systemctl &>/dev/null; then
    SYSTEMD_AVAILABLE=true
    ok "systemd detected"
else
    warn "systemd not detected — skipping systemd unit installation"
fi

# ── 2. Create virtual environment ────────────────────────────────────────
info "Creating virtual environment"

VENV_DIR="$HOME/.rowbutt/venv"
if [[ -d "$VENV_DIR" ]]; then
    warn "venv already exists at $VENV_DIR — will update"
else
    cmd "$PYTHON -m venv $VENV_DIR"
    ok "venv created at $VENV_DIR"
fi

# Activate
source "$VENV_DIR/bin/activate"
ok "activated: $(which python3)"

# ── 3. Install dependencies ──────────────────────────────────────────────
info "Installing Python dependencies"

# Upgrade pip first
cmd "pip install --quiet --upgrade pip"
# Install from requirements
cmd "pip install --quiet -r $PROJECT_DIR/requirements.txt"
# Install the package itself in editable mode
cmd "pip install --quiet -e $PROJECT_DIR"
ok "dependencies installed"

# ── 4. Create ~/.rowbutt/ directory structure ─────────────────────────────
info "Creating runtime directories"

mkdir -p "$HOME/.rowbutt/reports"
ok "$HOME/.rowbutt/reports/"

# ── 5. Create example agents.json ────────────────────────────────────────
info "Creating example agents.json"

AGENTS_JSON="$HOME/.rowbutt/agents.json"
if [[ -f "$AGENTS_JSON" ]]; then
    warn "agents.json already exists at $AGENTS_JSON — not overwriting"
else
    cat > "$AGENTS_JSON" <<'AGENTS'
{
    "_comment": "Rowbutt Dashboard — agent registry.",
    "_doc": "List every machine running the Rowbutt agent daemon.",
    "agents": [
        {
            "hostname": "my-inference-box",
            "url": "http://192.168.1.100:5000",
            "description": "e.g. ubuntu-server with Ollama + vLLM"
        }
    ]
}
AGENTS
    ok "created $AGENTS_JSON — edit it with your agent URLs"
fi

# ── 6. Make CLI accessible from PATH ──────────────────────────────────────
info "Ensuring rowbutt CLI is on PATH"

# Symlink the venv bin so `rowbutt` works
if ! command -v rowbutt &>/dev/null; then
    warn "'rowbutt' not on PATH — add to ~/.bashrc or ~/.zshrc:"
    cmd "  export PATH=\"\$HOME/.rowbutt/venv/bin:\$PATH\""
fi

# ── 7. Install systemd units (interactive) ────────────────────────────────
if $SYSTEMD_AVAILABLE; then
    info "Installing systemd units (user-level — no sudo needed)"

    # Copy unit files
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"

    for unit in rowbutt-agent.service rowbutt-aggregator.service rowbutt-aggregator.timer; do
        if [[ -f "$SCRIPT_DIR/$unit" ]]; then
            cp "$SCRIPT_DIR/$unit" "$UNIT_DIR/$unit"
            ok "installed $unit"
        else
            warn "$unit not found in $SCRIPT_DIR — skipping"
        fi
    done

    systemctl --user daemon-reload 2>/dev/null || true

    echo ""
    echo "  ── Enable which systemd units? ──"
    echo ""
    echo "    ${BOLD}1) Agent only${NC}       — rowbutt-agent.service"
    echo "                            Collects GPU/token metrics on this machine"
    echo ""
    echo "    ${BOLD}2) Aggregator only${NC}   — rowbutt-aggregator.service + timer"
    echo "                            Pulls from agents, computes costs, saves reports"
    echo ""
    echo "    ${BOLD}3) Both${NC}             — All three units"
    echo "                            This machine is an agent + the central aggregator"
    echo ""
    echo "    ${BOLD}4) Skip${NC}             — Don't enable any systemd units now"
    echo ""

    while true; do
        read -rp "  Choose [1-4]: " CHOICE
        case "$CHOICE" in
            1)
                systemctl --user enable --now rowbutt-agent.service 2>/dev/null || true
                ok "enabled rowbutt-agent.service"
                break
                ;;
            2)
                systemctl --user enable --now rowbutt-aggregator.timer 2>/dev/null || true
                ok "enabled rowbutt-aggregator.timer"
                break
                ;;
            3)
                systemctl --user enable --now rowbutt-agent.service 2>/dev/null || true
                systemctl --user enable --now rowbutt-aggregator.timer 2>/dev/null || true
                ok "enabled all units"
                break
                ;;
            4)
                warn "skipped systemd enable — you can enable later with:"
                cmd "  systemctl --user enable --now rowbutt-agent.service"
                cmd "  systemctl --user enable --now rowbutt-aggregator.timer"
                break
                ;;
            *)
                echo "  Please enter 1, 2, 3, or 4."
                ;;
        esac
    done
else
    warn "systemd not available — skipping systemd install"
    warn "run the aggregator manually: rowbutt aggregator pull-all && rowbutt aggregator compute-costs"
fi

# ── 8. Summary ────────────────────────────────────────────────────────────
echo ""
printf "╔══════════════════════════════════════════════════════════╗\n"
printf "║  ${BOLD}${GREEN}Installation complete${NC}                                ║\n"
printf "╚══════════════════════════════════════════════════════════╝\n"
echo ""

# Check if rowbutt CLI works
if command -v rowbutt &>/dev/null; then
    ok "rowbutt CLI available: $(rowbutt --help 2>&1 | head -3 | tr '\n' ' ')"
else
    warn "'rowbutt' CLI not on PATH — run:"
    cmd "    export PATH=\"\$HOME/.rowbutt/venv/bin:\\\$PATH\""
    cmd "    # and add that line to ~/.bashrc"
fi

echo ""
echo "  ${BOLD}Quick reference:${NC}"
echo ""
echo "    ${CYAN}rowbutt agent start${NC}          Start the agent Flask daemon (port 5000)"
echo "    ${CYAN}rowbutt aggregator pull-all${NC}   Pull day-summaries from all agents"
echo "    ${CYAN}rowbutt aggregator compute-costs${NC}  Compute electricity + frontier costs"
echo "    ${CYAN}rowbutt report today --save${NC}   Generate and save today's report"
echo "    ${CYAN}rowbutt web start${NC}             Start the web UI (port 8123)"
echo ""

# Check key env
if [[ ! -f "$HOME/.rowbutt/agents.json" ]]; then
    warn "No agents.json found — until you configure it the aggregator can't pull data."
    cmd "  Edit: $HOME/.rowbutt/agents.json"
fi
