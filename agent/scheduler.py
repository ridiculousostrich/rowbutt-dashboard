"""Agent poll scheduler — drives collectors on configurable intervals.

Architecture:
- Each collector is registered in a ``PollJob`` with its own interval.
- The scheduler runs a main loop that checks which jobs are due,
  calls their ``collect()`` method, and writes results to the local
  SQLite database.
- Everything runs in a single thread with a short sleep tick.
"""

import logging
import time
import threading
from datetime import datetime, date
from typing import Dict, List, Optional, Type

from agent.collectors.base import Collector, CollectResult
from db.db_common import connect_agent_db

logger = logging.getLogger(__name__)


class PollJob:
    """A single collector instance managed by the scheduler."""

    def __init__(self, collector: Collector, interval: int,
                 label: Optional[str] = None):
        self.collector = collector
        self.interval = interval          # seconds
        self.label = label or collector.name
        self._last_poll: float = 0.0

    @property
    def due(self) -> bool:
        """True if the collector should be polled now."""
        return (time.time() - self._last_poll) >= self.interval

    def run(self) -> Optional[CollectResult]:
        """Execute one collect cycle and record the timestamp."""
        result = self.collector.collect()
        self._last_poll = time.time()
        return result


# ── DB writer helpers ───────────────────────────────────────


def _write_token_events(conn, result: CollectResult):
    """Insert token event records from an LLM poll into the DB."""

    bucket_dt = datetime.utcnow()
    bucket_hour = (bucket_dt.hour // 4) * 4       # 4-hour bucket
    bucket_date = bucket_dt.strftime("%Y-%m-%d")
    iso_ts = bucket_dt.strftime("%Y-%m-%dT%H:%M:%S")

    for rec in result.data.get("records", []):
        try:
            conn.execute(
                """INSERT INTO token_events
                   (recorded_at, model, input_tokens, output_tokens, total_tokens,
                    session_id, source, bucket_hour, bucket_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    iso_ts,
                    rec.get("model", "unknown"),
                    rec.get("input_tokens", 0),
                    rec.get("output_tokens", 0),
                    rec.get("total_tokens", 0),
                    rec.get("slot_id", f"{rec.get('source', '?')}-{iso_ts}"),
                    rec.get("source", "unknown"),
                    bucket_hour,
                    bucket_date,
                ),
            )
        except Exception as exc:
            logger.error("Failed to insert token event: %s", exc)


def _write_system_samples(conn, result: CollectResult):
    """Insert a system metrics sample into the DB."""

    data = result.data
    bucket_dt = datetime.utcnow()
    bucket_hour = (bucket_dt.hour // 4) * 4
    bucket_date = bucket_dt.strftime("%Y-%m-%d")

    # Extract memory
    mem = data.get("memory", {})
    mem_pct = mem.get("mem_pct", 0.0)

    # Extract temperatures
    temps = data.get("temperatures", {})
    gpu_temps = [g.get("temp_gpu", 0.0) for g in data.get("gpus", [])]
    gpu_temp_avg = sum(gpu_temps) / len(gpu_temps) if gpu_temps else None

    # GPU power
    gpu_power = data.get("gpu_summary", {}).get("total_power_w")

    # Load
    load = data.get("load", {})

    try:
        conn.execute(
            """INSERT INTO system_samples
               (sampled_at, mem_total_gb, mem_used_gb, mem_pct,
                temp_cpu_avg, temp_cpu_max, temp_gpu,
                gpu_power_w, gpu_util_pct,
                load_1m, load_5m, load_15m,
                bucket_hour, bucket_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                mem.get("mem_total_gb"),
                mem.get("mem_used_gb"),
                mem_pct,
                # CPU temps: average and max of all thermal readings
                round(sum(temps.values()) / len(temps), 1) if temps else None,
                round(max(temps.values()), 1) if temps else None,
                gpu_temp_avg,
                gpu_power,
                data.get("gpu_summary", {}).get("avg_util_pct"),
                load.get("load_1m"),
                load.get("load_5m"),
                load.get("load_15m"),
                bucket_hour,
                bucket_date,
            ),
        )
    except Exception as exc:
        logger.error("Failed to insert system sample: %s", exc)


def _update_daily_rollups(conn, result: CollectResult):
    """Update or insert the daily rollup row for the current bucket."""
    bucket_dt = datetime.utcnow()
    bucket_hour = (bucket_dt.hour // 4) * 4
    bucket_date = bucket_dt.strftime("%Y-%m-%d")

    if result.collector_name == "llm_tokens":
        total_in = sum(
            r.get("input_tokens", 0) for r in result.data.get("records", [])
        )
        total_out = sum(
            r.get("output_tokens", 0) for r in result.data.get("records", [])
        )
        total_tok = total_in + total_out

        try:
            conn.execute(
                """INSERT INTO daily_rollups
                   (date, bucket_hour, total_input, total_output, total_tokens,
                    sample_count)
                   VALUES (?, ?, ?, ?, ?, 1)
                   ON CONFLICT(date, bucket_hour) DO UPDATE SET
                       total_input = total_input + excluded.total_input,
                       total_output = total_output + excluded.total_output,
                       total_tokens = total_tokens + excluded.total_tokens,
                       sample_count = sample_count + 1""",
                (bucket_date, bucket_hour, total_in, total_out, total_tok),
            )
        except Exception as exc:
            logger.error("Failed to update daily rollup: %s", exc)


# ── Scheduler ───────────────────────────────────────────────


class Scheduler:
    """Runs a loop that executes collectors on their configured intervals.

    Usage::

        sched = Scheduler()
        sched.add_job(SystemCollector(), interval=60)
        sched.start()               # runs in background thread
        ...
        sched.stop()

    Callbacks are one-shot functions called after a job completes.
    """

    def __init__(self):
        self._jobs: List[PollJob] = []
        self._callbacks: List = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def add_job(self, collector: Collector, interval: int,
                label: Optional[str] = None) -> None:
        """Register a collector to be polled every *interval* seconds."""
        self._jobs.append(PollJob(collector, interval, label))

    def on_collect(self, callback) -> None:
        """Register a callback called as ``callback(job, result)`` after each poll."""
        self._callbacks.append(callback)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the scheduler in a daemon background thread."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="rowbutt-scheduler")
        self._thread.start()
        logger.info("Scheduler started with %d job(s)", len(self._jobs))

    def stop(self) -> None:
        """Signal the scheduler to stop and wait for the thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=15)
            logger.info("Scheduler stopped")

    def _loop(self) -> None:
        """Main scheduler loop."""
        logger.debug("Scheduler loop starting")
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                logger.error("Scheduler tick error: %s", exc)
            time.sleep(1)  # 1-second tick resolution

    def _tick(self) -> None:
        """Check each job and run if due."""
        now = time.time()
        for job in self._jobs:
            if not job.due:
                continue
            try:
                result = job.run()
                if result is None:
                    continue

                # Write to DB
                with connect_agent_db() as conn:
                    if result.collector_name == "system":
                        _write_system_samples(conn, result)
                    elif result.collector_name == "llm_tokens":
                        _write_token_events(conn, result)
                    _update_daily_rollups(conn, result)

                # Fire callbacks
                for cb in self._callbacks:
                    try:
                        cb(job, result)
                    except Exception as exc:
                        logger.error("Callback error: %s", exc)

            except Exception as exc:
                logger.error("Job '%s' failed: %s", job.label, exc)
