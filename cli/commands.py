"""Rowbutt CLI — All subcommand implementations."""

import sys
import os
import json
import click
from pathlib import Path
from datetime import date as date_mod

from config.defaults import ROWBUTT_DIR, REPORTS_DIR, AGENT_HOST, AGENT_PORT
from db.db_common import init_agent_db, init_aggregator_db, get_reports_dir


# ── Shared helpers ──────────────────────────────────────────


def _ensure_rowbutt_dir():
    Path(ROWBUTT_DIR).mkdir(parents=True, exist_ok=True)
    Path(get_reports_dir()).mkdir(parents=True, exist_ok=True)


def _save_report(date_str: str, markdown: str) -> str:
    """Write report markdown to ~/.rowbutt/reports/YYYY-MM-DD.md."""
    path = Path(REPORTS_DIR) / f"{date_str}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown)
    return str(path)


def _deliver_telegram(markdown: str) -> None:
    """Send report via Telegram (if script available)."""
    import shutil, subprocess
    script = os.path.expanduser(
        "~/.hermes/skills/telegram-send/scripts/telegram-send"
    )
    if not os.path.exists(script):
        click.echo("⚠ Telegram send script not found at: {script}")
        click.echo("  Install via: hermes skill install telegram-send")
        return
    try:
        subprocess.run([script, "--message", "📊 *Daily Cost Report*",
                        "--markdown", markdown],
                       timeout=30, capture_output=True, text=True)
        click.echo("  Report delivered via Telegram.")
    except Exception as e:
        click.echo(f"⚠ Telegram delivery failed: {e}")


# ── Agent commands ──────────────────────────────────────────


@click.group(name="agent")
def agent_group():
    """Manage the per-machine data collection agent."""


@agent_group.command(name="init")
def agent_init():
    """Initialise the agent database and config directory."""
    _ensure_rowbutt_dir()
    db_path = init_agent_db()
    click.echo(f"✓ Agent database initialised at: {db_path}")
    click.echo("  Next: run 'rowbutt agent start' to begin collecting data.")


@agent_group.command(name="start")
@click.option("--host", default=AGENT_HOST, help="Bind address")
@click.option("--port", type=int, default=AGENT_PORT, help="Bind port")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def agent_start(host, port, debug):
    """Start the agent poll loop and HTTP server (foreground)."""
    from agent.cli import run_agent
    run_agent(host=host, port=port, debug=debug)


@agent_group.command(name="status")
def agent_status():
    """Show current agent collection status."""
    try:
        from db.db_common import connect_agent_db
        with connect_agent_db() as conn:
            meta = {r["key"]: r["value"] for r in
                    conn.execute("SELECT key, value FROM agent_meta").fetchall()}
            token_count = conn.execute(
                "SELECT COUNT(*) as c FROM token_events"
            ).fetchone()["c"]
            sample_count = conn.execute(
                "SELECT COUNT(*) as c FROM system_samples"
            ).fetchone()["c"]
            rollup_count = conn.execute(
                "SELECT COUNT(*) as c FROM daily_rollups"
            ).fetchone()["c"]

        click.echo(f"Agent version:      {meta.get('agent_version', '?')}")
        click.echo(f"Schema version:     {meta.get('schema_version', '?')}")
        click.echo(f"Token events:       {token_count}")
        click.echo(f"System samples:     {sample_count}")
        click.echo(f"Daily rollups:      {rollup_count}")
        click.echo("  (Agent is not running — data is from previous sessions)")
    except Exception as e:
        click.echo(f"✗ Could not read agent database: {e}")
        sys.exit(1)


@agent_group.command(name="day-summary")
@click.option("--date", "-d", default=None, help="ISO date (default: today)")
def agent_day_summary(date):
    """Fetch a day summary from the local agent DB and print as JSON."""
    from datetime import date as date_mod
    if date is None:
        date = date_mod.today().isoformat()

    try:
        from db.db_common import connect_agent_db
        with connect_agent_db() as conn:
            rollups = conn.execute(
                "SELECT * FROM daily_rollups WHERE date = ? ORDER BY bucket_hour",
                (date,),
            ).fetchall()
    except Exception as e:
        click.echo(f"✗ Could not read agent database: {e}")
        sys.exit(1)

    if not rollups:
        click.echo(json.dumps({"date": date, "hostname": "?", "token_usage": {},
                                "system_metrics": {}, "rollups": []}, indent=2))
        return

    # Aggregate across buckets
    total_in = sum(r["total_input"] for r in rollups)
    total_out = sum(r["total_output"] for r in rollups)
    click.echo(json.dumps({
        "date": date,
        "rollup_buckets": len(rollups),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_tokens": total_in + total_out,
    }, indent=2))


