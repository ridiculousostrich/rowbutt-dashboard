"""Aggregator — Daily Cost Report Generator.

Produces Markdown reports from ``cost_reports`` and ``daily_summaries`` data.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date as date_mod, timedelta
from typing import Optional

from config.defaults import REPORTS_DIR, ROWBUTT_DIR, GPU_POWER_COST_PER_KWH

logger = logging.getLogger(__name__)


@dataclass
class ReportResult:
    """Result of generating a daily report."""

    date: str
    markdown: str = ""
    path: Optional[str] = None
    machines: int = 0
    total_tokens: int = 0
    total_electricity_cost: float = 0.0
    total_frontier_cost: float = 0.0
    total_savings: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ── Public API ──────────────────────────────────────────────────


def generate_report(date_str: str | None = None) -> ReportResult:
    """Generate a Markdown cost report for a given date.

    Parameters
    ----------
    date_str : str, optional
        ISO date, defaults to today.

    Returns
    -------
    ReportResult
    """
    if date_str is None:
        date_str = date_mod.today().isoformat()

    from db.db_common import connect_aggregator_db

    with connect_aggregator_db() as conn:
        # Load cost_reports for the date
        cost_rows = conn.execute(
            """SELECT cr.*, ds.model_breakdown, ds.total_input, ds.total_output,
                      ds.total_tokens
               FROM cost_reports cr
               LEFT JOIN daily_summaries ds ON ds.hostname = cr.hostname AND ds.date = cr.date
               WHERE cr.date = ?
               ORDER BY cr.savings DESC""",
            (date_str,),
        ).fetchall()

        # Check for summaries without computed costs
        orphan_rows = conn.execute(
            """SELECT hostname, total_input, total_output, total_tokens
               FROM daily_summaries
               WHERE date = ? AND hostname NOT IN (
                   SELECT hostname FROM cost_reports WHERE date = ?
               )""",
            (date_str, date_str),
        ).fetchall()

        # Weekly / monthly totals
        week_ago = (date_mod.fromisoformat(date_str) - timedelta(days=6)).isoformat()
        month_start = date_str[:8] + "01"

        week_totals = conn.execute(
            "SELECT SUM(savings) as s FROM cost_reports WHERE date BETWEEN ? AND ?",
            (week_ago, date_str),
        ).fetchone()

        month_totals = conn.execute(
            "SELECT SUM(savings) as s FROM cost_reports WHERE date >= ? AND date <= ?",
            (month_start, date_str),
        ).fetchone()

    if not cost_rows and not orphan_rows:
        return ReportResult(date=date_str, markdown=_empty_report(date_str))

    result = _build_markdown(date_str, cost_rows, orphan_rows,
                             week_totals["s"] if week_totals else None,
                             month_totals["s"] if month_totals else None)

    # Write file
    reports_dir = REPORTS_DIR
    os.makedirs(reports_dir, exist_ok=True)
    file_path = os.path.join(reports_dir, f"{date_str}.md")
    with open(file_path, "w") as f:
        f.write(result.markdown)

    result.path = file_path
    logger.info("Report written to %s", file_path)
    return result


def generate_week_report(end_date: str | None = None) -> ReportResult:
    """Generate a 7-day summary report (calls generate_report for each, aggregates)."""
    if end_date is None:
        end_date = date_mod.today().isoformat()

    end = date_mod.fromisoformat(end_date)
    start = end - timedelta(days=6)

    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    reports = [generate_report(d) for d in days]

    # Aggregate
    total_elec = sum(r.total_electricity_cost for r in reports)
    total_frontier = sum(r.total_frontier_cost for r in reports)
    total_savings = sum(r.total_savings for r in reports)
    total_tokens = sum(r.total_tokens for r in reports)

    md = _build_weekly_markdown(start_date=start.isoformat(),
                                 end_date=end_date,
                                 reports=reports,
                                 total_electricity=total_elec,
                                 total_frontier=total_frontier,
                                 total_savings=total_savings,
                                 total_tokens=total_tokens)

    reports_dir = REPORTS_DIR
    file_path = os.path.join(reports_dir, f"week-{start.isoformat()}-to-{end_date}.md")
    os.makedirs(reports_dir, exist_ok=True)
    with open(file_path, "w") as f:
        f.write(md)

    result = ReportResult(
        date=f"{start.isoformat()} to {end_date}",
        markdown=md,
        machines=len(reports),
        path=file_path,
    )
    return result


# ── Markdown Builders ───────────────────────────────────────────


def _empty_report(date_str: str) -> str:
    return (
        f"# Rowbutt Cost Report — {date_str}\n\n"
        f"_No data available for this date._\n"
    )


def _build_markdown(
    date_str: str,
    cost_rows: list,
    orphan_rows: list,
    week_savings: float | None,
    month_savings: float | None,
) -> ReportResult:
    """Build the full Markdown report string and return a ReportResult."""
    lines: list[str] = []
    lines.append(f"# Rowbutt Cost Report — {date_str}")
    lines.append("")
    lines.append("---")
    lines.append("")

    total_tokens = 0
    total_elec = 0.0
    total_frontier = 0.0
    total_savings = 0.0
    all_warnings: list[str] = []

    # ── Summary Table ──
    lines.append("## Summary")
    lines.append("")
    lines.append("| Machine | Tokens (In / Out) | Inference Time | Electricity | Frontier Cost | Savings |")
    lines.append("|---------|------------------|----------------|-------------|---------------|---------|")

    for row in cost_rows:
        inp = row["total_input"] or 0
        out = row["total_output"] or 0
        elec = row["electricity_cost"] or 0.0
        frontier = row["frontier_total_cost"] or 0.0
        savings = row["savings"] or 0.0
        hours = row["inference_hours"] or 0.0

        total_tokens += inp + out
        total_elec += elec
        total_frontier += frontier
        total_savings += savings

        lines.append(
            f"| {row['hostname']} "
            f"| {_fmt_tokens(inp)} / {_fmt_tokens(out)} "
            f"| {_fmt_hours(hours)} "
            f"| {_fmt_usd(elec)} "
            f"| {_fmt_usd(frontier)} "
            f"| {_fmt_usd(savings)} |"
        )

    for row in orphan_rows:
        inp = row["total_input"] or 0
        out = row["total_output"] or 0
        total_tokens += inp + out
        lines.append(
            f"| {row['hostname']} "
            f"| {_fmt_tokens(inp)} / {_fmt_tokens(out)} "
            f"| _no cost data_ | _—_ | _—_ | _—_ |"
        )
        all_warnings.append(f"Missing cost data for {row['hostname']} — run `rowbutt aggregator compute-costs`")

    # Totals row
    if len(cost_rows) >= 2 or (cost_rows and orphan_rows):
        lines.append(
            f"| **Total** | **{_fmt_tokens(total_tokens)}** "
            f"| | **{_fmt_usd(total_elec)}** "
            f"| **{_fmt_usd(total_frontier)}** "
            f"| **{_fmt_usd(total_savings)}** |"
        )

    lines.append("")

    # ── Per-Machine Breakdown ──
    for row in cost_rows:
        row = dict(row)  # sqlite3.Row → dict (needed for .get())
        hostname = row["hostname"]
        lines.append(f"## {hostname}")
        lines.append("")

        # Model breakdown
        model_breakdown_raw = row.get("model_breakdown") or "{}"
        if isinstance(model_breakdown_raw, str):
            model_usage = json.loads(model_breakdown_raw)
        else:
            model_usage = model_breakdown_raw

        if model_usage:
            lines.append("### Token Usage & Frontier Cost")
            lines.append("")
            lines.append("| Model | Input Tokens | Output Tokens | Frontier Cost |")
            lines.append("|-------|-------------|--------------|---------------|")
            model_total_cost = 0.0
            for model_name, usage in model_usage.items():
                inp = usage.get("input", 0)
                out = usage.get("output", 0)
                # Compute cost from pricing
                cost = _estimate_model_cost(model_name, inp, out)
                model_total_cost += cost
                lines.append(f"| {model_name} | {_fmt_tokens(inp)} | {_fmt_tokens(out)} | {_fmt_usd(cost)} |")
            lines.append(f"| **Total** | | | **{_fmt_usd(model_total_cost)}** |")
            lines.append("")

            # Cost breakdown
            elec = row["electricity_cost"] or 0.0
            frontier = row["frontier_total_cost"] or 0.0
            savings = row["savings"] or 0.0
            gpu_power = row.get("gpu_avg_power_w")
            hours = row["inference_hours"] or 0.0
            kwh = row.get("total_power_kwh") or 0.0

            lines.append("### Cost Breakdown")
            lines.append("")
            lines.append(f"- **Inference time:** {_fmt_hours(hours)}")
            if gpu_power:
                lines.append(f"- **Average GPU power:** {gpu_power:.0f} W")
            lines.append(f"- **Electricity used:** {kwh:.4f} kWh @ ${GPU_POWER_COST_PER_KWH:.2f}/kWh")
            lines.append(f"- **Electricity cost:** {_fmt_usd(elec)}")
            lines.append(f"- **Frontier API equivalent:** {_fmt_usd(frontier)}")
            lines.append(f"- **Net savings:** {_fmt_usd(savings)}")
            lines.append("")

    # ── Savings Over Time ──
    lines.append("## Savings Over Time")
    lines.append("")
    lines.append(f"- **Today:** {_fmt_usd(total_savings)}")
    if week_savings is not None:
        lines.append(f"- **This week (7 days):** {_fmt_usd(week_savings)}")
    if month_savings is not None:
        lines.append(f"- **This month:** {_fmt_usd(month_savings)}")
    lines.append("")

    # ── Notes / Warnings ──
    if all_warnings:
        lines.append("## Notes")
        lines.append("")
        for w in all_warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by Rowbutt Dashboard v0.1.0*")

    markdown = "\n".join(lines)

    return ReportResult(
        date=date_str,
        markdown=markdown,
        machines=len(cost_rows) + len(orphan_rows),
        total_tokens=total_tokens,
        total_electricity_cost=round(total_elec, 4),
        total_frontier_cost=round(total_frontier, 4),
        total_savings=round(total_savings, 4),
        warnings=all_warnings,
    )


def _build_weekly_markdown(
    start_date: str,
    end_date: str,
    reports: list[ReportResult],
    total_electricity: float,
    total_frontier: float,
    total_savings: float,
    total_tokens: int,
) -> str:
    lines: list[str] = []
    lines.append(f"# Rowbutt Cost Report — Week of {start_date} to {end_date}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Weekly Summary")
    lines.append("")
    lines.append(f"- **Total tokens processed:** {_fmt_tokens(total_tokens)}")
    lines.append(f"- **Total electricity cost:** {_fmt_usd(total_electricity)}")
    lines.append(f"- **Total frontier API cost:** {_fmt_usd(total_frontier)}")
    lines.append(f"- **Total savings:** {_fmt_usd(total_savings)}")
    lines.append("")

    lines.append("### Daily Breakdown")
    lines.append("")
    lines.append("| Date | Machines | Tokens | Electricity | Frontier | Savings |")
    lines.append("|------|----------|--------|-------------|----------|---------|")
    for r in reports:
        lines.append(
            f"| {r.date} | {r.machines} | {_fmt_tokens(r.total_tokens)} "
            f"| {_fmt_usd(r.total_electricity_cost)} "
            f"| {_fmt_usd(r.total_frontier_cost)} "
            f"| {_fmt_usd(r.total_savings)} |"
        )
    lines.append("")

    for r in reports:
        if r.markdown:
            lines.append(f"### {r.date}")
            lines.append("")
            lines.append("```")
            lines.append(r.markdown[:500])  # excerpt
            lines.append("```")
            lines.append("")

    lines.append("---")
    lines.append(f"*Generated by Rowbutt Dashboard v0.1.0*")
    return "\n".join(lines)


# ── Formatting Helpers ──────────────────────────────────────────


def _fmt_tokens(count: int) -> str:
    """Human-readable token count: 1_234_567 → '1.2M'."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _fmt_hours(hours: float) -> str:
    """Human-readable hours: 5.33 → '5h 20m'."""
    h = int(hours)
    m = int(round((hours - h) * 60))
    if h == 0:
        return f"{m}m"
    return f"{h}h {m:02d}m"


def _fmt_usd(amount: float) -> str:
    """Human-readable USD: 3.47 → '$3.47'."""
    if abs(amount) < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def _estimate_model_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate frontier cost for a model using fallback pricing."""
    from aggregator.costs import FALLBACK_PRICING, MODEL_ALIASES

    canonical = MODEL_ALIASES.get(model_name.lower(), model_name)
    prices = FALLBACK_PRICING.get(canonical)
    if prices is None:
        return 0.0
    inp_price, out_price = prices
    inp_cost = input_tokens / 1_000_000 * inp_price
    out_cost = output_tokens / 1_000_000 * out_price
    return round(inp_cost + out_cost, 6)
