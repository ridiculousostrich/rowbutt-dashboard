"""Rowbutt Dashboard — Default Configuration Values."""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────
HOME = os.path.expanduser("~")
ROWBUTT_DIR = os.path.join(HOME, ".rowbutt")
AGENT_DB_PATH = os.path.join(ROWBUTT_DIR, "agent.db")
AGGREGATOR_DB_PATH = os.path.join(ROWBUTT_DIR, "aggregator.db")
REPORTS_DIR = os.path.join(ROWBUTT_DIR, "reports")

# ── Agent HTTP Server ──────────────────────────────────────
AGENT_HOST = os.environ.get("ROWBUTT_AGENT_HOST", "0.0.0.0")
AGENT_PORT = int(os.environ.get("ROWBUTT_AGENT_PORT", "9091"))

# ── Collector Polling Intervals (seconds) ──────────────────
SYSTEM_POLL_INTERVAL = int(os.environ.get("ROWBUTT_SYSTEM_INTERVAL", "60"))
LLM_POLL_INTERVAL = int(os.environ.get("ROWBUTT_LLM_INTERVAL", "300"))

# ── LLM Endpoint Defaults ──────────────────────────────────
# Each entry: (source_label, url, enabled_by_default, api_type)
# api_type determines how we scrape the endpoint.
LLM_ENDPOINT_DEFAULTS = {
    "ollama": {
        "url": "http://localhost:11434",
        "enabled": True,
        "api_type": "ollama",            # Prometheus /metrics
        "poll_interval": LLM_POLL_INTERVAL,
        "description": "Ollama inference engine",
    },
    "vllm": {
        "url": "http://localhost:8000",
        "enabled": False,
        "api_type": "vllm",              # Prometheus /metrics
        "poll_interval": LLM_POLL_INTERVAL,
        "description": "vLLM inference engine",
    },
    "llamacpp": {
        "url": "http://localhost:8080",
        "enabled": False,
        "api_type": "llamacpp",           # /slots endpoint
        "poll_interval": LLM_POLL_INTERVAL,
        "description": "llama.cpp server",
    },
}

# ── GPU Defaults ───────────────────────────────────────────
GPU_POWER_COST_PER_KWH = float(os.environ.get("ROWBUTT_POWER_COST", "0.12"))
# List of GPU indices to collect (empty = all)
GPU_INDICES = []

# ── System Collector Defaults ──────────────────────────────
COLLECT_SYSTEM = True
COLLECT_GPU = True

# ── Agent Identity ─────────────────────────────────────────
AGENT_HOSTNAME = os.environ.get("ROWBUTT_AGENT_HOSTNAME",
                                os.uname().nodename)
AGENT_VERSION = "0.1.0"
