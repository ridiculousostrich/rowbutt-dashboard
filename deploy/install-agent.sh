#!/usr/bin/env bash
# ─── Rowbutt Agent — Standalone Installer ────────────────────────────
# Fetches and installs ONLY the agent package (not aggregator/web/tests).
# Works locally (from deploy/ in the repo) or remotely (curl-piped).
#
# Usage:
#   bash deploy/install-agent.sh              # local mode
#   curl -sL https://git.io/... | bash        # remote mode
#   curl -sL https://... | bash -s -- --repo-url https://my-mirror/tarball
#
# Environment:
#   ROWBUTT_REPO_URL   — override the repo URL for fetching the agent tarball
#
# ──────────────────────────────────────────────────────────────────────────

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

STEP=1
info()  { printf "${BOLD}${GREEN}[%s]${NC} %s\n" "$STEP" "$1"; ((STEP++)); }
warn()  { printf "${YELLOW}⚠  %s${NC}\n" "$1"; }
fail()  { printf "${RED}✗  %s${NC}\n" "$1"; exit 1; }
ok()    { printf "  ${GREEN}✓${NC}  %s\n" "$1"; }
cmd()   { printf "  ${CYAN}→${NC}  %s\n" "$1"; }

# ── Config ────────────────────────────────────────────────────────────────

REPO_URL="${ROWBUTT_REPO_URL:-https://github.com/ridiculousostrich/rowbutt-dashboard}"
VENV_DIR="$HOME/.rowbutt/venv"
AGENT_DIR="$HOME/.rowbutt/agent-package"
SYSTEMD_DIR="$HOME/.config/systemd/user"

# ── Detect mode ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-}")" 2>/dev/null && pwd)"

LOCAL_MODE=false
if [[ -d "$SCRIPT_DIR/../agent" ]] && [[ -f "$SCRIPT_DIR/../pyproject.toml" ]]; then
    LOCAL_MODE=true
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    info "Running in local mode (source: $PROJECT_DIR)"
else
    info "Running in remote mode (source: $REPO_URL)"
fi

# ── 1. Prerequisites ──────────────────────────────────────────────────────

info "Checking prerequisites"

if ! command -v python3 &>/dev/null; then
    fail "python3 is required but not found. Install it first."
fi
ok "python3 found: $(python3 --version 2>&1)"

if ! python3 -c "import venv" &>/dev/null; then
    fail "python3-venv is required. Install it: apt install python3-venv"
fi
ok "python3-venv available"

if ! command -v pip3 &>/dev/null && ! python3 -m pip --version &>/dev/null; then
    fail "pip3 is required. Install it: apt install python3-pip"
fi
ok "pip available"

# ── 2. Get the agent package files ─────────────────────────────────────────

info "Fetching agent package files"

if $LOCAL_MODE; then
    # Build a minimal agent-only tarball from the local repo
    TMPDIR=$(mktemp -d)
    mkdir -p "$TMPDIR/rowbutt-agent"

    # Copy only agent-relevant paths (no aggregator, web, tests, docs)
    cp -r "$PROJECT_DIR/agent"       "$TMPDIR/rowbutt-agent/agent"
    cp -r "$PROJECT_DIR/cli"         "$TMPDIR/rowbutt-agent/cli"
    cp -r "$PROJECT_DIR/config"      "$TMPDIR/rowbutt-agent/config"
    cp -r "$PROJECT_DIR/db"          "$TMPDIR/rowbutt-agent/db"
    cp    "$PROJECT_DIR/pyproject.toml"   "$TMPDIR/rowbutt-agent/pyproject.toml"
    cp    "$PROJECT_DIR/requirements.txt" "$TMPDIR/rowbutt-agent/requirements.txt"

    # Systemd unit + launcher
    mkdir -p "$TMPDIR/rowbutt-agent/deploy"
    cp "$PROJECT_DIR/deploy/rowbutt-agent.service" "$TMPDIR/rowbutt-agent/deploy/"
    cp "$PROJECT_DIR/deploy/start-agent.sh"        "$TMPDIR/rowbutt-agent/deploy/"

    SOURCE_DIR="$TMPDIR/rowbutt-agent"
    ok "Files assembled from local repo"
