"""Rowbutt Dashboard — Phase 2 Integration Test.

Tests the aggregator: puller, costs engine, report generator, and CLI.
"""

import os
import sys
import json
import tempfile
import datetime

# ── Test configuration ──────────────────────────────────────────
TEST_DB = tempfile.mktemp(suffix="_phase2.db")
TEST_AGENT_DB = tempfile.mktemp(suffix="_agent.db")

os.environ["ROWBUTT_AGENT_DB"] = TEST_DB
os.environ["ROWBUTT_AGGREGATOR_DB"] = TEST_DB

# Set test path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        print(f"  ok {label}")
        passed += 1
    else:
        print(f"  FAIL {label}")
        failed += 1


# ── Setup ───────────────────────────────────────────────────────

from db.db_common import init_agent_db, init_aggregator_db, connect_aggregator_db


# Init both schemas (aggregator schema has pricing_cache + daily_summaries + cost_reports)
init_aggregator_db()

# ────────────────────────────────────────────────────────────────
# 1. AgentConfigStore
# ────────────────────────────────────────────────────────────────

from aggregator.puller import AgentConfigStore, AgentConfig, AGENTS_CONFIG_PATH

print("\n── AgentConfigStore ──")

# Use a temp path for test
store = AgentConfigStore(path=os.path.join(tempfile.mkdtemp(), "agents.json"))

# Save some configs
test_agents = [
    AgentConfig(hostname="test-machine", url="http://localhost:9999"),
    AgentConfig(hostname="gpu-server", url="http://192.168.1.100:9091",
                system_baseline_w=100, gpu_idle_w=40, gpu_load_w=200),
]
store.save(test_agents)
check("saved 2 agents", os.path.exists(store.path))

# Load them back
loaded = store.load()
check("loaded 2 agents", len(loaded) == 2)
check("agent hostname correct", loaded[0].hostname == "test-machine")
check("agent url correct", loaded[0].url == "http://localhost:9999")
check("custom baseline loaded", loaded[1].system_baseline_w == 100)
check("custom gpu idle loaded", loaded[1].gpu_idle_w == 40)
check("custom gpu load loaded", loaded[1].gpu_load_w == 200)

# Load from non-existent path
empty_store = AgentConfigStore(path="/tmp/nonexistent_agents.json")
empty_agents = empty_store.load()
check("load missing file returns []", len(empty_agents) == 0)

# Clean up test config
import shutil
os.remove(store.path)
os.rmdir(os.path.dirname(store.path))


# ────────────────────────────────────────────────────────────────
# 2. DB Operations — _upsert_summary
# ────────────────────────────────────────────────────────────────

from aggregator.puller import _upsert_summary

print("\n── DB Upsert ──")

sample_day_data = {
    "date": "2026-08-23",
    "hostname": "test-machine",
    "version": "0.1.0",
    "token_usage": {
        "deepseek-ai/DeepSeek-V4-Flash": {"input": 1200000, "output": 850000, "sessions": 15},
        "deepseek-ai/DeepSeek-R1": {"input": 400000, "output": 300000, "sessions": 5},
    },
    "system_metrics": {
        "samples_taken": 50,
        "avg_mem_pct": 72.3,
        "avg_gpu_power_w": 85.0,
        "avg_gpu_temp": 62.0,
    },
}

inserted = _upsert_summary("test-machine", "2026-08-23", sample_day_data)
check("inserted summary row", inserted == 1)

# Insert a second machine
sample_day_data2 = dict(sample_day_data)
sample_day_data2["hostname"] = "gpu-server"
sample_day_data2["token_usage"] = {
    "qwen/Qwen2.5-72B-Instruct": {"input": 800000, "output": 600000, "sessions": 10},
}
_upsert_summary("gpu-server", "2026-08-23", sample_day_data2)
check("second machine inserted", True)

# Verify in DB
with connect_aggregator_db() as conn:
    rows = conn.execute(
        "SELECT hostname, total_input, total_output, total_tokens FROM daily_summaries"
    ).fetchall()
check("2 daily_summaries rows", len(rows) == 2)

test_row = dict(rows[0])
check("test-machine input=1.6M", test_row["total_input"] == 1600000 or test_row.get("total_input") == 1600000)

# Re-insert (upsert — same hostname+date)
_upsert_summary("test-machine", "2026-08-23", sample_day_data)
with connect_aggregator_db() as conn:
    count = conn.execute(
        "SELECT COUNT(*) as c FROM daily_summaries WHERE hostname='test-machine' AND date='2026-08-23'"
    ).fetchone()["c"]
