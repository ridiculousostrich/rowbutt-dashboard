# Rowbutt Dashboard — ROADMAP

> **Project:** Distributed monitoring project that deploys a lightweight agent onto each machine hosting LLM endpoints. The agent collects local token usage and system metrics, then the central aggregator pulls daily summaries to compute operating costs, electricity spend, and savings vs. frontier API pricing.
>
> **Tagline:** _"What did this hobby cost me today?"_

---

## Next Steps

**Phase 1 is deployed** — agent running on `gx10-e587` (port 5000), packages published to GitHub.

**Phase 2+3+4 is deployed** — aggregator + web UI running on the Hermes container (192.168.1.57:8123). Systemd timer fires daily at 23:55 UTC.

The immediate next action is **live user testing** — let the pipeline run overnight, then inspect the report at `http://192.168.1.57:8123`.

---

## 1. Project Description

**Rowbutt Dashboard** is a distributed monitoring project. Unlike a typical dashboard that pulls data centrally via SSH or agent push, this project puts a lightweight **agent** on each machine that already runs LLM inference. That agent:

- Polls **local** LLM processes (Ollama, Open WebUI, vLLM, etc.) for per-session token counts — no API tokens to manage centrally, no SSH keys.
- Polls **local** system metrics (memory, CPU/GPU temperatures, GPU power draw via `nvidia-smi`) — the data is already there.
- Stores everything in a **local SQLite database** — zero-config, no network dependency.
- Exposes a **simple HTTP endpoint** so the central aggregator can fetch day-summaries (JSON) on demand.

The **central aggregator** is a separate process that:

- Reaches out to each agent once per day (or on demand) to pull the previous 24 hours of collected data.
- Computes cost metrics: electricity spend (configurable $/kWh + real GPU power), frontier API cost (current pricing cache), and savings.
- Generates a **daily cost report** — a concise summary of what the hobby cost/saved over the last 24-hour period.
- Optionally delivers the report via Telegram, a local file, or a minimal web view.

### Why This Design

| Principle | Why |
|-----------|-----|
| **Compute where the work happens** | The machines doing LLM inference also do the data collection — no extra load on the aggregator. |
| **No central credentials** | The aggregator doesn't need SSH keys or API tokens for any machine. It just makes HTTP requests to each agent. |
| **Offline-tolerant agents** | Each agent stores data locally. If the aggregator is down, no data is lost. |
| **Daily cadence** | Real-time is noise. The value is in answering: "What did today's inference cost?" |
| **Primary output = report** | The user wants to know costs and savings, not watch charts tick over. |

### What Gets Collected Per Machine

| Metric | Source | Collection |
|--------|--------|------------|
| **Token usage** | Local LLM API (Ollama, Open WebUI, etc.) | Agent polls every N minutes, records per-session |
| **System memory** | `/proc/meminfo` | Agent polls every N minutes |
| **CPU temps** | `sensors`, `/sys/class/thermal/` | Agent polls every N minutes |
| **GPU data** | `nvidia-smi` (temp, power.draw, utilization) | Agent polls every N minutes |
| **Power draw** | `nvidia-smi` GPU power + configured CPU/system baseline | Combined for electricity cost calc |

### What Gets Computed Per Day

| Metric | Inputs | Output |
|--------|--------|--------|
| **Electricity cost** | Total inference time × (GPU power + system baseline) × $0.11/kWh | $ spent on power for inference |
| **Frontier cost** | Total tokens × current API prices per model | $ the same tokens would cost from OpenAI, Anthropic, etc. |
| **Savings** | Frontier cost − Electricity cost | $ saved by running local |
| **Per-model breakdown** | Tokens grouped by model | Cost per model, workload distribution |

---

## 2. Feature Set (Epic-Level)

### Must-Have (MVP)

- [ ] **Per-machine agent (`rowbutt-agent`)** — a lightweight Python script that:
  - Polls local LLM endpoints (Ollama API, Open WebUI API, generic OpenAI-compatible) for token counts
  - Polls local system metrics (`/proc/meminfo`, `sensors`, `nvidia-smi`)
  - Stores raw samples and 4-hour rollups in a local SQLite DB
  - Serves a simple HTTP endpoint: `GET /api/v1/day-summary?date=YYYY-MM-DD` → JSON
  - Runs as a standalone daemon or systemd service
- [ ] **Central aggregator (`rowbutt-aggregator`)** — a script that:
  - Reads a config file listing agent URLs (e.g., `http://192.168.1.52:5000`)
  - Calls each agent's `/api/v1/day-summary` endpoint once per day
  - Merges data into a central SQLite DB
  - Computes electricity cost, frontier cost, and savings
  - Produces a daily cost report
- [ ] **Daily cost report** — output as Markdown (and optionally Telegram):
  - Per-machine token usage & model breakdown
  - Per-machine electricity cost estimate
  - Frontier API cost comparison (what the same tokens would cost at OpenAI/Anthropic/etc.)
  - Total savings for the day
- [ ] **Pricing cache** — a daily-updated table of frontier model prices (input + output per 1M tokens)
- [ ] **SQLite schema** — agent-local DB tables (`token_events`, `system_samples`) + central DB tables (`daily_summaries`, `pricing_cache`, `cost_reports`)
- [ ] **CLI** — `rowbutt agent --init`, `rowbutt agent --start`, `rowbutt aggregator --pull`, `rowbutt aggregator --report-today`

### Should-Have (Phase 2)