else
    # Fetch from GitHub — use raw.githubusercontent.com for direct file access
    # (avoids codeload redirect which can hang on some networks)
    TMPDIR=$(mktemp -d)
    RAW_BASE="https://raw.githubusercontent.com/ridiculousostrich/rowbutt-dashboard/main"
    AGENT_FILES="$TMPDIR/rowbutt-agent"
    mkdir -p "$AGENT_FILES/agent" "$AGENT_FILES/cli" "$AGENT_FILES/config" "$AGENT_FILES/db" "$AGENT_FILES/deploy"

    info "Downloading from $REPO_URL..."

    download_file() {
        local url="$1" outdir="$2"
        curl -sSfL --connect-timeout 10 --max-time 30 "$url" -o "$outdir" 2>/dev/null
    }

    # Download agent package files individually
    download_file "$RAW_BASE/agent/__init__.py"               "$AGENT_FILES/agent/__init__.py" || true
    download_file "$RAW_BASE/agent/cli.py"                    "$AGENT_FILES/agent/cli.py" || true
    download_file "$RAW_BASE/agent/scheduler.py"              "$AGENT_FILES/agent/scheduler.py" || true
    download_file "$RAW_BASE/agent/server.py"                 "$AGENT_FILES/agent/server.py" || true
    download_file "$RAW_BASE/cli/main.py"                     "$AGENT_FILES/cli/main.py" || true
    download_file "$RAW_BASE/cli/commands.py"                 "$AGENT_FILES/cli/commands.py" || true
    download_file "$RAW_BASE/config/defaults.py"              "$AGENT_FILES/config/defaults.py" || true
    download_file "$RAW_BASE/db/db_common.py"                 "$AGENT_FILES/db/db_common.py" || true
    download_file "$RAW_BASE/db/migrations.py"                "$AGENT_FILES/db/migrations.py" || true
    download_file "$RAW_BASE/db/schema_agent.sql"             "$AGENT_FILES/db/schema_agent.sql" || true
    download_file "$RAW_BASE/db/schema_aggregator.sql"        "$AGENT_FILES/db/schema_aggregator.sql" || true
    download_file "$RAW_BASE/pyproject.toml"                  "$AGENT_FILES/pyproject.toml" || true
    download_file "$RAW_BASE/requirements.txt"                "$AGENT_FILES/requirements.txt" || true
    download_file "$RAW_BASE/deploy/rowbutt-agent.service"    "$AGENT_FILES/deploy/rowbutt-agent.service" || true
    download_file "$RAW_BASE/deploy/start-agent.sh"           "$AGENT_FILES/deploy/start-agent.sh" || true

    # Download agent/collectors/
    mkdir -p "$AGENT_FILES/agent/collectors"
    for f in __init__.py base.py llm_tokens.py system.py; do
        download_file "$RAW_BASE/agent/collectors/$f" "$AGENT_FILES/agent/collectors/$f" || true
    done

    SOURCE_DIR="$AGENT_FILES"

    # Verify we got the essential files
    if [[ ! -f "$SOURCE_DIR/agent/server.py" ]]; then
        warn "Direct download failed — trying archive fallback..."
        mkdir -p "$TMPDIR/archive"
        if curl -sL --connect-timeout 10 --max-time 60 \
            "https://codeload.github.com/ridiculousostrich/rowbutt-dashboard/tar.gz/refs/heads/main" \
            -o "$TMPDIR/repo.tar.gz"; then
            TOPDIR=$(tar tzf "$TMPDIR/repo.tar.gz" 2>/dev/null | head -1 | cut -d/ -f1)
            if [[ -n "$TOPDIR" ]]; then
                mkdir -p "$TMPDIR/rowbutt-agent2"
                for path in \
                    "$TOPDIR/agent" \
                    "$TOPDIR/cli" \
                    "$TOPDIR/config" \
                    "$TOPDIR/db" \
                    "$TOPDIR/pyproject.toml" \
                    "$TOPDIR/requirements.txt" \
                    "$TOPDIR/deploy/rowbutt-agent.service" \
                    "$TOPDIR/deploy/start-agent.sh"; do
                    tar xzf "$TMPDIR/repo.tar.gz" -C "$TMPDIR/rowbutt-agent2" \
                        --strip-components=1 "$path" 2>/dev/null || true
                done
                SOURCE_DIR="$TMPDIR/rowbutt-agent2"
            fi
        fi
    fi

    if [[ ! -f "$SOURCE_DIR/agent/server.py" ]]; then
        fail "Failed to download agent files. Check network connectivity to GitHub."
    fi
    ok "Agent files downloaded from $REPO_URL"
fi

# ── 3. Install into permanent directory ────────────────────────────────────

info "Installing to $AGENT_DIR"

