#!/usr/bin/env python3
"""Phase 0 Smoke Test — Verify the complete skeleton works.

Tests:
1. Agent DB initialisation (schema creation)
2. Aggregator DB initialisation (schema creation + default pricing seed)
3. Insert token_events into agent DB
4. Insert system_samples into agent DB
5. Insert daily_summary into aggregator DB
6. Query pricing_cache from aggregator DB
7. Verify rollup query works
8. Tear down test databases

Exit code 0 = all tests pass. Non-zero = something failed.
"""

import os
import sys
import json
import tempfile
from pathlib import Path

# Point to test DBs so we don't clobber the real ones
os.environ["ROWBUTT_AGENT_DB"] = "/tmp/rowbutt_test_agent.db"
os.environ["ROWBUTT_AGGREGATOR_DB"] = "/tmp/rowbutt_test_aggregator.db"

from db.db_common import (
    init_agent_db,
    init_aggregator_db,
    connect_agent_db,
    connect_aggregator_db,
)
from db.migrations import migrate_agent, migrate_aggregator

passed = 0
failed = 0


def check(description: str, condition: bool):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {description}")
    else:
        failed += 1
        print(f"  ✗ {description}")


# ── Clean up before starting ────────────────────────────────
for p in ["/tmp/rowbutt_test_agent.db", "/tmp/rowbutt_test_aggregator.db"]:
    if os.path.exists(p):
        os.unlink(p)

print("=" * 60)
print("Rowbutt Dashboard — Phase 0 Smoke Test")
print("=" * 60)

# ── 1. Agent DB initialisation ──────────────────────────────
print("\n── Agent DB ──")
db_path = init_agent_db()
check("Agent DB file created", os.path.exists(db_path))

with connect_agent_db() as conn:
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]
    check("Agent tables exist: token_events", "token_events" in tables)
    check("Agent tables exist: system_samples", "system_samples" in tables)
    check("Agent tables exist: daily_rollups", "daily_rollups" in tables)
    check("Agent tables exist: agent_meta", "agent_meta" in tables)
    check("Agent has 4 tables total", len(tables) == 4)

    # Verify metadata seeded
    meta_rows = conn.execute("SELECT key, value FROM agent_meta").fetchall()
    meta = {r["key"]: r["value"] for r in meta_rows}
    check("Agent schema_version = 1", meta.get("schema_version") == "1")
    check("Agent agent_version = 0.1.0", meta.get("agent_version") == "0.1.0")

# ── 2. Aggregator DB initialisation ─────────────────────────
print("\n── Aggregator DB ──")
db_path = init_aggregator_db()
check("Aggregator DB file created", os.path.exists(db_path))

with connect_aggregator_db() as conn:
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]
    check("Aggregator tables exist: daily_summaries", "daily_summaries" in tables)
    check("Aggregator tables exist: pricing_cache", "pricing_cache" in tables)
    check("Aggregator tables exist: cost_reports", "cost_reports" in tables)
    check("Aggregator tables exist: aggregator_meta", "aggregator_meta" in tables)
    check("Aggregator has 4 tables total", len(tables) == 4)

    # Verify pricing seeded
    prices = conn.execute("SELECT COUNT(*) as c FROM pricing_cache").fetchone()["c"]
    check(f"Pricing cache seeded ({prices} models)", prices >= 8)

    # Verify meta
    meta_rows = conn.execute("SELECT key, value FROM aggregator_meta").fetchall()
    meta = {r["key"]: r["value"] for r in meta_rows}
    check("Aggregator schema_version = 1", meta.get("schema_version") == "1")