- [ ] **Historical reports** — query past days via CLI: `rowbutt report --date 2026-08-23 --format markdown|json|csv`
- [ ] **Per-machine config** — each agent configures its own power baseline (system watts + GPU idle/load watts)
- [ ] **Real GPU power** — if `nvidia-smi power.draw` is available, use real values instead of defaults
- [ ] **Multi-model frontier pricing** — compare savings against multiple providers simultaneously

### Nice-to-Have (Phase 3)

- [ ] **Web view** — static HTML page served by the aggregator showing the latest report and historical browse
- [ ] **Telegram delivery** — daily report auto-sent via the existing Telegram script
- [ ] **Agent auto-discovery** — agents broadcast presence on the LAN via mDNS/SSDP
- [ ] **Docker deployment** — `docker-compose up` with agent + aggregator as separate containers
- [ ] **Prometheus exporter** — expose agent metrics in Prometheus format for the existing stack

---

## 3. Architecture Overview

### High-Level Data Flow

```
 ┌─────────────────────────────────────────┐
 │         Machine A: ubuntu-server         │
 │  (192.168.1.52  —  Ollama + Open WebUI) │
 │                                          │
 │  ┌─────────────────────────────────┐     │
 │  │     rowbutt-agent (port 5000)    │     │
 │  │                                  │     │
 │  │  ┌──────────┐  ┌─────────────┐ │     │
 │  │  │ LLM Poller│  │ System      │ │     │
 │  │  │ (Ollama   │  │ Metrics     │ │     │
 │  │  │  API)     │  │ (/proc,     │ │     │
 │  │  │          │  │  sensors,   │ │     │
 │  │  │ Open     │  │  nvidia-smi)│ │     │
 │  │  │ WebUI    │  └──────┬──────┘ │     │
 │  │  │ API)     │         │        │     │
 │  │  └────┬─────┘         │        │     │
 │  │       │               │        │     │
 │  │       ▼               ▼        │     │
 │  │  ┌──────────────────────────┐  │     │
 │  │  │  Local SQLite DB          │  │     │
 │  │  │  ~/.rowbutt/agent.db     │  │     │
 │  │  │  tables:                  │  │     │
 │  │  │   token_events            │  │     │
 │  │  │   system_samples          │  │     │
 │  │  │   daily_rollups           │  │     │
 │  │  └──────────────────────────┘  │     │
 │  │                                │     │
 │  │  GET /api/v1/day-summary       │     │
 │  │  → returns JSON of today's     │     │
 │  │    token + metrics data        │     │
 │  └──────────┬──────────────────────┘     │
 └─────────────┼────────────────────────────┘
               │ HTTP (outbound from aggregator)
               ▼
 ┌─────────────────────────────────────────┐
 │      Machine B: GX10 / other LLM host   │
 │  (rowbutt-agent on port 5000)           │
 │  ...same agent design, different host   │
 └──────────────────┬──────────────────────┘
                    │ HTTP
                    ▼
 ┌─────────────────────────────────────────┐
 │   Central Aggregator (Proxmox container) │
 │                                          │
 │  ┌──────────────────────────────────┐    │
 │  │  rowbutt-aggregator              │    │
 │  │                                  │    │
 │  │  1. Pull /day-summary from each  │    │
 │  │     agent at end of day          │    │
 │  │  2. Compute electricity cost     │    │
 │  │     (tokens × power × $0.11)     │    │
 │  │  3. Compute frontier cost        │    │
 │  │     (tokens × cached API prices) │    │
 │  │  4. Compute savings delta        │    │
 │  │  5. Write cost_reports table     │    │
 │  │  6. Generate Markdown report     │    │
 │  └──────────┬───────────────────────┘    │
 │             │                             │
 │             ▼                             │
 │  ┌──────────────────────────────────┐    │
 │  │  Central SQLite DB               │    │
 │  │  ~/.rowbutt/aggregator.db       │    │
 │  │  tables:                         │    │
 │  │   daily_summaries                │    │
 │  │   pricing_cache                  │    │
 │  │   cost_reports                   │    │
 │  └──────────────────────────────────┘    │
 │                                          │
 │  ┌──────────────────────────────────┐    │
 │  │  Output:                         │    │
 │  │  • ~/.rowbutt/reports/today.md   │    │
 │  │  • Telegram message (optional)   │    │
 │  │  • CLI: rowbutt report --today   │    │
 │  └──────────────────────────────────┘    │
 └──────────────────────────────────────────┘

                   24h cycle

 ┌──────────────────────────────────────────┐
 │  Daily Cron (central aggregator host)    │
 │                                          │
 │  23:50 — rowbutt-aggregator pull-all     │
 │          (hits each agent, fetches data)  │
 │  23:55 — rowbutt-aggregator compute-costs│
 │          (electricity + frontier + save) │
 │  23:58 — rowbutt report --today          │
 │          (generates Markdown, delivers)  │
 └──────────────────────────────────────────┘

 ┌──────────────────────────────────────────┐
 │  Weekly Cron (central aggregator host)   │
 │                                          │
 │  Sun 09:00 — rowbutt report --week       │
 │          (7-day summary report)          │
 └──────────────────────────────────────────┘
```

### Agent HTTP API

The per-machine agent exposes a minimal REST API:

```
GET /health                   → {"status": "ok", "hostname": "...", "uptime": "..."}
GET /api/v1/status            → {"collecting": true, "samples_today": 42, "last_poll": "..."}
GET /api/v1/day-summary?date=2026-08-23
  → {
      "hostname": "ubuntu-server",
      "date": "2026-08-23",
      "token_usage": {
        "ollama": {
          "deepseek-ai/DeepSeek-V4-Flash": {
            "input_tokens": 1200000,
            "output_tokens": 850000,
            "total_tokens": 2050000,
            "session_count": 47
          }
        }
      },
      "system_metrics": {
        "avg_mem_pct": 68.5,
        "avg_cpu_temp_c": 62.3,
        "avg_gpu_temp_c": 71.0,
        "avg_gpu_power_w": 185,
        "avg_system_power_w": 210,
        "inference_time_minutes": 320
      },
      "machine_info": {
        "hostname": "ubuntu-server",
        "gpu_model": "NVIDIA RTX 3060",
        "cpu_model": "AMD Ryzen 5 ...",
        "total_ram_gb": 64
      }
    }
GET /api/v1/timeseries?metric=tokens&from=...&to=...
  → { ... bucket-level time series data ... }
```

No authentication on the agent API by default — it binds to the LAN interface with an optional configurable port. Since the data is local/operational (no secrets), this is acceptable. Firewall rules can restrict access if needed.

---

## 4. Development Plan — Phases

### Phase 0 — Project Skeleton & Shared Schema ✓ COMPLETED

**Objective:** Set up the repo structure, design the dual-side SQLite schema, and verify the DB layer works for both agent and aggregator.

**Tasks:**

1. [x] Create project directory structure:
   ```
   Rowbutt_Dashboard/
   ├── README.md
   ├── ROADMAP.md
   ├── requirements.txt
   ├── config/
   │   └── defaults.py
   ├── db/
   │   ├── schema_agent.sql      -- agent-local DB tables
   │   ├── schema_aggregator.sql  -- central DB tables
   │   ├── db_common.py           -- shared connection helpers
   │   └── migrations.py
   ├── agent/
   │   ├── __init__.py
   │   ├── cli.py                 -- Real agent runner (Flask + scheduler + collectors)
   │   ├── collectors/
   │   │   ├── __init__.py         -- Auto-imports all collectors for registration
   │   │   ├── base.py             -- Abstract Collector + CollectResult + registry
   │   │   ├── llm_tokens.py      -- Ollama, vLLM, llama.cpp Prometheus pollers + DiffTracker
   │   │   └── system.py          -- psutil + nvidia-smi + sensors collector
   │   ├── server.py              -- Flask HTTP server (health, agent-info, day-summary)
   │   └── scheduler.py           -- Background poll loop driving collectors
   ├── aggregator/
   │   ├── __init__.py
   │   ├── cli.py                -- CLI entry points (pull, compute, report)
   │   ├── puller.py             -- HTTP client, AgentConfigStore, pull_all_agents
   │   ├── costs.py              -- Electricity + frontier cost calculation engine
   │   └── report.py             -- Markdown daily/weekly report generator
   ├── cli/
   │   ├── main.py               -- root CLI entry point ✓
   │   └── commands.py           -- all subcommands scaffolded ✓
   └── scripts/
       ├── start-agent.sh
       ├── start-aggregator.sh
       └── bootstrap.sh
   ```
2. [x] Design **agent-local DB schema** (see §5):
   - `token_events` — raw per-session token records
   - `system_samples` — raw per-poll system metric records
   - `daily_rollups` — pre-computed 4-hour bucket summaries
   - `agent_meta` — agent metadata singleton
3. [x] Design **central DB schema** (see §5):
   - `daily_summaries` — one row per machine per day with aggregated token + system data
   - `pricing_cache` — daily snapshot of frontier model prices (11 models seeded)
   - `cost_reports` — computed costs per machine per day
   - `aggregator_meta` — aggregator metadata singleton
4. [x] Write both schemas as SQL files
5. [x] Write `db_common.py` — shared connection management, init helper
6. [x] Write `migrations.py` — version-tracked migration runner
7. [x] Write smoke test — **26/26 tests passed**
8. [x] Set up CLI scaffold with `click` — root `rowbutt` command, `agent`, `aggregator`, and `report` subcommand groups
9. [x] Write shell scripts (bootstrap, start-agent, start-aggregator)

**Deliverable:** Both SQLite databases working, CLI scaffold in place, project tree clean. Files created:

| File | Status |
|------|--------|
| `db/schema_agent.sql` | ✓ 4 tables, indexes, metadata |
| `db/schema_aggregator.sql` | ✓ 4 tables, 11 seeded prices, indexes |
| `db/db_common.py` | ✓ connection mgmt, init helpers |
| `db/migrations.py` | ✓ version-tracked runner framework |
| `config/defaults.py` | ✓ paths, power defaults, collector defaults |
| `cli/main.py` | ✓ click group entry point |
| `cli/commands.py` | ✓ all subcommands scaffolded (some stubs) |
| `agent/__init__.py` | ✓ package |
| `agent/collectors/__init__.py` | ✓ package |
| `agent/collectors/base.py` | ✓ abstract collector interface + registry |
| `requirements.txt` | ✓ click, httpx |
| `scripts/bootstrap.sh` | ✓ venv + DB init |
| `scripts/start-agent.sh` | ✓ stub |
| `scripts/start-aggregator.sh` | ✓ stub |
| `tests/test_phase0_smoke.py` | ✓ 26 tests pass |

---

### Phase 1 — Per-Machine Agent (`rowbutt-agent`)

**Objective:** Build the lightweight agent that runs on each LLM host. It polls local endpoints and system metrics, stores data locally, and serves a day-summary HTTP endpoint.

**Tasks:**

1. [x] Write `agent/collectors/base.py` — abstract collector:
   - `Collector.collect() → CollectResult` dataclass
   - `Collector.name → str` for registration
   - Registry pattern: `@register` decorator so collectors self-register on import