# Remove previous installation if any
rm -rf "$AGENT_DIR"
mkdir -p "$AGENT_DIR"
cp -r "$SOURCE_DIR"/* "$AGENT_DIR/"
ok "Copied to $AGENT_DIR"

# ── 4. Create virtual environment ──────────────────────────────────────────

info "Creating virtual environment"

python3 -m venv --clear "$VENV_DIR"
source "$VENV_DIR/bin/activate"

cmd "pip install --upgrade pip"
pip install --upgrade pip 2>/dev/null

cmd "pip install -r $AGENT_DIR/requirements.txt"
pip install -r "$AGENT_DIR/requirements.txt"

cmd "pip install -e $AGENT_DIR"
pip install -e "$AGENT_DIR"

# Verify the CLI entry point is available
if ! "$VENV_DIR/bin/rowbutt" --help &>/dev/null; then
    fail "CLI entry point not found after install. Something went wrong."
fi
ok "Virtual environment ready at $VENV_DIR"

# ── 5. Create runtime directories ──────────────────────────────────────────

info "Creating runtime directories"

mkdir -p "$HOME/.rowbutt/reports"
ok "$HOME/.rowbutt/ ready"

# ── 6. Initialise agent database ────────────────────────────────────────────

info "Initialising agent database"

if "$VENV_DIR/bin/rowbutt" agent init &>/dev/null; then
    ok "Agent database initialised"
else
    warn "Agent init had issues — check ~/.rowbutt/agent.db"
fi

# ── 7. Install systemd user unit ───────────────────────────────────────────

info "Installing systemd user unit"

mkdir -p "$SYSTEMD_DIR"

if [[ -f "$AGENT_DIR/deploy/rowbutt-agent.service" ]]; then
    cp "$AGENT_DIR/deploy/rowbutt-agent.service" "$SYSTEMD_DIR/rowbutt-agent.service"

    if command -v systemctl &>/dev/null; then
        # Ensure a user systemd session exists (needed on headless/SSH machines)
        if ! systemctl --user daemon-reload &>/dev/null; then
            warn "No user systemd bus found — enabling lingering session"
            loginctl enable-linger "$USER" 2>/dev/null || true
            systemctl --user daemon-reload 2>/dev/null || true
        fi
        ok "systemd unit installed: $SYSTEMD_DIR/rowbutt-agent.service"
    else
        ok "systemd unit copied (systemctl not available — will need manual start)"
    fi
else
    warn "rowbutt-agent.service not found — skipping systemd setup"
fi

# ── 8. Start the agent ─────────────────────────────────────────────────────

info "Starting the agent"

AGENT_CMD="$VENV_DIR/bin/rowbutt agent start --host 0.0.0.0 --port 5000"
STARTED=false

if command -v systemctl &>/dev/null; then
    if systemctl --user enable --now rowbutt-agent.service &>/dev/null; then
        ok "Agent started via systemd"
        systemctl --user status rowbutt-agent.service --no-pager 2>&1 | head -5
        STARTED=true
    fi
fi

if ! $STARTED; then
    warn "systemd not available — starting agent directly"
    warn "Agent running in background. Check logs at ~/.rowbutt/agent.log"
    nohup "$VENV_DIR/bin/rowbutt" agent start --host 0.0.0.0 --port 5000 \
        > "$HOME/.rowbutt/agent.log" 2>&1 &
    echo "$!" > "$HOME/.rowbutt/agent.pid"
    echo "  PID: $(cat "$HOME/.rowbutt/agent.pid")"
    ok "Agent started in background (PID: $(cat "$HOME/.rowbutt/agent.pid"))"
fi

# ── 9. Summary ─────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD} Rowbutt Agent Installed Successfully${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "  Agent package:  $AGENT_DIR"
echo "  Virtual env:    $VENV_DIR"
echo "  Runtime data:   $HOME/.rowbutt/"
echo "  Systemd unit:   $SYSTEMD_DIR/rowbutt-agent.service"
echo ""
echo -e "${BOLD}Quick commands:${NC}"
echo "  Status:   systemctl --user status rowbutt-agent.service"
echo "  Logs:     journalctl --user -u rowbutt-agent.service --since '1 hour ago'"
echo "  Restart:  systemctl --user restart rowbutt-agent.service"
echo "  Stop:     systemctl --user stop rowbutt-agent.service"
echo "  Manual:   $VENV_DIR/bin/rowbutt agent start"
echo ""
echo -e "${BOLD}Configure agents on the aggregator:${NC}"
echo "  Edit ~/.rowbutt/agents.json with this machine's URL:"
echo "    http://<this-ip>:5000"
echo ""

# Cleanup
rm -rf "$TMPDIR"
