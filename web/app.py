"""Rowbutt Dashboard Web View — Flask server for browsing cost reports.

Endpoints:
  GET  /                  — Landing page with latest report + date list
  GET  /report/<date>     — Full report rendered as HTML
  GET  /api/v1/reports    — JSON list of available dates
  GET  /api/v1/report/<date>  — JSON of the full report data
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date as date_mod
from pathlib import Path

import flask

from aggregator.report import generate_report, ReportResult
from db.db_common import (
    connect_aggregator_db,
    init_aggregator_db,
    get_reports_dir,
)

logger = logging.getLogger(__name__)

# Flask app
app = flask.Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


# ── Helpers ──────────────────────────────────────────────────


def _get_report_dates() -> list[str]:
    """Return sorted list of ISO dates that have cost_reports in the DB."""
    try:
        init_aggregator_db()
        with connect_aggregator_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM cost_reports ORDER BY date DESC"
            ).fetchall()
            return [r["date"] for r in rows]
    except Exception:
        return []


def _latest_date() -> str | None:
    """Return the most recent date with cost data."""
    dates = _get_report_dates()
    return dates[0] if dates else None


def _load_report(date_str: str) -> ReportResult | None:
    """Generate a ReportResult for *date_str*, or None if no data."""
    try:
        init_aggregator_db()
        result = generate_report(date_str)
        if result.markdown and "No data" not in result.markdown:
            return result
    except Exception:
        pass
    return None


def _md_to_html(markdown: str) -> str:
    """Convert Markdown to basic HTML (line-based, tables with <table>)."""
    lines = markdown.split("\n")
    html_parts = []
    in_table = False
    for line in lines:
        # Headers
        if line.startswith("# "):
            html_parts.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_parts.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_parts.append(f"<h3>{line[4:]}</h3>")
        # Bold
        elif line.startswith("**") and "**" in line[2:]:
            html_parts.append(f"<p><strong>{line.strip('*')}</strong></p>")
        # Table rows
        elif line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if not in_table:
                html_parts.append('<table class="report-table">')
                in_table = True
            # Detect header separator row
            if all(c in ("---", "-", "") for c in cells):
                continue
            tag = "th" if in_table and any("**" in c for c in cells) else "td"
            # Clean bold markers inside cells
            cells = [c.replace("**", "").strip() for c in cells]
            html_parts.append(
                f"  <tr>{''.join(f'<{tag}>{c}</{tag}>' for c in cells)}</tr>"
            )
        else:
            if in_table:
                html_parts.append("</table>")
                in_table = False
            if line.strip() == "":
                html_parts.append("<br>")
            elif line.startswith("- "):
                html_parts.append(f"<li>{line[2:]}</li>")
            elif line.strip():
                html_parts.append(f"<p>{line}</p>")
    if in_table:
        html_parts.append("</table>")
    return "\n".join(html_parts)


# ── Routes ───────────────────────────────────────────────────


@app.route("/")
def index():
    """Landing page: latest report + list of dates."""
    dates = _get_report_dates()
    latest = _load_report(_latest_date()) if dates else None
    return flask.render_template(
        "index.html",
        dates=dates,
        latest_report=latest,
        md_html=_md_to_html(latest.markdown) if latest else None,
        latest_date=latest.date if latest else None,
    )


@app.route("/report/<date_str>")
def report_view(date_str: str):
    """Full Markdown report rendered as HTML."""
    try:
        date_mod.fromisoformat(date_str)
    except ValueError:
        flask.abort(400, description=f"Invalid date: {date_str}")

    result = _load_report(date_str)
    if not result:
        flask.abort(404, description=f"No report found for {date_str}")

    dates = _get_report_dates()
    html_content = _md_to_html(result.markdown)
    return flask.render_template(
        "report.html",
        report_date=date_str,
        dates=dates,
        html_content=html_content,
        report=result,
    )


@app.route("/api/v1/reports")
def api_reports():
    """JSON list of available report dates."""
    dates = _get_report_dates()
    return flask.jsonify({"dates": dates, "count": len(dates)})


@app.route("/api/v1/report/<date_str>")
def api_report(date_str: str):
    """JSON with the full report data for a date."""
    try:
        date_mod.fromisoformat(date_str)
    except ValueError:
        return flask.jsonify({"error": f"Invalid date: {date_str}"}), 400

    result = _load_report(date_str)
    if not result:
        return flask.jsonify({"error": f"No report found for {date_str}"}), 404

    return flask.jsonify({
        "date": result.date,
        "machines": result.machines,
        "total_tokens": result.total_tokens,
        "total_electricity_cost": result.total_electricity_cost,
        "total_frontier_cost": result.total_frontier_cost,
        "total_savings": result.total_savings,
        "markdown": result.markdown,
    })


# ── CLI entry point ──────────────────────────────────────────


def serve(host: str = "0.0.0.0", port: int = 8123, debug: bool = False) -> None:
    """Start the web server."""
    init_aggregator_db()
    print(f"Rowbutt Dashboard Web view starting on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