2. [x] Write `agent/collectors/llm_tokens.py` — token usage collector:
   - **Ollama provider:** scrapes Prometheus `/api/metrics` or `/metrics`, extracts `ollama_request_tokens_total` counters by model
   - **vLLM provider:** scrapes Prometheus `/metrics`, extracts `vllm:prompt_tokens_total` and `vllm:generation_tokens_total`
   - **llama.cpp provider:** queries `/slots` JSON endpoint for per-slot `n_past` and `n_generated`
   - **DiffTracker:** turns cumulative Prometheus counters into per-interval deltas; handles counter resets gracefully
   - Common output: `{model, input_tokens, output_tokens, total_tokens, source, endpoint}`
3. [x] Write `agent/collectors/system.py` — system metrics collector:
   - Memory: `psutil.virtual_memory()` → total, used, pct
   - CPU temps: `psutil.sensors_temperatures()` or `/sys/class/thermal/thermal_zone*/temp`
   - GPU: `nvidia-smi --query-gpu=... --format=csv` (JSON) or fallback to `sensors`
   - Load: `psutil.getloadavg()` or `/proc/loadavg`
   - Graceful degradation: warns for missing sources, returns partial data
4. [x] Write `agent/scheduler.py` — poll loop:
   - PollJob per collector with configurable interval
   - 1-second tick resolution, daemon background thread
   - Writes raw samples to agent-local DB (token events, system samples, daily rollups)
   - ON CONFLICT upserts for daily_rollups (4-hour buckets)
   - Handles collector failures gracefully (log + continue)
5. [x] Write `agent/server.py` — HTTP server:
   - Uses **Flask** (as specified by user)
   - Endpoint: `GET /health` — liveness check with DB status
   - Endpoint: `GET /api/v1/agent-info` — shows configured endpoints
   - Endpoint: `GET /api/v1/day-summary?date=YYYY-MM-DD` — aggregated day data from local DB
   - Binds to `0.0.0.0:5000` (configurable)
6. [x] Write `agent/cli.py` — CLI entry point:
   - `run_agent()` orchestrates DB init → collector registration → scheduler start → Flask server
   - Handles SIGINT/SIGTERM for graceful shutdown
   - Configurable host/port/debug
7. [ ] Configure collector plugins via YAML (deferred — currently uses Python dict defaults)
8. [x] Write unit tests with mock collectors — Prometheus parser, DiffTracker, Flask routes, collector registration, system collector smoke test
9. [x] Integration test: 44/44 checks covering collector registry, Prometheus parser, DiffTracker, Flask endpoints, provider instantiation, system collector

**Deliverable:** A fully functional agent that can be installed on any machine with local LLM endpoints. Polls, stores, and serves data.

---

### Phase 2 — Central Aggregator (`rowbutt-aggregator`)

**Objective:** Build the aggregator that pulls daily data from all agents, computes costs, and stores results.

**Tasks:**

3. [x] Write `aggregator/puller.py` — HTTP client to pull day-summary from each agent via `GET /api/v1/day-summary?date=YYYY-MM-DD`:
   - `AgentConfigStore` — JSON file config (`~/.rowbutt/agents.json`)
   - `pull_agent()` — single agent pull with timeout/error handling
   - `pull_all_agents()` — iterate all configured agents
   - `_upsert_summary()` — writes/non-duplicates into `daily_summaries`
   - `_estimate_inference_minutes()` — ~50 tok/s throughput estimate
   - Non-200 responses, connection failures, timeouts all handled gracefully
4. [x] Write `aggregator/costs.py` — cost calculation engine:
   - **Electricity cost:** kWh = (gpu_w + system_baseline_w) × hours / 1000 × $/kWh
   - **Frontier cost:** per-model pricing lookup from `pricing_cache` with alias matching and fallback dict
   - **Savings:** frontier_cost - electricity_cost
   - Model aliases: `deepseek-v4` → `deepseek-ai/DeepSeek-V4-Flash`, etc.
   - Fallback pricing for 11 common models
   - Writes to `cost_reports` table
5. [x] Write `aggregator/report.py` — daily cost report generator:
   - Full Markdown report with Summary table, Per-Machine Breakdown, Savings Over Time
   - `generate_report(date)` — single day
   - `generate_week_report(end_date)` — 7-day aggregate
   - Formatting helpers: `_fmt_tokens()`, `_fmt_hours()`, `_fmt_usd()`
   - Writes to `~/.rowbutt/reports/YYYY-MM-DD.md`
   - Handles empty data, missing costs, orphan summaries
6. [x] Write `aggregator/cli.py` — CLI entry points:
   - `do_pull_all()` — pulls all agents, prints results
   - `do_compute_costs()` — runs cost engine, prints per-machine breakdown
   - `do_report_today()` / `do_report_date()` — generates and prints report
   - `do_report_week()` / `do_report_month()` — aggregated reports
   - Supports `--format markdown|json|csv` output
   - Telegram delivery via `~/.hermes/skills/telegram-send/scripts/telegram-send`
7. [x] Wire `cli/commands.py:` aggregator and report groups now delegate to `aggregator.cli`
   - `rowbutt aggregator pull-all --date YYYY-MM-DD`
   - `rowbutt aggregator compute-costs --hostname <name> --date YYYY-MM-DD`
   - `rowbutt report today --save --deliver telegram`
   - `rowbutt report date YYYY-MM-DD --format json`
   - `rowbutt report week`, `rowbutt report month`, `rowbutt report list`
8. [x] Phase 2 integration tests — 76/76 checks covering:
   - AgentConfigStore (save/load/missing)
   - DB upsert (insert/duplicate detection)
   - Cost calculation (electricity, frontier, savings, per-model)
   - Pricing lookup (exact/alias/unknown)
   - Report generator (daily, weekly, empty, formatting helpers)
   - CLI wiring (aggregator commands, report commands)
   - Edge cases (zero tokens, connection refused)

