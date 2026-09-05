# Rowbutt Dashboard

> **Distributed LLM cost monitoring.** Deploy a lightweight agent on each machine running local inference (Ollama, vLLM, llama.cpp). The agent collects token usage, GPU power draw, and system metrics. A central aggregator pulls daily summaries, computes electricity costs vs. frontier API pricing, and answers: *"What did this hobby cost me today?"*

---

## Architecture

```
┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│   Agent (port 5000)   │       │   Agent (port 5000)   │       │   Agent (port 5000)   │
│  ┌────────────────┐  │       │  ┌────────────────┐  │       │  ┌────────────────┐  │
│  │ LLM Token Collector│  │       │  │ LLM Token Collector│  │       │  │ LLM Token Collector│  │
│  │ System/GPU Collector│ │       │  │ System/GPU Collector│ │       │  │ System/GPU Collector│ │
│  │ SQLite store      │  │       │  │ SQLite store      │  │       │  │ SQLite store      │  │
│  └────────┬───────┘  │       │  └────────┬───────┘  │       │  └────────┬───────┘  │
│           │           │       │           │           │       │           │           │
│   GET /api/v1/        │       │   GET /api/v1/        │       │   GET /api/v1/        │
│   day-summary         │       │   day-summary         │       │   day-summary         │
│           │           │       │           │           │       │           │           │
└───────────┼───────────┘       └───────────┼───────────┘       └───────────┼───────────┘
            │                               │                               │
            └───────────────────────────────┼───────────────────────────────┘
                                            │
                                            ▼
                            ┌──────────────────────────┐
                            │     Aggregator (CLI)        │
                            │  ┌──────────────────────┐   │
                            │  │ pull-all → costs →    │   │
                            │  │ report today --save   │   │
                            │  └──────────┬───────────┘   │
                            │             │               │
                            │             ▼               │
                            │  ~/.rowbutt/reports/        │
                            │  YYYY-MM-DD.md              │
                            │             │               │
                            │             ▼               │
                            │  Telegram delivery (opt.)   │
                            └──────────┬──────────────────┘
                                       │
                                       ▼
                            ┌──────────────────────────┐
                            │  Web UI (:8123)            │
                            │  Browse historical reports │
                            └──────────────────────────┘
```

### Key Principles

| Principle | Why |
|-----------|-----|
| **Compute where the work happens** | Agents run on the inference machines — no extra load on the aggregator. |
| **No central credentials** | The aggregator doesn't need SSH keys or API tokens — it just makes HTTP requests to each agent. |
| **Offline-tolerant** | Each agent stores data locally in SQLite. If the aggregator is down, no data is lost. |
| **Daily cadence** | Real-time is noise. The value is in answering: "What did today's inference cost?" |
| **Primary output = report** | You want to know costs and savings, not watch charts tick over. |

---

## Quick Start

### Prerequisites

- Python 3.10+
- `pip` and `venv`
- On LLM machines: `nvidia-smi` available (for GPU metrics)
- On aggregator machine: network access to agent HTTP endpoints

### Option 1: Bootstrap Installer (Recommended)

```bash
cd deploy/
bash bootstrap.sh
```

The script will:
1. Check prerequisites
2. Create a virtual environment at `~/.rowbutt/venv/`
3. Install Python dependencies
4. Create runtime directories (`~/.rowbutt/`)
5. Create a default `~/.rowbutt/agents.json` (edit with your agent URLs)
6. Install systemd user units
7. Prompt which units to enable (agent, aggregator, both, or skip)

> Safe to re-run — won't overwrite existing configs.

### Option 2: Manual Install

```bash
# Create venv
python3 -m venv ~/.rowbutt/venv
source ~/.rowbutt/venv/bin/activate

# Install deps
pip install -r requirements.txt
pip install -e .    # editable install (lets you run `rowbutt`)

# Create runtime dirs
mkdir -p ~/.rowbutt/reports
```

---

## Deploy the Agent

Run this on **each machine** that runs local LLM inference.

### Start the Agent

```bash
# One-shot (foreground)
rowbutt agent init
rowbutt agent start --host 0.0.0.0 --port 5000

# Or via systemd
bash deploy/start-agent.sh
```

The agent will:
- Poll local LLM endpoints (Ollama enabled by default) for token usage
- Collect GPU power draw, temperatures, and system memory via `nvidia-smi`
- Store everything in `~/.rowbutt/agent.db` (SQLite)
- Expose a day-summary API at `GET /api/v1/day-summary`

