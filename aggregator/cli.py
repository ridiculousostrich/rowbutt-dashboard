"""Aggregator — CLI entry points for the rowbutt CLI.

These are called by ``cli/commands.py`` aggregator and report groups.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date as date_mod

logger = logging.getLogger(__name__)


def do_pull_all(date_str: str | None = None) -> None:
    """Pull day-summaries from all configured agents."""
    from aggregator.puller import pull_all_agents, AgentConfigStore
    from db.db_common import init_aggregator_db

    init_aggregator_db()

    store = AgentConfigStore()
    agents = store.load()
    if not agents:
        print("No agents configured. Create ~/.rowbutt/agents.json with agent URLs.")
        print("Example:")
        print('  {"agents": [{"hostname": "ubuntu-server", "url": "http://192.168.1.52:9091"}]}')
        sys.exit(1)

    results = pull_all_agents(agents, target_date=date_str)
    successes = sum(1 for r in results if r.success)
    failures = sum(1 for r in results if not r.success)

    print(f"\nPull complete: {successes} succeeded, {failures} failed")

    for r in results:
        if r.success:
            print(f"  ✓ {r.hostname} — {r.records_inserted} record(s)")
        else:
            print(f"  ✗ {r.hostname} — {r.error}")

    if failures:
        sys.exit(1)


def do_compute_costs(hostname: str | None = None, date_str: str | None = None) -> None:
    """Compute electricity and frontier costs for pulled summaries."""
    from aggregator.costs import compute_costs, DEFAULT_SYSTEM_BASELINE_W
    from db.db_common import init_aggregator_db

    init_aggregator_db()

    results = compute_costs(
        hostname=hostname,
        date_str=date_str,
        system_baseline_w=DEFAULT_SYSTEM_BASELINE_W,
    )

    if not results:
        print("No summaries found to compute costs for.")
        print("  First run 'rowbutt aggregator pull-all' to fetch agent data.")
        sys.exit(1)

    print(f"Costs computed for {len(results)} machine(s):")
    print("")

    for r in results:
        print(f"  {r.hostname}:")
        print(f"    Inference time:  {r.inference_hours:.2f} hours")
        print(f"    Electricity:     ${r.electricity_cost:.4f}")
        print(f"    Frontier cost:   ${r.frontier_total_cost:.4f}")
        print(f"    Savings:         ${r.savings:.4f}")
        print(f"    Models:          {len(r.model_breakdown)}")
        if r.warnings:
            for w in r.warnings:
                print(f"    ⚠ {w}")


def do_report_today(fmt: str = "markdown") -> str:
    """Generate today's report and print it."""
    from aggregator.report import generate_report
    from db.db_common import init_aggregator_db

    init_aggregator_db()
    result = generate_report()
    _print_report(result, fmt)
    return result.markdown


def do_report_date(date_str: str, fmt: str = "markdown") -> str:
    """Generate a report for a specific date."""
    from aggregator.report import generate_report
    from db.db_common import init_aggregator_db

    init_aggregator_db()
    try:
        date_mod.fromisoformat(date_str)
    except ValueError:
        print(f"Invalid date: {date_str}. Use YYYY-MM-DD format.")
        sys.exit(1)

    result = generate_report(date_str)
    _print_report(result, fmt)
    return result.markdown


def do_report_week(fmt: str = "markdown") -> str:
    """Generate a 7-day summary."""
    from aggregator.report import generate_week_report
    from db.db_common import init_aggregator_db

    init_aggregator_db()
    result = generate_week_report()
    _print_report(result, fmt)
    return result.markdown


