"""Aggregator — Cost Calculation Engine.

Reads daily_summaries from the central DB, computes electricity costs
and equivalent frontier-API costs, stores results in cost_reports.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date as date_mod
from typing import Any, Optional

from config.defaults import GPU_POWER_COST_PER_KWH

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────

DEFAULT_SYSTEM_BASELINE_W = 75.0
DEFAULT_GPU_IDLE_W = 30.0
DEFAULT_GPU_LOAD_W = 130.0

# Per-model fallback pricing (if pricing_cache has no entry for this date)
FALLBACK_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-ai/DeepSeek-V4-Flash": (0.15, 0.60),
    "deepseek-ai/DeepSeek-R1": (0.55, 2.19),
    "qwen/Qwen2.5-72B-Instruct": (0.35, 1.20),
    "mistralai/Mistral-Large": (2.00, 6.00),
    "meta-llama/Llama-3.1-70B": (0.88, 0.88),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}

# Fuzzy model name matching: if a model from the agent exactly matches
# a key in FALLBACK_PRICING, we use it. Otherwise we try to find a
# partial match.
MODEL_ALIASES: dict[str, str] = {
    "deepseek-v4": "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-v4-flash": "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-r1": "deepseek-ai/DeepSeek-R1",
    "llama3.1": "meta-llama/Llama-3.1-70B",
    "llama-3.1": "meta-llama/Llama-3.1-70B",
    "qwen2.5": "qwen/Qwen2.5-72B-Instruct",
    "qwen-2.5": "qwen/Qwen2.5-72B-Instruct",
    "mistral-large": "mistralai/Mistral-Large",
    "gpt4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "claude-3.5-sonnet": "claude-3-5-sonnet-20241022",
    "claude-3-opus": "claude-3-opus-20240229",
    "gemini-1.5-pro": "gemini-1.5-pro",
    "gemini-1.5-flash": "gemini-1.5-flash",
}


@dataclass
class CostResult:
    """Computed cost breakdown for one machine on one date."""

    hostname: str
    date: str
    # Electricity
    inference_hours: float = 0.0
    system_power_w: float = DEFAULT_SYSTEM_BASELINE_W
    gpu_avg_power_w: Optional[float] = None
    total_power_kwh: float = 0.0
    electricity_cost: float = 0.0
    # Frontier
    frontier_input_cost: float = 0.0
    frontier_output_cost: float = 0.0
    frontier_total_cost: float = 0.0
    # Savings
    savings: float = 0.0
    cost_per_1m_tokens: float = 0.0
    # Per-model detail
    model_breakdown: list[dict[str, Any]] = field(default_factory=list)
    # Errors / flags
    warnings: list[str] = field(default_factory=list)


# ── Public API ──────────────────────────────────────────────────


def compute_costs(
    hostname: str | None = None,
    date_str: str | None = None,
    system_baseline_w: float = DEFAULT_SYSTEM_BASELINE_W,
) -> list[CostResult]:
    """Compute electricity + frontier costs for all (or filtered) summaries.

    Reads from ``daily_summaries`` where costs haven't been computed yet
    (or for a specific hostname/date), then writes ``cost_reports`` rows.

    Parameters
    ----------
    hostname : str, optional
        Filter to one machine.
    date_str : str, optional
        ISO date, defaults to today.
    system_baseline_w : float
        System power draw excluding GPU (watts).

    Returns
    -------
    list[CostResult]
    """
    from db.db_common import connect_aggregator_db

    if date_str is None:
        date_str = date_mod.today().isoformat()

    summaries = _load_summaries(hostname, date_str)
    results: list[CostResult] = []

    with connect_aggregator_db() as conn:
        for summary in summaries:
            result = _compute_one(summary, conn, system_baseline_w)
            _write_cost_report(conn, result)
            results.append(result)

    logger.info("Computed costs for %d machine(s) on %s", len(results), date_str)
    return results


# ── Internal ────────────────────────────────────────────────────


def _load_summaries(hostname: str | None, date_str: str) -> list[dict[str, Any]]:
    """Load daily_summaries rows from the aggregator DB."""
    from db.db_common import connect_aggregator_db

    with connect_aggregator_db() as conn:
        if hostname:
            rows = conn.execute(
                "SELECT * FROM daily_summaries WHERE hostname = ? AND date = ?",
                (hostname, date_str),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM daily_summaries WHERE date = ?",
                (date_str,),
            ).fetchall()
    return [dict(r) for r in rows]


def _compute_one(
    summary: dict[str, Any],
    conn: Any,
    system_baseline_w: float,
) -> CostResult:
    """Compute costs for a single daily_summary row."""
    hostname = summary["hostname"]
    date_str = summary["date"]
    total_tokens = summary["total_tokens"] or 0
    total_input = summary["total_input"] or 0
    total_output = summary["total_output"] or 0
    inference_min = summary["inference_time_minutes"] or 0.0
    gpu_power = summary.get("avg_gpu_power_w")

    inference_hours = inference_min / 60.0

    # ── Electricity cost ──
    effective_gpu_w = gpu_power if gpu_power is not None and gpu_power > 0 else DEFAULT_GPU_LOAD_W
    total_power_w = effective_gpu_w + system_baseline_w
    if inference_hours > 0:
        total_power_kwh = round(total_power_w * inference_hours / 1000.0, 4)
        electricity_cost = round(total_power_kwh * GPU_POWER_COST_PER_KWH, 4)
    else:
        total_power_kwh = 0.0
        electricity_cost = 0.0

    # ── Frontier cost (per model) ──
    model_breakdown: list[dict[str, Any]] = []
    frontier_input_cost = 0.0
    frontier_output_cost = 0.0

    model_usage_raw = summary.get("model_breakdown") or "{}"
    if isinstance(model_usage_raw, str):
        model_usage = json.loads(model_usage_raw)
    else:
        model_usage = model_usage_raw

    for model_name, usage in model_usage.items():
        inp = usage.get("input", 0)
        out = usage.get("output", 0)
        inp_price, out_price = _lookup_price(model_name, date_str, conn)

        inp_cost = round(inp / 1_000_000 * inp_price, 6)
        out_cost = round(out / 1_000_000 * out_price, 6)
        model_total = round(inp_cost + out_cost, 6)

        frontier_input_cost += inp_cost
        frontier_output_cost += out_cost

        model_breakdown.append({
            "model": model_name,
            "input_tokens": inp,
            "output_tokens": out,
            "input_price_per_1m": inp_price,
            "output_price_per_1m": out_price,
            "input_cost": inp_cost,
            "output_cost": out_cost,
            "total_cost": model_total,
        })

    frontier_total_cost = round(frontier_input_cost + frontier_output_cost, 6)
    savings = round(frontier_total_cost - electricity_cost, 6)

    cost_per_1m = (
        round(frontier_total_cost / (total_tokens / 1_000_000), 4)
        if total_tokens > 0
        else 0.0
    )

    warnings = []
    if gpu_power is None or gpu_power == 0:
        warnings.append("GPU power data not available; used default load wattage")

    return CostResult(
        hostname=hostname,
        date=date_str,
        inference_hours=round(inference_hours, 2),
        system_power_w=system_baseline_w,
        gpu_avg_power_w=gpu_power,
        total_power_kwh=total_power_kwh,
        electricity_cost=electricity_cost,
        frontier_input_cost=round(frontier_input_cost, 6),
        frontier_output_cost=round(frontier_output_cost, 6),
        frontier_total_cost=frontier_total_cost,
        savings=savings,
        cost_per_1m_tokens=cost_per_1m,
        model_breakdown=model_breakdown,
        warnings=warnings,
    )


def _lookup_price(
    model_name: str,
    date_str: str,
    conn: Any,
) -> tuple[float, float]:
    """Look up frontier pricing for a model on a given date.

    Returns (input_price_per_1m, output_price_per_1m).
    Falls back to FALLBACK_PRICING dict, then to model aliases, then to (0, 0).
    """
    # 1. Exact match in pricing_cache
    row = conn.execute(
        "SELECT input_price, output_price FROM pricing_cache WHERE model = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (model_name, date_str),
    ).fetchone()
    if row:
        return (row["input_price"], row["output_price"])

    # 2. Try alias
    canonical = MODEL_ALIASES.get(model_name.lower())
    if canonical:
        row = conn.execute(
            "SELECT input_price, output_price FROM pricing_cache WHERE model = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (canonical, date_str),
        ).fetchone()
        if row:
            return (row["input_price"], row["output_price"])

    # 3. Fallback dict
    prices = FALLBACK_PRICING.get(canonical or model_name)
    if prices:
        return prices

    logger.warning("No pricing found for model '%s' on date %s", model_name, date_str)
    return (0.0, 0.0)


def _write_cost_report(conn: Any, result: CostResult) -> None:
    """Insert or update a row in ``cost_reports``."""
    pricing_cache_id = _find_pricing_cache_id(result.hostname, result.date, conn)

    conn.execute(
        """INSERT OR REPLACE INTO cost_reports
           (hostname, date, inference_hours, system_power_w, gpu_avg_power_w,
            total_power_kwh, electricity_cost,
            frontier_input_cost, frontier_output_cost, frontier_total_cost,
            savings, pricing_cache_id, cost_per_1m_tokens)
           VALUES (?, ?, ?, ?, ?,
                   ?, ?,
                   ?, ?, ?,
                   ?, ?, ?)""",
        (
            result.hostname,
            result.date,
            result.inference_hours,
            result.system_power_w,
            result.gpu_avg_power_w,
            result.total_power_kwh,
            result.electricity_cost,
            result.frontier_input_cost,
            result.frontier_output_cost,
            result.frontier_total_cost,
            result.savings,
            pricing_cache_id,
            result.cost_per_1m_tokens,
        ),
    )


def _find_pricing_cache_id(hostname: str, date_str: str, conn: Any) -> Optional[int]:
    """Find the pricing_cache row used for this computation (for reference)."""
    row = conn.execute(
        "SELECT id FROM cost_reports WHERE hostname = ? AND date = ? LIMIT 1",
        (hostname, date_str),
    ).fetchone()
    if row:
        return row["id"]
    return None
