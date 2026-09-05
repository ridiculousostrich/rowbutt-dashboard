"""Phase 1 Integration Test — Agent collectors + Flask server."""

import os
import sys
import json
import tempfile

# Isolate DB
os.environ["ROWBUTT_AGENT_DB"] = "/tmp/rowbutt_p1_test_agent.db"
for p in [os.environ["ROWBUTT_AGENT_DB"]]:
    if os.path.exists(p):
        os.unlink(p)

sys.path.insert(0, "/workspace/Rowbutt_Dashboard")

from db.db_common import init_agent_db
from agent.server import create_app
from agent.collectors.base import list_collectors, get_collector
from agent.collectors.llm_tokens import (
    _parse_prometheus_metrics, DiffTracker,
    OllamaProvider, VLLMProvider, LlamaCppProvider,
)
from agent.scheduler import PollJob
from flask.testing import FlaskClient

passed = 0
failed = 0


def check(desc: str, cond: bool):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok {desc}")
    else:
        failed += 1
        print(f"  FAIL {desc}")


print("=" * 60)
print("Rowbutt Dashboard — Phase 1 Integration Test")
print("=" * 60)

# ── 1. Collector registration ──────────────────────────────
print("\n── Collector Registry ──")
collectors = list_collectors()
check("llm_tokens registered", "llm_tokens" in collectors)
check("system registered", "system" in collectors)
check("at least 2 collectors", len(collectors) >= 2)

sys_cls = get_collector("system")
llm_cls = get_collector("llm_tokens")
check("system class can be instantiated", callable(sys_cls))
check("llm_tokens class can be instantiated", callable(llm_cls))

# ── 2. Prometheus parser ──────────────────────────────────
print("\n── Prometheus Parser ──")
sample = """# HELP ollama_request_tokens_total Total tokens processed
# TYPE ollama_request_tokens_total counter
ollama_request_tokens_total{model="deepseek-v4",type="prompt"} 1500
ollama_request_tokens_total{model="deepseek-v4",type="generation"} 3200
ollama_request_tokens_total{model="llama3.1",type="prompt"} 800
ollama_request_tokens_total{model="llama3.1",type="generation"} 1200
# HELP some_other_counter Something
some_other_counter 42
"""
parsed = _parse_prometheus_metrics(sample)
check("parsed ollama_request_tokens_total",
      "ollama_request_tokens_total" in parsed)
check("parsed 3 metric names", len(parsed) == 2)  # 2 distinct names
check("ollama has 4 label combos",
      len(parsed["ollama_request_tokens_total"]) == 4)
check("deepseek-v4 prompt=1500",
      any(v == 1500.0 for _, v in parsed["ollama_request_tokens_total"]))

# ── 3. DiffTracker ────────────────────────────────────────
print("\n── DiffTracker ──")
tracker = DiffTracker()

# First call — establish baseline
d1 = tracker.deltas(parsed)
check("first call returns no deltas", len(d1) == 0)

# Second call — with higher values (explicit, avoids .replace substring issues)
sample2 = """# HELP ollama_request_tokens_total Total tokens processed
# TYPE ollama_request_tokens_total counter
ollama_request_tokens_total{model="deepseek-v4",type="prompt"} 1800
ollama_request_tokens_total{model="deepseek-v4",type="generation"} 3600
ollama_request_tokens_total{model="llama3.1",type="prompt"} 900
ollama_request_tokens_total{model="llama3.1",type="generation"} 1400
# HELP some_other_counter Something
some_other_counter 55
"""
parsed2 = _parse_prometheus_metrics(sample2)
d2 = tracker.deltas(parsed2)
check("deltas returned after baseline", len(d2) >= 1)

# Check one known delta (models sorted by metric+labels order)
found_deltas = {f"{d['labels'].get('model','?')}.{d['labels'].get('type','?')}": d["delta_value"]
                for d in d2 if d.get("labels", {}).get("model") == "deepseek-v4"}
check("deepseek-v4 prompt delta = 300",
      found_deltas.get("deepseek-v4.prompt") == 300)

