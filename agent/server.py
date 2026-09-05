"""Flask HTTP server for the Rowbutt agent.

Exposes endpoints for the central aggregator to pull day-summary data.
"""

import json
import logging
import time
from datetime import date as date_mod, timedelta
from typing import Optional

from flask import Flask, jsonify, request

from config.defaults import AGENT_HOST, AGENT_PORT, AGENT_HOSTNAME, AGENT_VERSION
from db.db_common import connect_agent_db

logger = logging.getLogger(__name__)

app = Flask("rowbutt-agent")


# ── Error utilities ─────────────────────────────────────────


def _db_error_response(msg: str, status: int = 500):
    return jsonify({"status": "error", "error": msg}), status


# ── Routes ──────────────────────────────────────────────────


@app.route("/health")
def health():
    """Basic health check — returns agent version and uptime view."""
    try:
        with connect_agent_db() as conn:
            meta = dict(conn.execute(
                "SELECT key, value FROM agent_meta"
            ).fetchall())
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"

    return jsonify({
        "status": "ok",
        "agent": AGENT_HOSTNAME,
        "version": AGENT_VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "database": db_status,
    })


@app.route("/api/v1/day-summary")
def day_summary():
    """Return aggregated token and system data for a given date.

    Query params:
        date (str, optional) — ISO date (default: today).
        format (str, optional) — ``json`` (default) or ``compact``.

    Returns a JSON object with:
        - date
        - hostname
        - token_usage: dict of model → {input, output, sessions}
        - system_metrics: aggregated memory and GPU snapshots for the day
        - rollups: list of per-bucket summaries
    """
    date_str = request.args.get("date", date_mod.today().isoformat())
    output_format = request.args.get("format", "json")

    try:
        target_date = date_mod.fromisoformat(date_str)
    except ValueError:
        return jsonify({"status": "error", "error": f"Invalid date: {date_str}"}), 400

    try:
        with connect_agent_db() as conn:
            # ── Rollups ──
            rollups = conn.execute(
                """SELECT bucket_hour, total_input, total_output, total_tokens,
                          sample_count
                   FROM daily_rollups
                   WHERE date = ?
                   ORDER BY bucket_hour""",
                (date_str,),
            ).fetchall()

            # ── Token events for the day ──
            events = conn.execute(
                """SELECT model, input_tokens, output_tokens, total_tokens,
                          source, session_id, recorded_at
                   FROM token_events
                   WHERE bucket_date = ?
                   ORDER BY recorded_at""",
                (date_str,),
            ).fetchall()

            # ── System samples for the day ──
            samples = conn.execute(
                """SELECT sampled_at, mem_pct, temp_cpu_avg, temp_cpu_max, temp_gpu,
                          gpu_power_w, gpu_util_pct, load_1m
                   FROM system_samples
                   WHERE bucket_date = ?
                   ORDER BY sampled_at""",
                (date_str,),
            ).fetchall()

    except Exception as exc:
        return _db_error_response(str(exc))

    # ── Aggregate per-model token totals ──
    model_usage: dict = {}
    for ev in events:
        model = ev["model"]
        if model not in model_usage:
            model_usage[model] = {"input": 0, "output": 0, "sessions": 0}
        model_usage[model]["input"] += ev["input_tokens"]
        model_usage[model]["output"] += ev["output_tokens"]
        model_usage[model]["sessions"] += 1

    # ── Aggregate system metrics ──
    sys_aggregated = {}
    if samples:
        mem_pcts = [s["mem_pct"] for s in samples if s["mem_pct"] is not None]
        gpu_powers = [s["gpu_power_w"] for s in samples if s["gpu_power_w"] is not None]
        gpu_temps = [s["temp_gpu"] for s in samples if s["temp_gpu"] is not None]
        sys_aggregated = {
            "samples_taken": len(samples),
            "avg_mem_pct": round(sum(mem_pcts) / len(mem_pcts), 1) if mem_pcts else None,
            "avg_gpu_power_w": round(sum(gpu_powers) / len(gpu_powers), 1) if gpu_powers else None,
            "avg_gpu_temp": round(sum(gpu_temps) / len(gpu_temps), 1) if gpu_temps else None,
        }

    result = {
        "date": date_str,
        "hostname": AGENT_HOSTNAME,
        "version": AGENT_VERSION,
        "token_usage": model_usage,
        "system_metrics": sys_aggregated,
        "rollups": [
            {
                "hour": r["bucket_hour"],
                "input_tokens": r["total_input"],
                "output_tokens": r["total_output"],
                "total_tokens": r["total_tokens"],
            }
            for r in rollups
        ],
    }

    if output_format == "compact":
        return jsonify(result)
    return jsonify(result)


@app.route("/api/v1/agent-info")
def agent_info():
    """Return agent configuration metadata."""
    from config.defaults import LLM_ENDPOINT_DEFAULTS

    endpoints = {}
    for name, cfg in LLM_ENDPOINT_DEFAULTS.items():
        endpoints[name] = {
            "url": cfg.get("url"),
            "api_type": cfg.get("api_type"),
            "enabled": cfg.get("enabled", False),
            "description": cfg.get("description", ""),
        }

    return jsonify({
        "hostname": AGENT_HOSTNAME,
        "version": AGENT_VERSION,
        "endpoints": endpoints,
    })


# ── Server factory ──────────────────────────────────────────


def create_app() -> Flask:
    """Create and configure the Flask application instance."""
    return app


def run_server(host: Optional[str] = None, port: Optional[int] = None,
               debug: bool = False) -> None:
    """Run the Flask dev server (suitable for development/testing)."""
    host = host or AGENT_HOST
    port = port or AGENT_PORT
    logger.info("Starting Rowbutt agent HTTP server on %s:%s", host, port)
    app.run(host=host, port=port, debug=debug, use_reloader=False)