# ── 3. Insert token_events ──────────────────────────────────
print("\n── Data Insertion ──")
with connect_agent_db() as conn:
    conn.execute(
        """INSERT INTO token_events
           (recorded_at, model, input_tokens, output_tokens, total_tokens,
            session_id, source, bucket_hour, bucket_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("2026-08-23T10:00:00", "deepseek-ai/DeepSeek-V4-Flash",
         1200, 850, 2050, "sess-001", "ollama", 8, "2026-08-23")
    )
    conn.execute(
        """INSERT INTO token_events
           (recorded_at, model, input_tokens, output_tokens, total_tokens,
            session_id, source, bucket_hour, bucket_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("2026-08-23T14:00:00", "deepseek-ai/DeepSeek-R1",
         400, 300, 700, "sess-002", "ollama", 12, "2026-08-23")
    )
    count = conn.execute("SELECT COUNT(*) as c FROM token_events").fetchone()["c"]
    check("Inserted 2 token events", count == 2)

# ── 4. Insert system_samples ────────────────────────────────
with connect_agent_db() as conn:
    conn.execute(
        """INSERT INTO system_samples
           (sampled_at, mem_total_gb, mem_used_gb, mem_pct,
            temp_cpu_avg, temp_cpu_max, temp_gpu, gpu_power_w, gpu_util_pct,
            load_1m, load_5m, load_15m, bucket_hour, bucket_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("2026-08-23T10:05:00", 64.0, 42.5, 66.4,
         62.3, 68.0, 71.0, 185.0, 82.0,
         2.3, 1.8, 1.5, 8, "2026-08-23")
    )
    count = conn.execute("SELECT COUNT(*) as c FROM system_samples").fetchone()["c"]
    check("Inserted 1 system sample", count == 1)

# ── 5. Insert daily_summary into aggregator ─────────────────
with connect_aggregator_db() as conn:
    model_breakdown = json.dumps({
        "deepseek-ai/DeepSeek-V4-Flash": {"input": 1200, "output": 850, "sessions": 1},
        "deepseek-ai/DeepSeek-R1": {"input": 400, "output": 300, "sessions": 1},
    })
    conn.execute(
        """INSERT INTO daily_summaries
           (hostname, date, total_input, total_output, total_tokens,
            model_breakdown, avg_mem_pct, avg_temp_cpu, avg_temp_gpu,
            avg_gpu_power_w, inference_time_minutes, agent_version,
            collectors_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("ubuntu-server", "2026-08-23",
         1600, 1150, 2750,
         model_breakdown, 66.4, 62.3, 71.0,
         185.0, 320, "0.1.0", '["llm_tokens", "system"]')
    )
    count = conn.execute("SELECT COUNT(*) as c FROM daily_summaries").fetchone()["c"]
    check("Inserted 1 daily summary", count == 1)

# ── 6. Query pricing_cache ──────────────────────────────────
with connect_aggregator_db() as conn:
    deepseek_price = conn.execute(
        "SELECT input_price, output_price FROM pricing_cache WHERE model = ?",
        ("deepseek-ai/DeepSeek-V4-Flash",),
    ).fetchone()
    check("DeepSeek V4 Flash pricing found", deepseek_price is not None)
    if deepseek_price:
        check("DeepSeek input price is 0.15", abs(deepseek_price["input_price"] - 0.15) < 0.001)
        check("DeepSeek output price is 0.60", abs(deepseek_price["output_price"] - 0.60) < 0.001)

# ── 7. Verify rollup query ──────────────────────────────────
with connect_agent_db() as conn:
    # Simulate a rollup by querying token_events grouped by bucket
    rollup = conn.execute(
        """SELECT bucket_date, bucket_hour,
                  SUM(input_tokens) as total_in,
                  SUM(output_tokens) as total_out,
                  SUM(total_tokens) as total_tok,
                  COUNT(*) as sessions
           FROM token_events
           GROUP BY bucket_date, bucket_hour
           ORDER BY bucket_hour""",
    ).fetchall()
    check("Rollup query returns rows", len(rollup) >= 1)
    check("Rollup sums tokens correctly", rollup[0]["total_in"] >= 1200)

# ── 8. Migration run (no-op, just verify it doesn't error) ──
print("\n── Migrations ──")
try:
    migrate_agent()
    migrate_aggregator()
    check("Agent migration run (no-op)", True)
    check("Aggregator migration run (no-op)", True)
except Exception as e:
    check(f"Migration run failed: {e}", False)

# ── Summary ─────────────────────────────────────────────────
print(f"\n{'=' * 60}")
total = passed + failed
print(f"Results:  {passed}/{total} passed", end="")
if failed > 0:
    print(f"  —  {failed} FAILED ❌", file=sys.stderr)
else:
    print("  —  All passed ✓")
print(f"{'=' * 60}")
sys.exit(1 if failed > 0 else 0)