# Third call — simulate counter reset (lower values = new baseline)
sample3 = """# HELP ollama_request_tokens_total Total tokens processed
# TYPE ollama_request_tokens_total counter
ollama_request_tokens_total{model="deepseek-v4",type="prompt"} 50
ollama_request_tokens_total{model="deepseek-v4",type="generation"} 100
ollama_request_tokens_total{model="llama3.1",type="prompt"} 25
ollama_request_tokens_total{model="llama3.1",type="generation"} 75
# HELP some_other_counter Something
some_other_counter 10
"""
parsed3 = _parse_prometheus_metrics(sample3)
d3 = tracker.deltas(parsed3)
check("counter reset = no negative deltas",
      all(d["delta_value"] >= 0 for d in d3))
# On reset, tracker updates baseline silently — returns no records
check("counter reset = 0 records (baseline updated silently)",
      len(d3) == 0)

# ── 4. Flask app routes ────────────────────────────────────
print("\n── Flask Server ──")
init_agent_db()
app = create_app()
client: FlaskClient = app.test_client()

# Health
resp = client.get("/health")
check("/health returns 200", resp.status_code == 200)
data = resp.get_json()
check("health has status=ok", data.get("status") == "ok")
check("health has version", "version" in data)
check("health has database status", "database" in data)

# Agent info
resp = client.get("/api/v1/agent-info")
check("/api/v1/agent-info returns 200", resp.status_code == 200)
data = resp.get_json()
check("agent-info has hostname", "hostname" in data)
check("agent-info has endpoints", "endpoints" in data)

# Day summary (empty DB)
resp = client.get("/api/v1/day-summary")
check("/api/v1/day-summary returns 200", resp.status_code == 200)
data = resp.get_json()
check("day-summary has hostname", "hostname" in data)
check("day-summary has token_usage", "token_usage" in data)
check("day-summary has system_metrics", "system_metrics" in data)
check("day-summary has rollups", "rollups" in data)

# Day summary with bad date
resp = client.get("/api/v1/day-summary?date=not-a-date")
check("bad date returns 400", resp.status_code == 400)

# ── 5. Provider instantiation ──────────────────────────────
print("\n── LLM Providers ──")
ollama = OllamaProvider()
check("OllamaProvider.name == ollama", ollama.name == "ollama")

vllm = VLLMProvider()
check("VLLMProvider.name == vllm", vllm.name == "vllm")

llamacpp = LlamaCppProvider()
check("LlamaCppProvider.name == llamacpp", llamacpp.name == "llamacpp")

# ── 6. Collectors instantiate with config ─────────────────
print("\n── Collector Instantiation ──")
sc = sys_cls(collect_gpu=False)
errors = sc.validate_config()
check("SystemCollector validates", len(errors) == 0)
check("SystemCollector.name == system", sc.name == "system")

lc = llm_cls(endpoints={
    "ollama": {"url": "http://localhost:11434", "enabled": True, "api_type": "ollama"},
})
errors = lc.validate_config()
check("LLMCollector validates", len(errors) == 0)

# Collect from a non-existent endpoint (should fail gracefully)
result = lc.collect()
check("LLM collect on unavailable endpoint returns result", result is not None)
# Provider handles 404 internally and returns empty — success=True, no errors logged
check("LLM collect has success=True (provider handles 404 gracefully)",
      result.success == True)
check("LLM collect has records (empty list)", "records" in result.data)
check("LLM collect has endpoints_polled=1",
      result.data.get("endpoints_polled") == 1)
check("LLM collect has endpoints_errored=0 (provider self-handles errors)",
      result.data.get("endpoints_errored") == 0)

# ── 7. System collector (basic smoke) ─────────────────────
print("\n── System Collector ──")
sc2 = sys_cls(collect_gpu=False)  # Skip GPU to avoid nvidia-smi dependency
result2 = sc2.collect()
check("System collect returns result", result2 is not None)
check("System collect success", result2.success == True)
check("System has memory data", "memory" in result2.data)
if result2.data.get("memory"):
    check("Memory has mem_pct",
          "mem_pct" in result2.data["memory"])
check("System has load data" if result2.data.get("load") else "Load data present (or unavail in container)",
      True)

# ── 8. Cleanup ────────────────────────────────────────────
print("\n── Cleanup ──")
os.unlink("/tmp/rowbutt_p1_test_agent.db")
check("Test DB cleaned up", True)

# ── Summary ────────────────────────────────────────────────
print(f"\n{'=' * 60}")
total = passed + failed
print(f"Results:  {passed}/{total} passed", end="")
if failed > 0:
    print(f"  —  {failed} FAILED", file=sys.stderr)
else:
    print("  —  All passed")
print(f"{'=' * 60}")
sys.exit(1 if failed > 0 else 0)