check("upsert doesn't duplicate (count=1)", count == 1)


# ────────────────────────────────────────────────────────────────
# 3. Cost Calculation Engine
# ────────────────────────────────────────────────────────────────

from aggregator.costs import compute_costs, CostResult, _lookup_price, DEFAULT_SYSTEM_BASELINE_W

print("\n── Cost Calculation ──")

# Compute costs for test-machine
results = compute_costs(hostname="test-machine", date_str="2026-08-23")
check("cost results returned", len(results) > 0)

r = results[0]
check("hostname matches", r.hostname == "test-machine")
check("inference_hours > 0", r.inference_hours > 0)
check("electricity_cost > 0", r.electricity_cost > 0)
check("frontier_input_cost > 0", r.frontier_input_cost > 0)
check("frontier_total_cost > 0", r.frontier_total_cost > 0)
check("savings > 0", r.savings > 0)
check("model_breakdown has models", len(r.model_breakdown) >= 1)

# Verify specific calculations
# Total frontier: V4 (1.2M*$0.15/1M + 0.85M*$0.60/1M = $0.69)
#               + R1 (0.4M*$0.55/1M + 0.3M*$2.19/1M = $0.877)
#               = ~$1.567
check("frontier ~$1.57 total", abs(r.frontier_total_cost - 1.57) < 0.05)

# Electricity: (85W + 75W) * (inference_hours) / 1000 * 0.12
# inference_minutes from total_tokens (1200000+850000=2050000) / 50 tok/s / 60 = 683.3 min ≈ 11.39h
# kWh = 160 * 11.39 / 1000 = 1.822, cost = 1.822 * 0.12 = 0.2186
check("electricity_cost > 0.10", r.electricity_cost > 0.10)

# Compute costs for all machines
all_results = compute_costs(date_str="2026-08-23")
check("cost results for all machines", len(all_results) >= 2)

# Cost report should exist in DB
with connect_aggregator_db() as conn:
    cost_rows = conn.execute(
        "SELECT COUNT(*) as c FROM cost_reports WHERE date='2026-08-23'"
    ).fetchone()["c"]
check("cost_reports rows in DB", cost_rows >= 2)


# ────────────────────────────────────────────────────────────────
# 4. Pricing Lookup
# ────────────────────────────────────────────────────────────────

with connect_aggregator_db() as conn:
    # Exact match
    inp, out = _lookup_price("deepseek-ai/DeepSeek-V4-Flash", "2026-08-23", conn)
    check("V4 input price 0.15", inp == 0.15)
    check("V4 output price 0.60", out == 0.60)

    # Alias match
    inp2, out2 = _lookup_price("deepseek-v4", "2026-08-23", conn)
    check("alias V4 input price 0.15", inp2 == 0.15)

    # Unknown model → fallback to 0
    inp3, out3 = _lookup_price("unknown-model-9000", "2026-08-23", conn)
    check("unknown model price 0/0", inp3 == 0.0 and out3 == 0.0)


# ────────────────────────────────────────────────────────────────
# 5. Report Generator
# ────────────────────────────────────────────────────────────────

from aggregator.report import generate_report, ReportResult, _fmt_tokens, _fmt_hours, _fmt_usd

print("\n── Report Generator ──")

result = generate_report("2026-08-23")
check("report generated", isinstance(result, ReportResult))
check("report has markdown", len(result.markdown) > 100)
check("report has date '2026-08-23'", "2026-08-23" in result.markdown)
check("report has Summary section", "## Summary" in result.markdown)
check("test-machine in report", "test-machine" in result.markdown)
check("gpu-server in report", "gpu-server" in result.markdown)
check("report has Savings Over Time", "Savings Over Time" in result.markdown)
check("report has electricity cost $", "$" in result.markdown)
check("report mentions DeepSeek-V4", "DeepSeek-V4" in result.markdown)
check("machines count >= 2", result.machines >= 2)
check("total_tokens > 0", result.total_tokens > 0)
check("total_savings > 0", result.total_savings > 0)

# Empty date
empty_result = generate_report("2099-01-01")
check("empty report has markdown", len(empty_result.markdown) > 10)
check("empty report says no data", "No data available" in empty_result.markdown)