# ── Aggregator commands ─────────────────────────────────────


@click.group(name="aggregator")
def aggregator_group():
    """Manage the central data aggregator and cost engine."""


@aggregator_group.command(name="init")
def aggregator_init():
    """Initialise the aggregator database and config directory."""
    _ensure_rowbutt_dir()
    db_path = init_aggregator_db()
    click.echo(f"✓ Aggregator database initialised at: {db_path}")
    click.echo("  Next: configure agents in ~/.rowbutt/aggregator.yaml")
    click.echo("  Then: run 'rowbutt aggregator pull-all' to fetch data.")


@aggregator_group.command(name="pull-all")
@click.option("--date", "-d", default=None, help="ISO date (default: today)")
def aggregator_pull_all(date):
    """Pull day-summaries from all configured agents."""
    from aggregator.cli import do_pull_all
    do_pull_all(date_str=date)


@aggregator_group.command(name="compute-costs")
@click.option("--hostname", "-n", default=None, help="Filter to one machine")
@click.option("--date", "-d", default=None, help="ISO date (default: today)")
def aggregator_compute_costs(hostname, date):
    """Compute electricity and frontier costs for pulled data."""
    from aggregator.cli import do_compute_costs
    do_compute_costs(hostname=hostname, date_str=date)


# ── Report commands ─────────────────────────────────────────


@click.group(name="report")
def report_group():
    """View and generate daily cost reports."""


@report_group.command(name="today")
@click.option("--format", "-f", "fmt", default="markdown",
              type=click.Choice(["markdown", "json", "csv"]))
@click.option("--deliver", default=None,
              help="Delivery channel (e.g. 'telegram')")
@click.option("--save", "-s", is_flag=True, default=False,
              help="Save markdown to ~/.rowbutt/reports/")
def report_today(fmt, deliver, save):
    """Generate today's cost report."""
    from aggregator.cli import do_report_today
    md = do_report_today(fmt=fmt)
    if save and fmt == "markdown":
        path = _save_report(date_mod.today().isoformat(), md)
        click.echo(f"\nReport saved to: {path}")
    if deliver == "telegram":
        _deliver_telegram(md)


@report_group.command(name="date")
@click.argument("date")
@click.option("--format", "-f", "fmt", default="markdown",
              type=click.Choice(["markdown", "json", "csv"]))
@click.option("--save", "-s", is_flag=True, default=False,
              help="Save markdown to ~/.rowbutt/reports/")
@click.option("--deliver", default=None,
              help="Delivery channel (e.g. 'telegram')")
def report_date(date, fmt, save, deliver):
    """Generate a report for a specific ISO date (YYYY-MM-DD)."""
    from aggregator.cli import do_report_date
    md = do_report_date(date, fmt=fmt)
    if save and fmt == "markdown":
        path = _save_report(date, md)
        click.echo(f"\nReport saved to: {path}")
    if deliver == "telegram":
        _deliver_telegram(md)


@report_group.command(name="week")
@click.option("--format", "-f", "fmt", default="markdown",
              type=click.Choice(["markdown", "json", "csv"]))
@click.option("--deliver", default=None,
              help="Delivery channel (e.g. 'telegram')")
@click.option("--save", "-s", is_flag=True, default=False,
              help="Save markdown to ~/.rowbutt/reports/")
def report_week(fmt, deliver, save):
    """Generate a 7-day summary report."""
    from aggregator.cli import do_report_week
    md = do_report_week(fmt=fmt)
    if save and fmt == "markdown":
        path = _save_report(f"{date_mod.today().isoformat()}-week", md)
        click.echo(f"\nReport saved to: {path}")
    if deliver == "telegram":
        _deliver_telegram(md)


@report_group.command(name="month")
@click.option("--format", "-f", "fmt", default="markdown",
              type=click.Choice(["markdown", "json", "csv"]))
@click.option("--deliver", default=None,
              help="Delivery channel (e.g. 'telegram')")
def report_month(fmt, deliver):
    """Generate a month-to-date summary."""
    from aggregator.cli import do_report_month
    md = do_report_month(fmt=fmt)
    if deliver == "telegram":
        _deliver_telegram(md)


@report_group.command(name="list")
def report_list():
    """List available report dates (DB + on-disk reports)."""
    from aggregator.cli import do_report_list
    do_report_list()


# ── Web server commands ──────────────────────────────────────


@click.group(name="web")
def web_group():
    """Start and manage the web UI."""


@web_group.command(name="start")
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", type=int, default=8123, help="Bind port")
@click.option("--debug", is_flag=True, help="Enable Flask debug mode")
def web_start(host, port, debug):
    """Start the Rowbutt Dashboard web server (foreground)."""
    from web.app import serve
    serve(host=host, port=port, debug=debug)
