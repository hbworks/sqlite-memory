"""Shared schema initialization for the SQLite memory provider and dashboard."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def ensure_memory_schema(conn: sqlite3.Connection) -> bool:
    """Create memory tables and FTS synchronization triggers; rebuild FTS if needed.

    Returns whether the FTS integrity check completed, so callers with a schema cache
    can avoid caching an initialization whose index could not be checked.
    """
    fts_check_completed = False
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT DEFAULT 'general',
                content TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content, category, content='memories', content_rowid='id'
            );
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, category)
                VALUES (new.id, new.content, new.category);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, category)
                VALUES ('delete', old.id, old.content, old.category);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, category)
                VALUES ('delete', old.id, old.content, old.category);
                INSERT INTO memories_fts(rowid, content, category)
                VALUES (new.id, new.content, new.category);
            END;
        """)

        try:
            mem_row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            mem_count = mem_row[0] if mem_row else 0
            doc_row = conn.execute("SELECT COUNT(*) FROM memories_fts_docsize").fetchone()
            doc_count = doc_row[0] if doc_row else 0
            if mem_count != doc_count:
                conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild');")
            fts_check_completed = True
        except Exception as exc:
            logger.debug("Notice during FTS integrity check: %s", exc)

    return fts_check_completed