### Check Agent Status

```bash
rowbutt agent status
```

---

## Deploy the Aggregator

Run this on **one central machine** (can be the same as an agent host, or a separate box).

### Configure Agents

Edit `~/.rowbutt/agents.json`:

```json
{
  "_comment": "Rowbutt Dashboard — agent registry.",
  "agents": [
    {
      "hostname": "ubuntu-server",
      "url": "http://192.168.1.52:5000",
      "description": "Main inference server"
    },
    {
      "hostname": "operator-1",
      "url": "http://192.168.1.100:5000",
      "description": "Operator machine (RTX 5070)"
    }
  ]
}
```

### Run the Pipeline

```bash
# Initialize the aggregator DB
rowbutt aggregator init

# Pull data from all agents
rowbutt aggregator pull-all

# Compute electricity + frontier costs
rowbutt aggregator compute-costs

# Generate today's report
rowbutt report today
```

### Automate with Systemd

```bash
bash deploy/start-aggregator.sh     # enable timer + show status
bash deploy/start-aggregator.sh --now   # run pipeline immediately
```

The timer triggers daily at **23:55**.

---

## CLI Reference

### `rowbutt agent`

| Command | Description |
|---------|-------------|
| `agent init` | Initialize agent database and config directory |
| `agent start` | Start the agent poll loop and HTTP server |
| `agent status` | Show current agent collection status |
| `agent day-summary --date YYYY-MM-DD` | Fetch a day summary from the local DB |

### `rowbutt aggregator`

| Command | Description |
|---------|-------------|
| `aggregator init` | Initialize the aggregator database |
| `aggregator pull-all --date YYYY-MM-DD` | Pull day-summaries from all configured agents |
| `aggregator compute-costs --hostname <name> --date YYYY-MM-DD` | Compute electricity and frontier costs |

### `rowbutt report`

| Command | Description |
|---------|-------------|
| `report today` | Generate today's cost report |
| `report date YYYY-MM-DD` | Report for a specific date |
| `report week` | 7-day summary |
| `report month` | Month-to-date summary |
| `report list` | List available report dates |

All report commands support:
- `--format markdown|json|csv`
- `--save` — save to `~/.rowbutt/reports/`
- `--deliver telegram` — send via Telegram (requires Hermes `telegram-send` skill)

### `rowbutt web`

| Command | Description |
|---------|-------------|
| `web start --host 0.0.0.0 --port 8123` | Start the web UI |

---

## Web UI

Start a lightweight Flask server to browse historical reports:

```bash
rowbutt web start --host 0.0.0.0 --port 8123
```

Endpoints:

| Route | Description |
|-------|-------------|
| `GET /` | Landing page with latest report + date list |
| `GET /report/<date>` | Full report rendered as HTML |
| `GET /api/v1/reports` | JSON list of available dates |
| `GET /api/v1/report/<date>` | JSON of the full report data |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ROWBUTT_AGENT_HOST` | `0.0.0.0` | Agent HTTP server bind address |
| `ROWBUTT_AGENT_PORT` | `5000` | Agent HTTP server port |
| `ROWBUTT_SYSTEM_INTERVAL` | `60` | System/GPU poll interval (seconds) |
| `ROWBUTT_LLM_INTERVAL` | `300` | LLM endpoint poll interval (seconds) |
| `ROWBUTT_POWER_COST` | `0.12` | Electricity cost ($/kWh) |
| `ROWBUTT_AGENT_HOSTNAME` | *(hostname)* | Override agent hostname |

### LLM Endpoints (per-agent)

The agent discovers enabled LLM endpoints from `config/defaults.py`:

| Endpoint | Port | Enabled by Default |
|----------|------|-------------------|
| Ollama | `11434` | ✅ Yes |
| vLLM | `8000` | ❌ No |
| llama.cpp | `8080` | ❌ No |

To enable an endpoint, set the corresponding URL or override via environment/collector config.

### Agent Config File

Agent runtime data lives under `~/.rowbutt/`:

| Path | Purpose |
|------|---------|
| `~/.rowbutt/agent.db` | Agent-local SQLite database (token events, system samples) |
| `~/.rowbutt/aggregator.db` | Central aggregator database (summaries, costs, pricing cache) |
| `~/.rowbutt/agents.json` | Agent registry for the aggregator (edit with your agent URLs) |
| `~/.rowbutt/reports/` | Daily Markdown reports |

---

## Multi-Machine Deployment (Ansible)

For deploying the agent to multiple machines at once:

```bash
# 1. Create an inventory
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