**Deliverable:** Running `rowbutt aggregator pull-all && rowbutt report today` produces a complete daily cost report.

---

### Phase 3 — Daily Report Delivery & Automation

**Objective:** Schedule the daily pull + report cycle, deliver the report where Robert can see it.

**Tasks:**

1. [x] Write systemd user service `rowbutt-aggregator.service` + timer:
   - `rowbutt-aggregator.service` — runs `rowbutt aggregator pull-all && rowbutt aggregator compute-costs && rowbutt report today --save`
   - `rowbutt-aggregator.timer` — triggers daily at 23:55
   - User-level service (`systemctl --user`), no sudo required
2. [x] Report delivery to `~/.rowbutt/reports/YYYY-MM-DD.md`:
   - `report today --save` writes Markdown to disk
   - `report today --deliver telegram` pipes through existing Telegram script
   - `report today --format json|csv` supports structured output
3. [x] Weekly summary: `rowbutt report week --save --deliver telegram`
4. [x] Monthly summary: `rowbutt report month --format markdown`
5. [x] `rowbutt report list` — shows available dates from DB + on-disk reports

**Deliverable:** Automated daily report appears in `~/.rowbutt/reports/` and (optionally) in Telegram every evening.

---

### Phase 4 — Web View (Nice-to-Have)

**Objective:** Lightweight web UI for browsing historical cost reports — not a real-time dashboard, more of a "check the archives" view.

**Tasks:**

1. [x] Write `web/app.py` — Flask server (Flask already a dependency from Phase 1):
   - `GET /` → landing page showing latest report summary and list of available dates
   - `GET /report/<date>` → full Markdown report rendered as HTML with stats cards
   - `GET /api/v1/reports` → JSON list of available report dates
   - `GET /api/v1/report/<date>` → JSON of the full report data
   - Built-in `_md_to_html()` conversion (no `markdown` library dependency needed)
2. [x] Write `web/templates/index.html`:
   - Stats cards (tokens, electricity, frontier, savings)
   - Latest report section with inline rendered Markdown
   - Grid of all available report dates
   - API endpoints reference
   - Dark theme ("industrial" iron aesthetic)
3. [x] Write `web/templates/report.html`:
   - Detail view with stats + full rendered report
   - "Back to overview" navigation link
4. [x] Wire CLI: `rowbutt web start --host 0.0.0.0 --port 8123`
5. [x] Phase 4 integration tests — 42/42 checks covering:
   - GET / (with data, empty DB)
   - GET /report/<date> (valid, invalid, future/404)
   - GET /api/v1/reports (JSON shape)
   - GET /api/v1/report/<date> (JSON shape, missing)
   - CLI --help wiring (web, web start)
   - _md_to_html conversion (h1, h2, strong, table, td)

**Deliverable:** A browsable archive of daily cost reports at `http://<host>:8123`.

---

### Phase 5 — Packaging & Deployment

**Objective:** One-command deployment for both agent and aggregator.

**Tasks:**

1. [x] Write `deploy/bootstrap.sh` — unified interactive installer:
   - Checks python3 + venv prerequisites
   - Creates `~/.rowbutt/venv/` virtual environment
   - Installs deps from `requirements.txt` + editable package install
   - Creates `~/.rowbutt/` runtime directories + example `agents.json`
   - Installs systemd user units from deploy/
   - **Prompts user:** agent only, aggregator only, both, or skip
   - Summary output with quick-reference commands
   - `--help` flag, idempotent, safe to re-run
2. [x] Write `deploy/start-agent.sh` — convenience launcher:
   - `bash start-agent.sh` → start via systemd (or foreground fallback)
   - `--foreground` flag for non-systemd environments
   - `status` and `stop` subcommands
3. [x] Write `deploy/start-aggregator.sh` — convenience launcher:
   - `bash start-aggregator.sh` → enable timer + show status
   - `--now` flag to run pipeline immediately
   - `status` subcommand for timer + last-run check
4. [x] Write systemd unit for the agent:
   - `deploy/rowbutt-agent.service` — Flask daemon (port 5000)
   - Restart on failure, log to journald, security hardening
5. [x] Write systemd unit for the aggregator:
   - `deploy/rowbutt-aggregator.service` — runs daily pull + compute + save report
   - `deploy/rowbutt-aggregator.timer` — daily 23:55 trigger
6. [x] Create `deploy/` directory with:
   - `deploy/ansible/playbook.yaml` — Ansible playbook for deploying agent to multiple machines
   - `deploy/ansible/requirements.yaml` — empty requirements (no collections needed)
   - `deploy/README.md` — comprehensive install guide
7. [x] Write `README.md` with:
   - Quick start (install agent on an LLM host)
   - Quick start (install aggregator on the central container)
   - Config file reference
   - Adding collectors
   - Reading reports

**Deliverable:** `curl https://raw.githubusercontent.com/.../bootstrap.sh | bash` works (or equivalent manual steps documented).

### 📦 Phase 5a — GitHub Publication ✓ COMPLETED

1. [x] Container's SSH public key added to ridiculousostrich GitHub account
2. [x] `git init`, `.gitignore` (35 entries), repo created on GitHub as `ridiculousostrich/rowbutt-dashboard`
3. [x] Initial commit + push: all Phases 1–5 code, README, ROADMAP, pyproject.toml
4. [x] `REPLACE_ME`/`TODO` placeholders in deploy files resolved to real repo URLs
5. [x] Repo made public (required for unauth curl-pipe from target machines)
6. [x] `install-agent.sh` hardened for curl-pipe execution:
   - `${BASH_SOURCE[0]:-}` fix for unbound variable under `set -u`
   - `loginctl enable-linger` + nohup fallback for headless machines without D-Bus user session
   - GitHub archive download via raw.githubusercontent.com (codeload redirect hung on some networks)
   - Download file list corrected to match actual project structure
   - Verbose pip output so install errors are visible

