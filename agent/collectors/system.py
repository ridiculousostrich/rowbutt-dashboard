"""System metrics collector — memory, CPU temps, GPU stats, load.

Uses psutil for most metrics and falls back to file-based /proc reading
for environments where psutil isn't available or reports incomplete data.
GPU data comes from parsing ``nvidia-smi --query-gpu ... --format=csv``.
"""

import json
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional, Any

import psutil

from agent.collectors.base import Collector, CollectResult, register

logger = logging.getLogger(__name__)


def _run_cmd(cmd: List[str], timeout: int = 10) -> Optional[str]:
    """Run a command and return stdout, or None on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Command failed: %s — %s", " ".join(cmd), exc)
        return None


def _read_sysfs(path: str) -> Optional[str]:
    """Read a sysfs file and return stripped content, or None."""
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


# ── GPU helpers (nvidia-smi) ────────────────────────────────


def _nvidia_smi_query(indices: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Query all NVIDIA GPUs via ``nvidia-smi``.

    Returns a list of dicts, one per GPU, with keys:
      index, name, power_draw_w, gpu_util_pct, mem_total_mib,
      mem_used_mib, mem_free_mib, temp_gpu
    Returns empty list if nvidia-smi is unavailable.
    """
    gpus = "all" if not indices else ",".join(str(i) for i in indices)
    cmd = [
        "nvidia-smi",
        f"--query-gpu=index,name,power.draw,utilization.gpu,"
        f"memory.total,memory.used,memory.free,temperature.gpu",
        "--format=csv,noheader,nounits",
        f"--id={gpus}",
    ]
    raw = _run_cmd(cmd, timeout=15)
    if not raw:
        return []

    results = []
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        try:
            results.append({
                "index": int(parts[0]),
                "name": parts[1],
                "power_draw_w": float(parts[2]) if parts[2] not in ("N/A", "[N/A]", "") else 0.0,
                "gpu_util_pct": float(parts[3]) if parts[3] not in ("N/A", "[N/A]", "") else 0.0,
                "mem_total_mib": float(parts[4]),
                "mem_used_mib": float(parts[5]),
                "mem_free_mib": float(parts[6]),
                "temp_gpu": float(parts[7]) if parts[7] not in ("N/A", "[N/A]", "") else 0.0,
            })
        except (ValueError, IndexError) as exc:
            logger.warning("Failed to parse nvidia-smi line: %s — %s", line, exc)
    return results


def _cpu_temps() -> Dict[str, float]:
    """Return CPU temperature measurements in Celsius.

    Merges data from psutil and direct sysfs reads for best coverage.
    Returns dict with keys like ``cpu_avg``, ``cpu_max``, ``cpu_package``.
    """
    temps = {}

    # Try psutil (most reliable)
    if hasattr(psutil, "sensors_temperatures"):
        try:
            st = psutil.sensors_temperatures()
            for chip, entries in st.items():
                for entry in entries:
                    label = f"{chip}_{entry.label}" if entry.label else chip
                    temps[label] = entry.current
            if temps:
                return temps
        except Exception:
            pass

    # Fallback: sysfs (common on Linux without sensors)
    base = "/sys/class/thermal"
    if os.path.isdir(base):
        for entry in sorted(os.listdir(base)):
            if entry.startswith("thermal_zone"):
                temp_path = os.path.join(base, entry, "temp")
                typ_path = os.path.join(base, entry, "type")
                typ = _read_sysfs(typ_path) or entry
                raw = _read_sysfs(temp_path)
                if raw:
                    try:
                        temps[typ] = int(raw) / 1000.0
                    except ValueError:
                        pass

    return temps


# ── Memory ──────────────────────────────────────────────────


def _memory_info() -> Dict[str, Any]:
    """Return memory info in GB / percentages."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "mem_total_gb": round(mem.total / (1024 ** 3), 2),
        "mem_used_gb": round(mem.used / (1024 ** 3), 2),
        "mem_avail_gb": round(mem.available / (1024 ** 3), 2),
        "mem_pct": round(mem.percent, 1),
        "swap_total_gb": round(swap.total / (1024 ** 3), 2),
        "swap_used_gb": round(swap.used / (1024 ** 3), 2),
        "swap_pct": round(swap.percent, 1),
    }


# ── Collector ───────────────────────────────────────────────


@register
class SystemCollector(Collector):
    """Collects local system metrics — memory, CPU temps, GPU, load.

    Configuration (via :mod:`config.defaults` or environment vars):

    - ``ROWBUTT_COLLECT_SYSTEM``: enable/disable (default True)
    - ``ROWBUTT_COLLECT_GPU``: enable/disable GPU polling (default True)
    - ``GPU_INDICES``: list of GPU indices to query (default: all)
    """

    name = "system"

    def __init__(self, collect_gpu: bool = True, gpu_indices: Optional[List[int]] = None):
        self.collect_gpu = collect_gpu
        self.gpu_indices = gpu_indices or []

    def validate_config(self) -> List[str]:
        errors = []
        if not hasattr(psutil, "virtual_memory"):
            errors.append("psutil.virtual_memory is unavailable")
        return errors

    def collect(self) -> CollectResult:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        data: Dict[str, Any] = {}

        try:
            # Memory
            data["memory"] = _memory_info()

            # Load
            try:
                load = psutil.getloadavg()
                data["load"] = {
                    "load_1m": round(load[0], 2),
                    "load_5m": round(load[1], 2),
                    "load_15m": round(load[2], 2),
                }
            except Exception:
                # Fallback: read /proc/loadavg
                raw = _read_sysfs("/proc/loadavg")
                if raw:
                    parts = raw.split()[:3]
                    data["load"] = {
                        f"load_{i+1}m": float(parts[i])
                        for i in range(min(3, len(parts)))
                    }

            # CPU temps
            data["temperatures"] = _cpu_temps()

            # GPU
            if self.collect_gpu:
                gpus = _nvidia_smi_query(self.gpu_indices)
                data["gpus"] = gpus
                if gpus:
                    data["gpu_summary"] = {
                        "total_power_w": round(sum(g["power_draw_w"] for g in gpus), 1),
                        "avg_temp": round(
                            sum(g["temp_gpu"] for g in gpus) / len(gpus), 1
                        ) if gpus else 0.0,
                        "avg_util_pct": round(
                            sum(g["gpu_util_pct"] for g in gpus) / len(gpus), 1
                        ) if gpus else 0.0,
                    }

            # CPUs / cores
            data["cpu_count"] = {
                "physical": psutil.cpu_count(logical=False),
                "logical": psutil.cpu_count(logical=True),
            }

            # Uptime
            if hasattr(psutil, "boot_time"):
                data["uptime_seconds"] = int(time.time() - psutil.boot_time())

        except Exception as exc:
            logger.error("System collection error: %s", exc)
            return CollectResult(
                timestamp=timestamp,
                collector_name=self.name,
                success=False,
                data=data,
                error=str(exc),
            )

        return CollectResult(
            timestamp=timestamp,
            collector_name=self.name,
            success=True,
            data=data,
        )
