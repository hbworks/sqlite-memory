"""Shared read-only statistics for SQLite memory databases."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict


def _file_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def collect_memory_stats(conn: sqlite3.Connection, db_path: Path) -> Dict[str, Any]:
    """Collect database and memory counts without checkpointing the WAL.

    ``total_memories`` counts what the agent currently remembers (active rows) so it matches
    the sum of ``categories`` and the rows the browser lists; ``all_memories`` also counts
    retired rows kept for audit.
    """
    all_row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
    active_row = conn.execute("SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()
    category_rows = conn.execute(
        """SELECT category, COUNT(*)
           FROM memories
           WHERE status = 'active'
           GROUP BY category
           ORDER BY category"""
    ).fetchall()
    updated_row = conn.execute("SELECT MAX(updated_at) FROM memories").fetchone()
    active_total = int(active_row[0]) if active_row else 0

    return {
        "total_memories": active_total,
        "active_memories": active_total,
        "all_memories": int(all_row[0]) if all_row else 0,
        "categories": {row[0]: int(row[1]) for row in category_rows},
        "last_updated_at": updated_row[0],
        "db_size_bytes": _file_size_bytes(db_path),
        "wal_size_bytes": _file_size_bytes(Path(str(db_path) + "-wal")),
    }