### Phase 5b — First Deployment ✓ COMPLETED

**Objective:** Agent installed on one inference machine, aggregator + web UI deployed on the Hermes container.

**Deployed Agent (`gx10-e587` — 192.168.1.X):**

| Component | Status | Details |
|-----------|--------|---------|
| `rowbutt-agent.service` | ✅ Active (running) | `systemctl --user`, port 5000 |
| Agent package | ✅ Installed | `~/.rowbutt/agent-package/` |
| Virtual env | ✅ Ready | `~/.rowbutt/venv/` |
| Agent DB | ✅ Initialised | `~/.rowbutt/agent.db` |
| HTTP endpoint | ✅ Reachable | `http://gx10-e587:5000/health` |

**Deployed Aggregator (Hermes container — 192.168.1.57):**

| Component | Status | Details |
|-----------|--------|---------|
| Package | ✅ Installed | `pip install -e` from local workspace |
| `agents.json` | ✅ Configured | 1 agent: `gx10-e587` |
| `rowbutt-aggregator.timer` | ✅ Active | Daily 23:55 UTC via `systemctl --user` |
| `rowbutt-aggregator.service` | ✅ Installed | Oneshot: pull → compute → save |
| `rowbutt-web.service` | ✅ Active (running) | Flask on port 8123 |
| Pipeline test | ✅ Verified | `pull-all` → `compute-costs` → `report today --save` |
| Report output | ✅ Generated | `/root/.rowbutt/reports/2026-09-05.md` |

**Install command for additional agents:**
```bash
curl -sL https://raw.githubusercontent.com/ridiculousostrich/rowbutt-dashboard/main/deploy/install-agent.sh | bash
```

---

## 5. Database Schema (Draft)

### Agent-Local DB (`~/.rowbutt/agent.db`)

```sql
-- Raw token usage events from LLM endpoint polls
CREATE TABLE token_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT    NOT NULL,  -- ISO timestamp when agent recorded this
    model           TEXT    NOT NULL,  -- model name, e.g. "deepseek-ai/DeepSeek-V4-Flash"
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    session_id      TEXT,              -- LLM session ID, if available
    source          TEXT,              -- "ollama", "open-webui", "openai-compatible"
    batch_hour      INTEGER,           -- which 4-hour bucket this falls in (0,4,8,12,16,20)
    batch_date      TEXT               -- ISO date for the bucket
);

CREATE INDEX idx_token_date ON token_events(batch_date, batch_hour);
CREATE INDEX idx_token_model ON token_events(model);

-- Raw system metric samples
CREATE TABLE system_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at      TEXT    NOT NULL,  -- ISO timestamp
    mem_total_gb    REAL,
    mem_used_gb     REAL,
    mem_pct         REAL,
    temp_cpu_avg    REAL,
    temp_cpu_max    REAL,
    temp_gpu        REAL,
    gpu_power_w     REAL,
    gpu_util_pct    REAL,
    load_1m         REAL,
    load_5m         REAL,
    load_15m        REAL,
    batch_hour      INTEGER,
    batch_date      TEXT
);

CREATE INDEX idx_sys_date ON system_samples(batch_date, batch_hour);

-- Pre-computed 4-hour rollups for quick day-summary generation
CREATE TABLE daily_rollups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,  -- ISO date
    bucket_hour     INTEGER NOT NULL,  -- 0, 4, 8, 12, 16, 20
    -- Token summary
    total_input     INTEGER NOT NULL DEFAULT 0,
    total_output    INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    -- Per-model token breakdown (stored as JSON for simplicity)
    model_breakdown TEXT,  -- JSON: {"model": {"input": N, "output": N, "sessions": N}}
    -- System summary
    avg_mem_pct     REAL,
    avg_temp_cpu    REAL,
    avg_temp_gpu    REAL,
    avg_gpu_power_w REAL,
    max_temp_cpu    REAL,
    max_temp_gpu    REAL,
    -- Timing
    sample_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(date, bucket_hour)
);
```

### Central Aggregator DB (`~/.rowbutt/aggregator.db`)

