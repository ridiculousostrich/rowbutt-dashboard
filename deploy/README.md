# Rowbutt Dashboard — Deployment Guide

## Quick Install (bootstrap.sh)

```bash
# Clone the repo
git clone https://github.com/ridiculousostrich/rowbutt-dashboard.git
cd Rowbutt_Dashboard/deploy

# Run the installer (interactive)
bash bootstrap.sh
```

The bootstrap script:
1. Checks prerequisites (python3, venv)
2. Creates a venv at `~/.rowbutt/venv`
3. Installs all Python dependencies
4. Creates `~/.rowbutt/` runtime directories
5. Creates `~/.rowbutt/agents.json` (edit it with your agent URLs)
6. Installs systemd user units
7. **Prompts** which units to enable: agent only, aggregator only, both, or skip

> **Idempotent:** Safe to re-run — won't overwrite existing configs.

## Systemd Units

All units are **user-level** (`systemctl --user`), no sudo needed.

| Unit | Function |
|---|---|
| `rowbutt-agent.service` | Flask daemon (port 5000) — collects GPU/token metrics |
| `rowbutt-aggregator.service` | Runs `pull-all → compute-costs → report today --save` |
| `rowbutt-aggregator.timer` | Triggers aggregator daily at 23:55 |

```bash
# Enable manually (after bootstrap or later)
systemctl --user enable --now rowbutt-agent.service
systemctl --user enable --now rowbutt-aggregator.timer

# Check status
systemctl --user status rowbutt-agent.service --no-pager | head -10
systemctl --user status rowbutt-aggregator.timer --no-pager | head -10

# View logs
journalctl --user -u rowbutt-agent.service --since "24 hours ago" --no-pager
```

## Launcher Scripts

Convenience wrappers in `deploy/`:

```bash
# Start the agent (uses systemd; falls back to foreground)
bash deploy/start-agent.sh
bash deploy/start-agent.sh --foreground   # force foreground
bash deploy/start-agent.sh status         # check if running
bash deploy/start-agent.sh stop           # stop

# Run the aggregator
bash deploy/start-aggregator.sh           # enable timer + show status
bash deploy/start-aggregator.sh --now     # run pipeline immediately
bash deploy/start-aggregator.sh status    # check timer + last run
```

## Web UI

```bash
# Start the web UI (port 8123)
rowbutt web start --host 0.0.0.0 --port 8123
```

## Ansible (multi-machine)

For deploying the agent to every inference machine in your cluster:

```bash
# 1. Write an inventory
cat > inventory.ini <<'EOF'
[agents]
ubuntu-server ansible_host=192.168.1.52 ansible_user=root
operator-1  ansible_host=192.168.1.100 ansible_user=root

[coordinator]
ubuntu-server ansible_host=192.168.1.52 ansible_user=root
EOF

# 2. Run the playbook
ansible-playbook -i inventory.ini deploy/ansible/playbook.yaml
```

## Manual Commands (if not using systemd)

```bash
# Agent (foreground, port 5000)
rowbutt agent start --host 0.0.0.0 --port 5000

# Aggregator pipeline (one-shot)
rowbutt aggregator pull-all
rowbutt aggregator compute-costs
rowbutt report today --save --format markdown

# Web UI
rowbutt web start --host 0.0.0.0 --port 8123
```

## What Gets Installed Where

| Path | Purpose |
|---|---|
| `~/.rowbutt/venv/` | Python virtual environment |
| `~/.rowbutt/agent.db` | Agent's local SQLite DB (if agent enabled) |
| `~/.rowbutt/aggregator.db` | Aggregator's central SQLite DB |
| `~/.rowbutt/agents.json` | Agent registry (edit this!) |
| `~/.rowbutt/reports/` | Daily Markdown reports (when using `--save`) |
| `~/.config/systemd/user/` | User-level systemd units |
