"""Schema migration / version checks for Rowbutt Dashboard.

Simple version-tracked migrations. Each migration is a function
keyed by a version string. The runner checks the current schema
version in the DB's meta table and applies any pending migrations
in order.
"""

import logging
from typing import Callable, Dict

from db.db_common import (
    connect_agent_db,
    connect_aggregator_db,
)

logger = logging.getLogger(__name__)

# ── Registry ────────────────────────────────────────────────

# Maps schema_version → migration callable.
# Each migration receives the connection and must return nothing.
_AGENT_MIGRATIONS: Dict[str, Callable] = {}
_AGGREGATOR_MIGRATIONS: Dict[str, Callable] = {}


def register_agent_migration(version: str):
    """Decorator: register a function as an agent DB migration."""
    def wrapper(fn):
        _AGENT_MIGRATIONS[version] = fn
        return fn
    return wrapper


def register_aggregator_migration(version: str):
    """Decorator: register a function as an aggregator DB migration."""
    def wrapper(fn):
        _AGGREGATOR_MIGRATIONS[version] = fn
        return fn
    return wrapper


# ── Runner ──────────────────────────────────────────────────


def _current_version(conn, meta_table: str) -> str:
    """Read the current schema version from the meta table."""
    row = conn.execute(
        f"SELECT value FROM {meta_table} WHERE key='schema_version'"
    ).fetchone()
    return row["value"] if row else "0"


def _set_version(conn, meta_table: str, version: str):
    """Write the schema version to the meta table."""
    conn.execute(
        f"INSERT OR REPLACE INTO {meta_table} (key, value, updated_at) "
        "VALUES ('schema_version', ?, datetime('now'))",
        (version,),
    )


def migrate_agent():
    """Apply any pending agent DB migrations."""
    with connect_agent_db() as conn:
        current = _current_version(conn, "agent_meta")
        versions = sorted(_AGENT_MIGRATIONS.keys())
        for v in versions:
            if v > current:
                logger.info("Running agent migration → %s", v)
                _AGENT_MIGRATIONS[v](conn)
                _set_version(conn, "agent_meta", v)


def migrate_aggregator():
    """Apply any pending aggregator DB migrations."""
    with connect_aggregator_db() as conn:
        current = _current_version(conn, "aggregator_meta")
        versions = sorted(_AGGREGATOR_MIGRATIONS.keys())
        for v in versions:
            if v > current:
                logger.info("Running aggregator migration → %s", v)
                _AGGREGATOR_MIGRATIONS[v](conn)
                _set_version(conn, "aggregator_meta", v)


# ── Placeholder for future migrations ──────────────────────

# Example — uncomment and extend when schema changes:
#
# @register_agent_migration("2")
# def agent_migration_2(conn):
#     conn.execute("ALTER TABLE token_events ADD COLUMN duration_ms INTEGER;")