```sql
-- Daily summary per machine (pulled from agent APIs)
CREATE TABLE daily_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname        TEXT    NOT NULL,  -- machine name
    date            TEXT    NOT NULL,  -- ISO date
    -- Token data (merged from agent)
    total_input     INTEGER NOT NULL DEFAULT 0,
    total_output    INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    model_breakdown TEXT,              -- JSON per-model token counts
    -- System data (averaged from agent)
    avg_mem_pct     REAL,
    avg_temp_cpu    REAL,
    avg_temp_gpu    REAL,
    avg_gpu_power_w REAL,
    max_temp_cpu    REAL,
    max_temp_gpu    REAL,
    inference_time_minutes REAL,  -- estimated from token events or nvidia-smi activity
    -- Agent metadata
    agent_version   TEXT,
    collectors_active TEXT,         -- JSON list
    created_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(hostname, date)
);

-- Frontier pricing cache
CREATE TABLE pricing_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,  -- ISO date this price was current
    model           TEXT    NOT NULL,  -- canonical model name
    provider        TEXT    NOT NULL,  -- "openai", "anthropic", "google", "deepseek", etc.
    input_price     REAL    NOT NULL,  -- $ per 1M input tokens
    output_price    REAL    NOT NULL,  -- $ per 1M output tokens
    source          TEXT,              -- URL or reference
    UNIQUE(date, model)
);

-- Computed cost reports (one row per machine per day)
CREATE TABLE cost_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname        TEXT    NOT NULL,
    date            TEXT    NOT NULL,  -- ISO date
    -- Electricity
    inference_hours      REAL,        -- total hours of inference activity
    system_power_w       REAL,        -- configured system baseline (W)
    gpu_avg_power_w      REAL,        -- average GPU power draw (W)
    total_power_kwh      REAL,        -- computed kWh
    electricity_cost     REAL,        -- $ at $0.11/kWh
    -- Frontier costs
    frontier_provider    TEXT,        -- which provider was used for comparison
    frontier_input_cost  REAL,        -- $ for input tokens
    frontier_output_cost REAL,        -- $ for output tokens
    frontier_total_cost  REAL,        -- total $ for tokens
    -- Savings
    savings              REAL,        -- frontier_total_cost - electricity_cost
    -- Reference
    pricing_cache_id     INTEGER REFERENCES pricing_cache(id),
    cost_per_1m_tokens   REAL,        -- blended cost per 1M tokens
    created_at           TEXT DEFAULT (datetime('now')),
    UNIQUE(hostname, date)
);
```

---

## 6. Known Problems & Open Questions

### Problem 1: Ollama Token Count Availability

**The question:** Does Ollama expose per-session token counts via its API?

**Current understanding:**
- Ollama's `/api/generate` response includes `prompt_eval_count` and `eval_count` (input/output tokens), but only if you're making the request yourself. The built-in Ollama web UI or CLI sessions don't log to an API that we can retroactively query.
- Ollama's server log at `~/.ollama/logs/server.log` does log completion events with token counts — we can tail and parse these.
- Ollama's `/api/tags` endpoint lists loaded models but doesn't expose token counts.

**Approach:** Start by polling Ollama's `/api/metrics` Prometheus endpoint (enabled in newer versions). Fall back to log parsing. If neither works, offer a CLI wrapper option.

### Problem 2: Inference Time Estimation

**The question:** How do we know how long the GPU was actively doing inference?

**Without direct instrumentation:**
- If `nvidia-smi` is available, monitor `utilization.gpu` — periods above a threshold (e.g., >10%) count as inference time.
- If `nvidia-smi` reports `power.draw`, we can detect inference by power draw significantly above idle.
- Fallback: track the time between the first and last token event of a session, or use a configurable "average inference time per session" value.

**Approach:** Use GPU utilization monitoring as primary, token event timestamps as secondary.

### Problem 3: Power Draw Without nvidia-smi

**The question:** What if the machine doesn't have an NVIDIA GPU or `nvidia-smi` isn't available?

**Approach:** The agent uses configurable power baselines per machine. In `agent.yaml`:
```yaml
power:
  system_idle_watts: 75
  gpu_idle_watts: 0     # no GPU, or AMD/Intel
  gpu_load_watts: 0
```
The cost calculator simply uses the system baseline for inference time when no GPU power data is available.

### Problem 4: Frontier Pricing Data Source

**The question:** Where do we get daily-updated API prices?

**Approach for MVP:** Ship a static price map in `config/defaults.py` for the most common models. The user can override via a YAML file or CLI command. Future: add a web scraping cron.

### Problem 5: Agent Discovery

**The question:** How does the aggregator know where all the agents are?

**Approach:** Static config in `~/.rowbutt/aggregator.yaml`:
```yaml
agents:
  - hostname: ubuntu-server
    url: http://192.168.1.52:5000
  - hostname: gx10-operator
    url: http://192.168.1.52:5000  # different port on same machine, or different host
```
Future: mDNS/SSDP auto-discovery.

---

## 7. Technology Stack (Proposed)

| Layer | Choice | Why |
|-------|--------|-----|
| **Language** | Python 3.13 | Already in environment, strong SQLite + HTTP support |
| **Agent HTTP server** | FastAPI or `http.server` | Lightweight; FastAPI if we want structured endpoints, stdlib if we want zero deps |
| **HTTP client (aggregator)** | `httpx` | Async, connection pooling, timeout handling |
| **Storage** | SQLite | Zero-config, portable, ships everywhere |
| **CLI** | `click` | Battle-tested, subcommand groups, no config file needed for simple usage |
| **Frontend** | Vanilla HTML/CSS | No build pipeline; just served Markdown rendered to HTML |
| **Process supervision** | systemd | Native on Debian, timer units for scheduling |
| **Config** | YAML + env vars | Human-readable, easy to script |
| **Reports** | Markdown | Renders anywhere (Terminal, GitHub, Telegram, HTML) |

---

## 8. Milestones & Rough Timeline

| Milestone | What | Estimated Effort |
|-----------|------|------------------|
| **M0 — Skeleton** | Repo structure, both DB schemas, CLI scaffold, smoke test | ~1 session |
| **M1 — Agent** | Per-machine collector polling LLMs + system metrics, HTTP API | ~2–3 sessions |
| **M2 — Aggregator** | Pull day-summaries from agents, compute costs, pricing cache | ~1–2 sessions |
| **M3 — Daily Report** | Markdown report generation, delivery, cron automation | ~1 session |
| **M4 — Web View** | Lightweight report browser (nice-to-have) | ~1 session |
| **M5 — Packaging** | bootstrap scripts, systemd units, README | ~1 session |

**Total estimated effort:** 7–10 focused sessions.

---

## 9. Future Ideas (Post-MVP)

