"""Rowbutt Dashboard — Phase 4 Integration Test.

Tests the web view: Flask app, templates, JSON API, CLI wiring.
"""

import os
import sys
import tempfile
import datetime

# ── Test infrastructure ──────────────────────────────────────

PASS = 0
FAIL = 0

def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {label}")
        return
    print(f"  ✓ {label}")


# ── Setup: fresh DB with seed data ────────────────────────────

# Use a temp DB for web tests
fake_db = tempfile.mktemp(suffix="_p4.db")
os.environ["ROWBUTT_AGENT_DB"] = fake_db
os.environ["ROWBUTT_AGGREGATOR_DB"] = fake_db

# Init DB + seed data via the aggregator pipeline
from db.db_common import init_aggregator_db, connect_aggregator_db
import json
init_aggregator_db()

today = datetime.date.today().isoformat()
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

with connect_aggregator_db() as conn:
    # Seed daily_summaries (two hosts, two days)
    for date_str in (yesterday, today):
        for host, inp, out, gpu_w in [
            ("ubuntu-server", 1200000, 850000, 200),
            ("operator-1",    400000, 300000, 150),
        ]:
            conn.execute("""
                INSERT INTO daily_summaries
                    (hostname, date, total_input, total_output, total_tokens,
                     avg_gpu_power_w, inference_time_minutes,
                     model_breakdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                host, date_str, inp, out, inp + out,
                gpu_w, 1440,  # avg_gpu_power_w, inference_time_minutes
                json.dumps({
                    "deepseek-v4-flash": {"input": inp, "output": out},
                })
            ))
    # Seed cost_reports
    for date_str in (yesterday, today):
        conn.execute("""
            INSERT OR REPLACE INTO cost_reports
                (hostname, date, inference_hours, system_power_w, gpu_avg_power_w,
                 total_power_kwh, electricity_cost,
                 frontier_input_cost, frontier_output_cost, frontier_total_cost,
                 savings, cost_per_1m_tokens)
            VALUES (?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?)
        """, (
            "ubuntu-server", date_str, 24.0, 75.0, 200.0,
            6.6, 0.726,
            0.18, 0.51, 0.69,
            -0.036, 0.35,
        ))
        conn.execute("""
            INSERT OR REPLACE INTO cost_reports
                (hostname, date, inference_hours, system_power_w, gpu_avg_power_w,
                 total_power_kwh, electricity_cost,
                 frontier_input_cost, frontier_output_cost, frontier_total_cost,
                 savings, cost_per_1m_tokens)
            VALUES (?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?)
        """, (
            "operator-1", date_str, 24.0, 75.0, 150.0,
            3.96, 0.4356,
            0.06, 0.18, 0.24,
            -0.1956, 0.42,
        ))



# ── Tests ─────────────────────────────────────────────────────

print("\n=== Phase 4: Web View — Flask App Tests ===\n")

from web.app import app  # noqa: E402

app.config["TESTING"] = True
client = app.test_client()


# 1. GET / — landing page renders (with seed data)
r = client.get("/")
check("GET / returns 200", r.status_code == 200)
check("GET / has report header", b"Rowbutt Dashboard" in r.data)
check("GET / shows latest date", today.encode() in r.data)
check("GET / shows tokens count", b"Total Tokens" in r.data)
check("GET / shows electricity cost", b"Electricity Cost" in r.data)
check("GET / shows frontier cost", b"Frontier API Cost" in r.data)
check("GET / shows savings", b"Savings" in r.data)
check("GET / shows both dates", yesterday.encode() in r.data and today.encode() in r.data)

# 2. GET /report/<today> — full report view
r = client.get(f"/report/{today}")
check(f"GET /report/{today} returns 200", r.status_code == 200)
check(f"Report page shows date", today.encode() in r.data)
check("Report page shows deepseek", b"deepseek" in r.data.lower())
check("Report page shows hostname", b"ubuntu-server" in r.data)
check("Report has back link", b"Back to overview" in r.data)

# 3. GET /report/<invalid> — 400
r = client.get("/report/not-a-date")
check("GET /report/invalid returns 400", r.status_code == 400)

# 4. GET /report/<future-date> — 404 (no data)
future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
r = client.get(f"/report/{future}")
check(f"GET /report/{future} returns 404", r.status_code == 404)

# 5. GET /api/v1/reports — JSON
r = client.get("/api/v1/reports")
check("GET /api/v1/reports returns 200", r.status_code == 200)
data = r.get_json()
check("API reports has dates key", "dates" in data)
check("API reports count >= 1", data["count"] >= 1)
check("API reports contains today", today in data["dates"])

# 6. GET /api/v1/report/<today> — JSON
r = client.get(f"/api/v1/report/{today}")
check(f"GET /api/v1/report/{today} returns 200", r.status_code == 200)
data = r.get_json()
check("API report has date", data.get("date") == today)
check("API report has markdown", "markdown" in data)
check("API report has electricity_cost", "total_electricity_cost" in data)
check("API report has frontier_cost", "total_frontier_cost" in data)
check("API report has savings", "total_savings" in data)
check("API report has two machines", data.get("machines") == 2)

# 7. GET /api/v1/report/<invalid> — 400
r = client.get("/api/v1/report/not-a-date")
check("GET /api/v1/report/invalid returns 400", r.status_code == 400)

# 8. GET /api/v1/report/<future> — 404
r = client.get(f"/api/v1/report/{future}")
check(f"GET /api/v1/report/{future} returns 404", r.status_code == 404)

# 9. CLI wiring
from click.testing import CliRunner  # noqa: E402
from cli.main import cli  # noqa: E402
runner = CliRunner()

r = runner.invoke(cli, ["web", "--help"])
check("CLI web --help exits 0", r.exit_code == 0)
check("CLI web --help shows start", "start" in r.output)

r = runner.invoke(cli, ["web", "start", "--help"])
check("CLI web start --help exits 0", r.exit_code == 0)
check("CLI web start shows --host", "--host" in r.output)
check("CLI web start shows --port", "--port" in r.output)

# 10. Empty DB — fresh start with no data
fake_db2 = tempfile.mktemp(suffix="_p4b.db")
os.environ["ROWBUTT_AGGREGATOR_DB"] = fake_db2
init_aggregator_db()

from web.app import app as app2  # noqa: E402
app2.config["TESTING"] = True
client2 = app2.test_client()

r = client2.get("/")
check("GET / with empty DB returns 200", r.status_code == 200)
check("GET / empty shows 'No Reports Yet'", b"No Reports Yet" in r.data)

r = client2.get("/api/v1/reports")
check("GET /api/v1/reports empty returns 200", r.status_code == 200)
data = r.get_json()
check("API reports empty has empty dates", data.get("dates") == [])

# 11. Markdown-to-HTML conversion
from web.app import _md_to_html  # noqa: E402
md = "# Header\n\n## Sub\n\n**Bold**\n\n| Col1 | Col2 |\n| --- | --- |\n| A | B |\n"
html = _md_to_html(md)
check("_md_to_html: h1 present", "<h1>Header</h1>" in html)
check("_md_to_html: h2 present", "<h2>Sub</h2>" in html)
check("_md_to_html: strong present", "<strong>Bold</strong>" in html)
check("_md_to_html: table present", "<table" in html)
check("_md_to_html: td present", "<td>A</td>" in html)


# ── Summary ───────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"Results:  {PASS}/{PASS+FAIL} passed  —  {'All passed ✓' if FAIL == 0 else f'{FAIL} FAILURES ✗'}")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