def do_report_month(fmt: str = "markdown") -> str:
    """Generate month-to-date summary."""
    from aggregator.report import generate_report
    from db.db_common import init_aggregator_db
    from datetime import timedelta

    init_aggregator_db()

    today = date_mod.today()
    month_start = today.replace(day=1)

    # Aggregate all days this month
    results = []
    current = month_start
    while current <= today:
        r = generate_report(current.isoformat())
        if r.markdown.strip() and "No data" not in r.markdown:
            results.append(r)
        current += timedelta(days=1)

    total_elec = sum(r.total_electricity_cost for r in results)
    total_frontier = sum(r.total_frontier_cost for r in results)
    total_savings = sum(r.total_savings for r in results)
    total_tokens = sum(r.total_tokens for r in results)

    lines = []
    lines.append(f"# Rowbutt Cost Report — {month_start.isoformat()} to {today.isoformat()}")
    lines.append("")
    lines.append(f"**Days with data:** {len(results)}")
    lines.append(f"**Total tokens:** {_fmt_tokens(total_tokens)}")
    lines.append(f"**Total electricity:** {_fmt_usd(total_elec)}")
    lines.append(f"**Total frontier cost:** {_fmt_usd(total_frontier)}")
    lines.append(f"**Total savings:** {_fmt_usd(total_savings)}")
    lines.append("")
    for r in results:
        lines.append(f"- {r.date}: {_fmt_usd(r.total_savings)} saved ({r.machines} machines)")
    md = "\n".join(lines)

    if fmt == "json":
        import json as _json
        md = _json.dumps({
            "period": f"{month_start.isoformat()}/{today.isoformat()}",
            "days_with_data": len(results),
            "total_tokens": total_tokens,
            "total_electricity_cost": round(total_elec, 4),
            "total_frontier_cost": round(total_frontier, 4),
            "total_savings": round(total_savings, 4),
        }, indent=2)
    elif fmt == "csv":
        md = (
            "period_start,period_end,days_with_data,total_tokens,"
            "electricity_cost,frontier_cost,savings\n"
            f"{month_start.isoformat()},{today.isoformat()},{len(results)},{total_tokens},"
            f"{total_elec:.4f},{total_frontier:.4f},{total_savings:.4f}\n"
        )

    print(md)
    return md


def do_report_list() -> None:
    """List available report dates from DB and on-disk reports."""
    from db.db_common import connect_aggregator_db, get_reports_dir, init_aggregator_db
    from pathlib import Path

    init_aggregator_db()

    # Dates from DB (cost_reports)
    db_dates = []
    try:
        with connect_aggregator_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM cost_reports ORDER BY date DESC LIMIT 30"
            ).fetchall()
            db_dates = [r["date"] for r in rows]
    except Exception:
        pass

    # Dates from on-disk reports
    reports_dir = Path(get_reports_dir())
    file_dates = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("*.md"), reverse=True):
            file_dates.append(f.stem)

    if not db_dates and not file_dates:
        print("No reports found.")
        return

    combined = set(db_dates) | set(file_dates)
    print("Available reports:")
    print("")
    for d in sorted(combined, reverse=True):
        parts = []
        if d in db_dates:
            parts.append("DB")
        if d in file_dates:
            parts.append("file")
        source = f" ({', '.join(parts)})" if parts else ""
        print(f"  {d}{source}")


def _fmt_tokens(num: int) -> str:
    """Format token count (1234567 → '1.2M')."""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


def _print_report(result, fmt: str) -> None:
    if fmt == "markdown":
        print(result.markdown)
    elif fmt == "json":
        print(json.dumps({
            "date": result.date,
            "machines": result.machines,
            "total_tokens": result.total_tokens,
            "total_electricity_cost": result.total_electricity_cost,
            "total_frontier_cost": result.total_frontier_cost,
            "total_savings": result.total_savings,
        }, indent=2))
    elif fmt == "csv":
        print("date,machines,total_tokens,electricity_cost,frontier_cost,savings")
        print(f"{result.date},{result.machines},{result.total_tokens},"
              f"{result.total_electricity_cost},{result.total_frontier_cost},{result.total_savings}")
    else:
        print(result.markdown)


def _fmt_usd(amount: float) -> str:
    if abs(amount) < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"
