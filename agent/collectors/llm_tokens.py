"""LLM token collector — polls local inference endpoints for token usage.

Supports three engine types out of the box:

- **ollama**: scrapes Prometheus ``/metrics`` on the Ollama port,
  extracting ``ollama_request_tokens_total`` counters by model.
- **vllm**: scrapes Prometheus ``/metrics`` on the vLLM port,
  extracting ``vllm:prompt_tokens_total`` and ``vllm:generation_tokens_total``.
- **llamacpp**: queries the ``/slots`` JSON endpoint which exposes
  per-slot ``n_past`` and ``n_generated`` counters.

Each endpoint is polled at its configured interval. A diff-based
tracker computes delta tokens between polls so we record *usage
during the interval*, not cumulative totals.
"""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Any

import httpx

from agent.collectors.base import Collector, CollectResult, register

logger = logging.getLogger(__name__)

# ── Prometheus text-format parser ──────────────────────────

# Matches lines like:
#   metric_name{label="val",label2="val2"} 1234.0
#   metric_name  42
_PROM_LINE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\s*'
    r'(\{(?P<labels>[^}]+)\})?\s+'
    r'(?P<value>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$'
)
_LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>[^"]*)"')


def _parse_prometheus_metrics(text: str) -> Dict[str, List[Tuple[Dict[str, str], float]]]:
    """Parse Prometheus exposition format.

    Returns ``{metric_name: [ ({label_key: label_val, ...}, value), ... ]}``.
    Skips comment/empty lines (HELP, TYPE, blank).
    """
    metrics: Dict[str, List[Tuple[Dict[str, str], float]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PROM_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        raw_labels = m.group("labels")
        value = float(m.group("value"))
        labels: Dict[str, str] = {}
        if raw_labels:
            for lab in _LABEL_RE.finditer(raw_labels):
                labels[lab.group("key")] = lab.group("value")
        metrics.setdefault(name, []).append((labels, value))
    return metrics


# ── Diff tracker ────────────────────────────────────────────


class DiffTracker:
    """Tracks cumulative token counters and returns deltas between polls.

    On the *first* call, records the baseline and returns zero deltas
    so we don't over-count the initial snapshot.
    """

    def __init__(self):
        # key: (metric_name, frozenset of label items) → last_value
        self._baseline: Dict[Tuple[str, frozenset], float] = {}
        self._initialised = False

    def deltas(self, metrics: Dict[str, List[Tuple[Dict[str, str], float]]]
               ) -> List[Dict[str, Any]]:
        """Given parsed metrics, return delta records.

        Each record: ``{metric, labels, delta_value}``
        """
        results = []
        now = time.time()

        for metric_name, samples in metrics.items():
            for labels_dict, current_value in samples:
                # Ordered key for diff comparison
                label_items = frozenset(labels_dict.items())
                key = (metric_name, label_items)

                if not self._initialised:
                    # First pass — just record baseline
                    self._baseline[key] = current_value
                    continue

                last = self._baseline.get(key)
                if last is None:
                    # New metric appeared since baseline
                    self._baseline[key] = current_value
                    continue

                delta = current_value - last
                if delta < 0:
                    # Counter reset (process restart) — use current as new base
                    self._baseline[key] = current_value
                    continue

                if delta > 0:
                    self._baseline[key] = current_value
                    results.append({
                        "metric": metric_name,
                        "labels": dict(labels_dict),
                        "delta_value": delta,
                        "current_value": current_value,
                        "timestamp": now,
                    })

        self._initialised = True
        return results


# ── Provider base ───────────────────────────────────────────


class LLMProvider(ABC):
    """Abstract base for an LLM-endpoint-specific metrics poller."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider label (e.g. 'ollama', 'vllm')."""

    @abstractmethod
    def poll(self, client: httpx.Client, base_url: str) -> List[Dict[str, Any]]:
        """Poll the endpoint and return token-usage records.

        Each record dict must have at minimum:
          - ``model`` (str)
          - ``input_tokens`` (int)
          - ``output_tokens`` (int)
          - ``source`` (str) — the provider label
        """


# ── Ollama provider ─────────────────────────────────────────


class OllamaProvider(LLMProvider):
    """Scrapes Ollama's Prometheus endpoint."""

    OLLAMA_TOKEN_METRICS = {"ollama_request_tokens_total"}

    @property
    def name(self) -> str:
        return "ollama"

    def _extract_prometheus(self, metrics: Dict) -> List[Dict]:
        """Parse Ollama's token counter metrics into model-level records."""
        records = []

        # Group token counters by model
        model_tokens: Dict[str, Dict[str, int]] = {}

        for metric_name, samples in metrics.items():
            if metric_name not in self.OLLAMA_TOKEN_METRICS:
                continue
            for labels, value in samples:
                model = labels.get("model", "unknown")
                tok_type = labels.get("type", "")  # "prompt" or "generation"
                if model not in model_tokens:
                    model_tokens[model] = {"input_tokens": 0, "output_tokens": 0}
                if tok_type == "prompt":
                    model_tokens[model]["input_tokens"] += int(value)
                elif tok_type == "generation":
                    model_tokens[model]["output_tokens"] += int(value)
                else:
                    # Fallback: count all as "other"
                    model_tokens[model].setdefault("other_tokens", 0)
                    model_tokens[model]["other_tokens"] += int(value)

        for model, counts in model_tokens.items():
            records.append({
                "model": model,
                "input_tokens": counts.get("input_tokens", 0),
                "output_tokens": counts.get("output_tokens", 0),
                "source": self.name,
            })

        return records

    def poll(self, client: httpx.Client, base_url: str) -> List[Dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/api/metrics"
        try:
            resp = client.get(url, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Ollama metrics unreachable at %s: %s", url, exc)
            # Fallback: try /metrics (some Ollama versions)
            try:
                resp = client.get(f"{base_url.rstrip('/')}/metrics", timeout=10)
                resp.raise_for_status()
            except Exception:
                return []

        parsed = _parse_prometheus_metrics(resp.text)
        return self._extract_prometheus(parsed)


# ── vLLM provider ───────────────────────────────────────────


class VLLMProvider(LLMProvider):
    """Scrapes vLLM's Prometheus endpoint for token counters."""

    VLLM_PROMPT_METRIC = "vllm:prompt_tokens_total"
    VLLM_GEN_METRIC = "vllm:generation_tokens_total"

    @property
    def name(self) -> str:
        return "vllm"

    def poll(self, client: httpx.Client, base_url: str) -> List[Dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/metrics"
        try:
            resp = client.get(url, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("vLLM metrics unreachable at %s: %s", url, exc)
            return []

        parsed = _parse_prometheus_metrics(resp.text)
        records = []

        # vLLM labels typically include: model_name, etc.
        prompt_samples = parsed.get(self.VLLM_PROMPT_METRIC, [])
        gen_samples = parsed.get(self.VLLM_GEN_METRIC, [])

        # Build a dict of model → prompt/gen tokens
        model_tokens: Dict[str, Dict[str, int]] = {}

        def _accumulate(samples, key: str):
            for labels, value in samples:
                model = labels.get("model_name", labels.get("model", "unknown"))
                if model not in model_tokens:
                    model_tokens[model] = {"input": 0, "output": 0}
                model_tokens[model][key] += int(value)

        _accumulate(prompt_samples, "input")
        _accumulate(gen_samples, "output")

        for model, counts in model_tokens.items():
            records.append({
                "model": model,
                "input_tokens": counts["input"],
                "output_tokens": counts["output"],
                "source": self.name,
            })

        return records


# ── llama.cpp provider ──────────────────────────────────────


class LlamaCppProvider(LLMProvider):
    """Polls llama.cpp server's ``/slots`` endpoint."""

    @property
    def name(self) -> str:
        return "llamacpp"

    def poll(self, client: httpx.Client, base_url: str) -> List[Dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/slots"
        try:
            resp = client.get(url, timeout=10)
            resp.raise_for_status()
            slots = resp.json()
        except Exception as exc:
            logger.warning("llama.cpp slots unreachable at %s: %s", url, exc)
            return []

        if not isinstance(slots, list):
            return []

        records = []
        for slot in slots:
            model = slot.get("model", slot.get("name", "unknown"))
            # n_past = tokens processed so far, n_generated = tokens generated
            n_past = slot.get("n_past", 0) or 0
            n_generated = slot.get("n_generated", 0) or 0
            records.append({
                "model": model,
                "input_tokens": int(n_past),
                "output_tokens": int(n_generated),
                "source": self.name,
                # Also capture extra metadata
                "slot_id": slot.get("id", 0),
                "state": slot.get("state", "unknown"),
            })

        return records


# ── Provider registry ───────────────────────────────────────

_PROVIDER_MAP: Dict[str, type] = {
    "ollama": OllamaProvider,
    "vllm": VLLMProvider,
    "llamacpp": LlamaCppProvider,
}


def get_provider(api_type: str) -> LLMProvider:
    """Return a provider instance for the given type string."""
    cls = _PROVIDER_MAP.get(api_type)
    if cls is None:
        raise ValueError(f"Unknown endpoint api_type '{api_type}'. "
                         f"Available: {list(_PROVIDER_MAP.keys())}")
    return cls()


# ── Main Collector ──────────────────────────────────────────


@register
class LLMTokenCollector(Collector):
    """Collects token usage from configured LLM inference endpoints.

    Configuration passed via ``endpoints``: a dict mapping endpoint names
    to dicts with keys ``url``, ``api_type``, ``enabled``, ``poll_interval``.

    Uses per-endpoint ``DiffTracker`` instances to turn cumulative
    Prometheus counters into per-interval deltas.
    """

    name = "llm_tokens"

    def __init__(self, endpoints: Optional[Dict[str, Dict]] = None):
        self._trackers: Dict[str, DiffTracker] = {}
        self._providers: Dict[str, LLMProvider] = {}
        self._http_client: Optional[httpx.Client] = None

        # Resolve endpoints
        from config.defaults import LLM_ENDPOINT_DEFAULTS
        self.endpoints: Dict[str, Dict] = {}
        ep_src = endpoints or LLM_ENDPOINT_DEFAULTS
        for name, cfg in ep_src.items():
            if isinstance(cfg, dict) and cfg.get("enabled", True):
                self.endpoints[name] = cfg
                self._trackers[name] = DiffTracker()
                try:
                    self._providers[name] = get_provider(cfg.get("api_type", name))
                except ValueError:
                    logger.warning("Unknown api_type '%s' for endpoint '%s' — skipping",
                                   cfg.get("api_type", name), name)

    def _get_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=10)
        return self._http_client

    def validate_config(self) -> List[str]:
        errors = []
        if not self.endpoints:
            errors.append("No LLM endpoints configured")
        for name, cfg in self.endpoints.items():
            if not cfg.get("url"):
                errors.append(f"Endpoint '{name}' has no URL")
        return errors

    def collect(self) -> CollectResult:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        records: List[Dict[str, Any]] = []
        errors: List[str] = []
        client = self._get_client()

        for ep_name, cfg in self.endpoints.items():
            provider = self._providers.get(ep_name)
            if provider is None:
                continue
            url = cfg["url"]
            tracker = self._trackers[ep_name]

            try:
                raw_records = provider.poll(client, url)
                for rec in raw_records:
                    # Compute model-level totals
                    tokens = rec.copy()
                    tokens["endpoint"] = ep_name
                    tokens["polled_at"] = timestamp
                    tokens["total_tokens"] = tokens.get("input_tokens", 0) + \
                                              tokens.get("output_tokens", 0)
                    records.append(tokens)
            except Exception as exc:
                err = f"{ep_name} ({url}): {exc}"
                errors.append(err)
                logger.warning("LLM poll error: %s", err)

        data = {
            "records": records,
            "endpoints_polled": len(self.endpoints),
            "endpoints_errored": len(errors),
        }
        if errors:
            data["errors"] = errors

        ok = len(errors) < len(self.endpoints)
        return CollectResult(
            timestamp=timestamp,
            collector_name=self.name,
            success=ok,
            data=data,
            error="; ".join(errors) if errors else None,
        )
