"""Agent entry points — used by CLI and as a library."""

import logging
import signal
import sys
import threading
from typing import Optional

from config.defaults import (
    SYSTEM_POLL_INTERVAL, LLM_POLL_INTERVAL,
    AGENT_HOST, AGENT_PORT, LLM_ENDPOINT_DEFAULTS,
    COLLECT_SYSTEM, COLLECT_GPU, GPU_INDICES,
)
from db.db_common import init_agent_db
from agent.collectors.system import SystemCollector
from agent.collectors.llm_tokens import LLMTokenCollector
from agent.scheduler import Scheduler
from agent.server import run_server

logger = logging.getLogger(__name__)


def run_agent(host: Optional[str] = None, port: Optional[int] = None,
              debug: bool = False) -> None:
    """Start the agent: init DB, start collectors, launch HTTP server.

    Blocks forever (or until SIGINT/SIGTERM).
    """
    # ── Setup ──
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Rowbutt Agent starting (host=%s)", host or AGENT_HOST)

    # Initialise DB if fresh
    db_path = init_agent_db()
    logger.info("Agent database: %s", db_path)

    # ── Build collectors ──
    collectors = []

    if COLLECT_SYSTEM:
        sys_collector = SystemCollector(
            collect_gpu=COLLECT_GPU,
            gpu_indices=GPU_INDICES if GPU_INDICES else None,
        )
        collectors.append(("system", sys_collector, SYSTEM_POLL_INTERVAL))
        logger.info("Registered system collector (interval=%ds)", SYSTEM_POLL_INTERVAL)

    llm_endpoints = {
        name: cfg for name, cfg in LLM_ENDPOINT_DEFAULTS.items()
        if cfg.get("enabled", False)
    }
    if llm_endpoints:
        llm_collector = LLMTokenCollector(endpoints=llm_endpoints)
        collectors.append(("llm_tokens", llm_collector, LLM_POLL_INTERVAL))
        logger.info("Registered LLM token collector with %d endpoint(s): %s",
                    len(llm_endpoints),
                    ", ".join(f"{n} ({c['api_type']})" for n, c in llm_endpoints.items()))

    if not collectors:
        logger.warning("No collectors configured! Agent will only serve HTTP.")

    # ── Start scheduler ──
    sched = Scheduler()
    for label, col, interval in collectors:
        sched.add_job(col, interval=interval, label=label)

    if collectors:
        sched.start()

    # ── Start HTTP server (blocking) ──
    logger.info("Starting HTTP server on %s:%s", host or AGENT_HOST, port or AGENT_PORT)

    # Handle shutdown gracefully
    shutdown_event = threading.Event()

    def _signal_handler(sig, frame):
        logger.info("Received signal %s, shutting down...", sig)
        shutdown_event.set()
        sched.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        run_server(host=host, port=port, debug=debug)
    except KeyboardInterrupt:
        pass
    finally:
        sched.stop()
        logger.info("Agent shut down.")