- **Weekly/monthly trend reports** — "You spent $X on electricity this month, saved $Y vs. API pricing"
- **Annotation support** — mark days with "training run", "heavy experimentation", "idle" for better categorization
- **Per-user breakdown** — if Hermes/Open WebUI exposes user IDs, show who's using the most tokens
- **Multi-provider comparison** — "Using DeepSeek would have cost $X, OpenAI would have cost $Y" side by side
- **Prometheus exporter** — expose cost snapshots in Prometheus format for the existing monitoring stack
- **Billing alerts** — notify if daily electricity or frontier-equivalent cost exceeds a threshold
- **NFS-based sharing** — agents write rollups to an NFS share (like the TrueNAS NFS mount) and the aggregator reads from there instead of hitting HTTP endpoints (zero network polling)

---

## 9. Work Orders (Cross-Session Problems)

These are known issues, decisions, or implementation gaps that will need to be resolved in dedicated sessions. They arise from pool decisions, stubs left for later phases, or discoveries made during development.

### W1 — Which HTTP server framework for the agent?

The agent needs a tiny HTTP server to expose `/api/v1/day-summary`. Options:
- **`http.server`** (stdlib) — zero deps, but cumbersome for structured JSON APIs.
- **FastAPI + uvicorn** — clean, async, auto-docs, but adds a dependency.
- **Flask** — simple, well-known.

**Status:** Phase 1 completed. `requirements.txt` has `flask`, `psutil`, `click`, and `httpx`. Flask was chosen as the HTTP framework (user preference).

### W2 — The `rowbutt` CLI needs `__main__.py`

Currently invoked as `python3 -m cli.main`. This works but is awkward and not what users expect. A proper `__main__.py` entry point pointing at `cli.main.main()` would allow `python3 -m rowbutt` or, with a pip-installable package, just `rowbutt`.

**Status:** Low-priority polish. The `.venv/bin/python3 -m cli.main` pattern works for development. Add when packaging (Phase 5).

### W3 — Aggregator needs an agent configuration format

The aggregator needs to know where agents live (hostname:port). Currently there's no config file format. The ROADMAP says "configure agents in `~/.rowbutt/aggregator.yaml`" but no YAML parser is in `requirements.txt` and no config schema exists.

**Status:** To be decided in Phase 2. Format should be simple:

```yaml
agents:
  ubuntu-server:
    url: http://192.168.1.X:5000
    hostname: ubuntu-server
  rp11:
    url: http://192.168.1.240:5000
    hostname: rp11
```

### W4 — Pricing cache maintenance

The aggregator DB seeds 11 model prices at init time, but these go stale. Needs a mechanism to:
- Periodically update `pricing_cache` from a remote source (OpenRouter API? Hardcoded table in a script?)
- Allow manual overrides (user runs `rowbutt aggregator set-price <model> <input> <output>`)
- Detect stale prices and warn in reports

**Status:** Phase 2 concern. The schema supports it (`updated_at` column).

### W5 — Agent YAML configuration

The agent should be configurable via `~/.rowbutt/agent.yaml`:
- Which LLM endpoints to poll (Ollama default `http://localhost:11434`, vLLM `http://localhost:8000`, llama.cpp `http://localhost:8080`)
- Poll interval (default 300s for tokens, 60s for system metrics)
- Server bind address/port (default `0.0.0.0:5000`)
- Local SQLite path override
- Which collectors to enable
- GPU indices to monitor

**Status:** Phase 1 uses Python dict defaults in `config/defaults.py` (`LLM_ENDPOINT_DEFAULTS`, `SYSTEM_POLL_INTERVAL`, etc.). A proper YAML config loader will be built in a dedicated session. The infrastructure (per-collector kwargs, configurable intervals) is already in place.

### W6 — Sensible default pricing data

The 11 models seeded in `pricing_cache` are hardcoded in `schema_aggregator.sql`. These should be moved to a dedicated data file (e.g. `db/pricing_data.sql` or `config/model_prices.yaml`) so they can be updated independently of the schema migration. The schema file is for structure; price data is content.

**Status:** Acceptable for MVP. Refactor in Phase 5 if pricing updates become frequent.

### W7 — Prometheus endpoint paths vary by Ollama version

During Phase 1 testing, `http://localhost:11434/api/metrics` returned 404 on the test system's Ollama version. Some Ollama versions expose metrics at `/metrics` instead of `/api/metrics`. The `OllamaProvider` falls back to `/metrics` automatically, but this should be documented and tested.

**Status:** Handled via try/except fallback in `OllamaProvider.poll()`. No action needed unless a version uses a third path.

### W8 — llama.cpp `/slots` returns per-slot cumulative counters, not deltas

The LlamaCppProvider reads `/slots` which gives cumulative `n_past` and `n_generated` per slot. Currently these are recorded as-is (they appear as raw totals in the DB). For daily rollups they need DiffTracker treatment similar to Prometheus counters.

**Status:** Acceptable for MVP. The daily rollups accumulate correctly over the day. Add DiffTracker for llama.cpp `/slots` in a follow-up if per-interval delta tracking is needed.

### W9 — NFS-based data sharing as an alternative to HTTP

The "Future Ideas" section mentions NFS-based sharing: agents write rollups to a shared NFS mount, and the aggregator reads flat files instead of hitting HTTP endpoints. This would:
- Eliminate network polling entirely on the aggregator side
- Work well with the TrueNAS NFS mount already in the environment
- Reduce operational complexity (no ports to open, no service management on agents if run as cron)

**Status:** Future. Keep as an alternative deployment mode in the architecture.

---

*This ROADMAP is a living document. As we learn more about actual LLM endpoint APIs and system capabilities, phases may be reordered, split, or consolidated.*