---

## What Gets Collected

| Metric | Source | Collection Interval |
|--------|--------|-------------------|
| Token usage (per model) | Ollama / vLLM / llama.cpp API | Every 5 minutes |
| GPU power draw | `nvidia-smi` | Every 60 seconds |
| GPU temperature | `nvidia-smi` | Every 60 seconds |
| GPU utilization | `nvidia-smi` | Every 60 seconds |
| System memory | `/proc/meminfo` | Every 60 seconds |
| CPU temperatures | `sensors` / thermal sysfs | Every 60 seconds |
| CPU load | `/proc/loadavg` | Every 60 seconds |

## What Gets Computed

| Metric | Inputs | Output |
|--------|--------|--------|
| Electricity cost | GPU power + system baseline × $0.12/kWh × hours | $ spent on power |
| Frontier cost | Total tokens × current API pricing (OpenAI, Anthropic, etc.) | $ same tokens would cost from cloud providers |
| Savings | Frontier cost − Electricity cost | $ saved by running local |
| Per-model breakdown | Tokens grouped by model | Cost per model, workload distribution |

---

## Adding Collectors

The agent uses a collector plugin pattern. New collectors go in `agent/collectors/`:

```python
# agent/collectors/my_collector.py
from agent.collectors.base import BaseCollector

class MyCollector(BaseCollector):
    name = "my_collector"

    def poll(self) -> dict:
        # Return a dict of metrics
        return {"my_metric": 42}
```

The collector is auto-discovered via the `collectors` module.

---

## Reading Reports

Daily reports are saved as Markdown to `~/.rowbutt/reports/YYYY-MM-DD.md`:

```markdown
# Daily Cost Report — 2026-08-23

## Summary
| Metric | Value |
|--------|-------|
| Total tokens | 2,750 |
| Electricity cost | $0.42 |
| Frontier API cost | $1.85 |
| Savings | $1.43 |

## Per-Machine Breakdown
| Machine | Tokens | GPU Power | Electricity | Frontier Cost | Savings |
|---------|--------|-----------|-------------|---------------|---------|
| ubuntu-server | 2,750 | 185W | $0.42 | $1.85 | $1.43 |

## Savings Over Time
| Interval | Electricity | Frontier Cost | Savings |
|----------|-------------|---------------|---------|
| Today | $0.42 | $1.85 | $1.43 |
| This Week | $3.15 | $12.92 | $9.77 |
| This Month | $14.80 | $58.40 | $43.60 |
```

---

## Development

### Run Tests

```bash
# Phase 0 — smoke test (DB init, schema, basic CRUD)
python3 tests/test_phase0_smoke.py

# Phase 1 — integration (agent collectors, API server, scheduler)
python3 tests/test_phase1_integration.py

# Phase 2 — aggregation pipeline (puller, costs, reporter)
python3 tests/test_phase2_integration.py

# Phase 4 — web UI
python3 tests/test_phase4_web.py
```

All tests use temporary databases and won't touch production data.

### Project Structure

```
Rowbutt_Dashboard/
├── agent/                  # Per-machine data collection agent
│   ├── collectors/         # Collector plugins (llm_tokens, system)
│   ├── cli.py              # Agent CLI entry point
│   ├── scheduler.py        # Poll loop scheduler
│   └── server.py           # Flask HTTP server
├── aggregator/             # Central cost aggregation engine
│   ├── cli.py              # Aggregator CLI commands
│   ├── costs.py            # Electricity + frontier cost calculator
│   ├── puller.py           # HTTP client for agent day-summaries
│   └── report.py           # Markdown/JSON/CSV report generator
├── cli/                    # Root CLI
│   ├── main.py             # Click entry point
│   └── commands.py         # All subcommand implementations
├── config/                 # Default configuration
├── db/                     # Database helpers and migrations
├── deploy/                 # Deployment scripts
│   ├── bootstrap.sh        # One-command installer
│   ├── start-agent.sh      # Agent launcher
│   ├── start-aggregator.sh # Aggregator launcher
│   ├── ansible/            # Ansible playbook for multi-machine deploy
│   └── *.service/.timer    # systemd user units
├── docs/                   # Planning and design documents
├── tests/                  # Integration tests
├── web/                    # Flask web UI
│   └── templates/          # Jinja2 templates
└── requirements.txt        # Python dependencies
```

---

## License

MIT
