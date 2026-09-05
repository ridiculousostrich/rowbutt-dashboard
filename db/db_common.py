"""Shared database connection management for Rowbutt Dashboard.

Provides helpers for initialising and connecting to both the agent-local
and aggregator-central SQLite databases. All paths are configurable via
the defaults module or environment variables.
"""

import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from config.defaults import (
    ROWBUTT_DIR,
    AGENT_DB_PATH,
    AGGREGATOR_DB_PATH,
)


# ── Helpers ─────────────────────────────────────────────────


def ensure_dir(path: str) -> None:
    """Create parent directory for *path* if it doesn't exist."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_agent_db_path() -> str:
    """Return the path to the agent-local SQLite database."""
    return os.environ.get("ROWBUTT_AGENT_DB", AGENT_DB_PATH)


def get_aggregator_db_path() -> str:
    """Return the path to the aggregator-central SQLite database."""
    return os.environ.get("ROWBUTT_AGGREGATOR_DB", AGGREGATOR_DB_PATH)


def get_reports_dir() -> str:
    """Return the path where generated reports are written."""
    reports = os.environ.get("ROWBUTT_REPORTS_DIR", str(
        Path(ROWBUTT_DIR) / "reports"
    ))
    return reports


# ── Connection helpers ──────────────────────────────────────


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with recommended settings.

    Returns a connection with WAL mode, foreign keys enabled, and
    a row factory that returns dict-like rows.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def connect_agent_db():
    """Context manager yielding a connection to the agent DB.

    Commits on success, rolls back on exception.
    """
    db_path = get_agent_db_path()
    ensure_dir(db_path)
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect_aggregator_db():
    """Context manager yielding a connection to the aggregator DB.

    Commits on success, rolls back on exception.
    """
    db_path = get_aggregator_db_path()
    ensure_dir(db_path)
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema initialisation ──────────────────────────────────


def _load_sql(name: str) -> str:
    """Load an SQL file from the db/ package directory."""
    here = Path(__file__).parent
    sql_file = here / name
    if not sql_file.exists():
        raise FileNotFoundError(
            f"Schema file not found: {sql_file}. "
            f"Make sure db/{name} exists."
        )
    return sql_file.read_text()


def init_agent_db() -> str:
    """Create agent-local tables if they don't exist.

    Returns the path to the database.
    """
    db_path = get_agent_db_path()
    ensure_dir(db_path)
    schema = _load_sql("schema_agent.sql")
    with connect_agent_db() as conn:
        conn.executescript(schema)
    return db_path


def init_aggregator_db() -> str:
    """Create aggregator-central tables if they don't exist.

    Seeds default pricing data on first init.
    Returns the path to the database.
    """
    db_path = get_aggregator_db_path()
    ensure_dir(db_path)
    schema = _load_sql("schema_aggregator.sql")
    with connect_aggregator_db() as conn:
        conn.executescript(schema)
    return db_path
