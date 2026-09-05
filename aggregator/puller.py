"""Aggregator — HTTP Puller.

Polls all configured agents via GET /api/v1/day-summary,
stores results in the central daily_summaries table.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date as date_mod
from typing import Any, Optional

import httpx

from config.defaults import ROWBUTT_DIR

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────

AGENTS_CONFIG_PATH = os.path.join(ROWBUTT_DIR, "agents.json")
DEFAULT_AGENT_PORT = 9091

# Electricity defaults (overridable per agent)
DEFAULT_SYSTEM_BASELINE_W = 75
DEFAULT_GPU_IDLE_W = 30
DEFAULT_GPU_LOAD_W = 130


@dataclass
class AgentConfig:
    """Configuration for a single agent endpoint."""

    hostname: str
    url: str
    system_baseline_w: float = DEFAULT_SYSTEM_BASELINE_W
    gpu_idle_w: float = DEFAULT_GPU_IDLE_W
    gpu_load_w: float = DEFAULT_GPU_LOAD_W


@dataclass
class PullResult:
    """Result of pulling data from one agent."""

    hostname: str
    success: bool
    http_status: Optional[int] = None
    error: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    records_inserted: int = 0


# ── Agent Config Store ──────────────────────────────────────────


class AgentConfigStore:
    """Load/save agent configurations from JSON file."""

    def __init__(self, path: str | None = None):
        self.path = path or AGENTS_CONFIG_PATH

    def load(self) -> list[AgentConfig]:
        """Load agent configs from JSON file. Returns empty list if missing."""
        if not os.path.exists(self.path):
            logger.warning("Agent config not found at %s — no agents configured", self.path)
            return []
        with open(self.path) as f:
            raw = json.load(f)
        agents: list[AgentConfig] = []
        for item in raw.get("agents", []):
            agents.append(AgentConfig(
                hostname=item["hostname"],
                url=item["url"],
                system_baseline_w=item.get("system_baseline_w", DEFAULT_SYSTEM_BASELINE_W),
                gpu_idle_w=item.get("gpu_idle_w", DEFAULT_GPU_IDLE_W),
                gpu_load_w=item.get("gpu_load_w", DEFAULT_GPU_LOAD_W),
            ))
        return agents

    def save(self, agents: list[AgentConfig]) -> None:
        """Persist agent configs to JSON file."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "agents": [
                {
                    "hostname": a.hostname,
                    "url": a.url,
                    "system_baseline_w": a.system_baseline_w,
                    "gpu_idle_w": a.gpu_idle_w,
                    "gpu_load_w": a.gpu_load_w,
                }
                for a in agents
            ]
        }
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved %d agent(s) to %s", len(agents), self.path)


# ── Pull Logic ──────────────────────────────────────────────────


def pull_agent(
    agent: AgentConfig,
    target_date: str | None = None,
    http_timeout: float = 15.0,
) -> PullResult:
    """Pull a day-summary from one agent and upsert into daily_summaries.

    Parameters
    ----------
    agent : AgentConfig
        The agent to pull from.
    target_date : str, optional
        ISO date string, defaults to today.
    http_timeout : float
        HTTP request timeout in seconds.

    Returns
    -------
    PullResult
    """
    if target_date is None:
        target_date = date_mod.today().isoformat()

    summary_url = f"{agent.url.rstrip('/')}/api/v1/day-summary?date={target_date}"

    try:
        with httpx.Client(timeout=http_timeout) as client:
            resp = client.get(summary_url)
    except httpx.ConnectError as exc:
        return PullResult(hostname=agent.hostname, success=False,
                          error=f"Connection failed: {exc}")
    except httpx.TimeoutException as exc:
        return PullResult(hostname=agent.hostname, success=False,
                          error=f"Timeout after {http_timeout}s: {exc}")
    except Exception as exc:
        return PullResult(hostname=agent.hostname, success=False,
                          error=str(exc))

    if resp.status_code != 200:
        return PullResult(hostname=agent.hostname, success=False,
                          http_status=resp.status_code,
                          error=f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except Exception as exc:
        return PullResult(hostname=agent.hostname, success=False,
                          http_status=resp.status_code,
                          error=f"Invalid JSON: {exc}")

    # Upsert into daily_summaries
    inserted = _upsert_summary(agent.hostname, target_date, data)

    return PullResult(
        hostname=agent.hostname,
        success=True,
        http_status=200,
        data=data,
        records_inserted=inserted,
    )


def pull_all_agents(
    agents: list[AgentConfig] | None = None,
    target_date: str | None = None,
) -> list[PullResult]:
    """Pull day-summaries from all configured agents.

    Parameters
    ----------
    agents : list[AgentConfig], optional
        Defaults to loading from ``agents.json``.
    target_date : str, optional
        ISO date string, defaults to today.

    Returns
    -------
    list[PullResult]
        One result per agent, in the same order as ``agents``.
    """
    if agents is None:
        store = AgentConfigStore()
        agents = store.load()

    if not agents:
        logger.warning("No agents configured — nothing to pull")
        return []

    results: list[PullResult] = []
    for agent in agents:
        logger.info("Pulling %s from %s ...", agent.hostname, agent.url)
        result = pull_agent(agent, target_date)
        results.append(result)
        if result.success:
            logger.info("  ✓ %s — %d record(s)", agent.hostname, result.records_inserted)
        else:
            logger.warning("  ✗ %s — %s", agent.hostname, result.error or "unknown error")

    return results


# ── DB Operations ───────────────────────────────────────────────


def _upsert_summary(hostname: str, date_str: str, data: dict[str, Any]) -> int:
    """Insert or update a row in ``daily_summaries`` from agent API response.

    Returns number of rows affected (0 or 1).
    """
    from db.db_common import connect_aggregator_db

    token_usage = data.get("token_usage", {})
    total_input = sum(m.get("input", 0) for m in token_usage.values())
    total_output = sum(m.get("output", 0) for m in token_usage.values())
    total_tokens = total_input + total_output

    sys_metrics = data.get("system_metrics", {})
    inference_minutes = _estimate_inference_minutes(total_tokens)

    with connect_aggregator_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO daily_summaries
               (hostname, date, total_input, total_output, total_tokens,
                model_breakdown, avg_mem_pct, avg_gpu_power_w,
                avg_temp_cpu, avg_temp_gpu, max_temp_cpu, max_temp_gpu,
                inference_time_minutes, agent_version, raw_payload)
               VALUES (?, ?, ?, ?, ?,
                       ?, ?, ?,
                       ?, ?, ?, ?,
                       ?, ?, ?)""",
            (
                hostname,
                date_str,
                total_input,
                total_output,
                total_tokens,
                json.dumps(token_usage),
                sys_metrics.get("avg_mem_pct"),
                sys_metrics.get("avg_gpu_power_w"),
                sys_metrics.get("avg_temp_cpu"),
                sys_metrics.get("avg_gpu_temp"),
                sys_metrics.get("max_temp_cpu"),
                sys_metrics.get("max_temp_gpu"),
                inference_minutes,
                data.get("version", "unknown"),
                json.dumps(data),
            ),
        )
    return 1


def _estimate_inference_minutes(total_tokens: int) -> float:
    """Rough estimate: assume ~50 tok/s throughput → inference minutes."""
    if total_tokens <= 0:
        return 0.0
    seconds = total_tokens / 50  # 50 tok/s average
    return round(seconds / 60, 1)