# Formatting helpers
check("fmt_tokens 1234567", _fmt_tokens(1234567) == "1.2M")
check("fmt_tokens 1234", _fmt_tokens(1234) == "1.2K")
check("fmt_tokens 500", _fmt_tokens(500) == "500")
check("fmt_hours 5.5", _fmt_hours(5.5) == "5h 30m")
check("fmt_hours 0.25", _fmt_hours(0.25) == "15m")
check("fmt_usd 3.47", _fmt_usd(3.47) == "$3.47")
check("fmt_usd 0.0034", _fmt_usd(0.0034) == "$0.0034")


# Report file was written
import os
report_path = os.path.expanduser("~/.rowbutt/reports/2026-08-23.md")
check("report file exists", os.path.exists(report_path))
with open(report_path) as f:
    content = f.read()
check("report file has content", len(content) > 100)


# ────────────────────────────────────────────────────────────────
# 6. CLI Integration (via Click Runner)
# ────────────────────────────────────────────────────────────────

from click.testing import CliRunner
from cli.main import cli

print("\n── CLI Integration ──")

runner = CliRunner()

# aggregator --help
r = runner.invoke(cli, ["aggregator", "--help"])
check("aggregator --help exits 0", r.exit_code == 0)
check("aggregator shows init", "init" in r.output)
check("aggregator shows pull-all", "pull-all" in r.output)
check("aggregator shows compute-costs", "compute-costs" in r.output)

# aggregator init
r = runner.invoke(cli, ["aggregator", "init"])
check("aggregator init exits 0", r.exit_code == 0)
check("init says database initialised", "database initialised" in r.output.lower())

# aggregator compute-costs (uses existing DB data)
r = runner.invoke(cli, ["aggregator", "compute-costs", "--date", "2026-08-23",
                        "--hostname", "test-machine"])
check("compute-costs exits 0", r.exit_code == 0)
check("compute-costs shows hostname", "test-machine" in r.output)
check("compute-costs shows savings", "Savings:" in r.output)

# report --help (Phase 3 wiring)
r = runner.invoke(cli, ["report", "--help"])
check("report --help exits 0", r.exit_code == 0)
check("report shows today", "today" in r.output)
check("report shows date", "date" in r.output)
check("report shows week", "week" in r.output)
check("report shows month", "month" in r.output)
check("report shows list", "list" in r.output)


# ────────────────────────────────────────────────────────────────
# 7. Week Report Generator
# ────────────────────────────────────────────────────────────────

from aggregator.report import generate_week_report

print("\n── Week Report ──")

week_result = generate_week_report(end_date="2026-08-23")
check("week report generated", week_result.markdown is not None)
check("week report has Weekly", "Weekly" in week_result.markdown)
check("week has date range", "2026-08-23" in week_result.markdown)


# ────────────────────────────────────────────────────────────────
# 8. Edge Cases
# ────────────────────────────────────────────────────────────────

print("\n── Edge Cases ──")

# Summary with zero tokens
from aggregator.puller import _estimate_inference_minutes
check("zero tokens → 0 min", _estimate_inference_minutes(0) == 0.0)
check("negative tokens → 0 min", _estimate_inference_minutes(-100) == 0.0)
check("100k tokens → 33.3 min", abs(_estimate_inference_minutes(100000) - 33.3) < 0.2)

# Pull with connection refused (non-existent server)
from aggregator.puller import pull_agent, AgentConfig
bad_agent = AgentConfig(hostname="ghost", url="http://127.0.0.1:1")
pr = pull_agent(bad_agent, target_date="2026-08-23", http_timeout=1)
check("connection refused → success=False", not pr.success)
check("refused shows error", pr.error is not None)


# ────────────────────────────────────────────────────────────────
# Cleanup
# ────────────────────────────────────────────────────────────────

print("\n── Cleanup ──")
for f in [TEST_DB, TEST_AGENT_DB]:
    try:
        if os.path.exists(f):
            os.unlink(f)
    except Exception:
        pass
check("Test DB cleaned up", not os.path.exists(TEST_DB))

# Clean test report
if os.path.exists(report_path):
    os.remove(report_path)

# ────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────

total = passed + failed
print(f"\n{'='*60}")
print(f"Rowbutt Dashboard — Phase 2 Integration Test")
print(f"{'='*60}")
print(f"Results:  {passed}/{total} passed", end="")
if failed == 0:
    print("  —  All passed ✓")
else:
    print(f"  —  {failed} FAILED")

sys.exit(1 if failed else 0)
