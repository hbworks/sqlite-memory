"""SQLite Memory Plugin for Hermes Agent.

Provides zero-dependency, local-first persistent long-term memory using
Python's built-in sqlite3 and FTS5 full-text search.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider, RecallStatus

logger = logging.getLogger(__name__)

MIN_QUERY_LEN = 2
MAX_RECALL_RESULTS = 5
INDICATOR_GLYPH = "💾"

# Tool Schemas
REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_remember",
        "description": "Store a piece of information or fact into persistent long-term SQLite memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The exact fact, preference, rule, or piece of context to remember across sessions.",
                },
                "category": {
                    "type": "string",
                    "description": "Category for organizing memories (e.g. 'preference', 'project', 'user', 'rule', 'general').",
                    "default": "general",
                },
            },
            "required": ["content"],
        },
    },
}

SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_search",
        "description": "Search the persistent long-term SQLite memory using keywords or full-text query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords or phrase to look up in memory.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

FORGET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_forget",
        "description": "Delete a memory from persistent SQLite memory by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "The ID of the memory to remove.",
                },
            },
            "required": ["memory_id"],
        },
    },
}

LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_list_memories",
        "description": "List recently stored memories, optionally filtered by category.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter (e.g. 'preference', 'project').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to list (default 10).",
                    "default": 10,
                },
            },
        },
    },
}


def _get_default_db_path() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home() / "memory.db"
    except Exception:
        return Path(os.path.expanduser("~/.hermes/memory.db"))


class SQLiteMemoryProvider(MemoryProvider):
    """Local SQLite-backed persistent memory provider with FTS5 search."""

    def __init__(self) -> None:
        super().__init__()
        self._db_path = _get_default_db_path()
        self._session_id = ""
        self._last_recall_count = 0
        self._lock = threading.Lock()
        self._initialized = False
        self._init_db()

    @property
    def name(self) -> str:
        return "sqlite_memory"

    def is_available(self) -> bool:
        """sqlite3 is standard library, always available."""
        return True

    def get_config_schema(self):
        return [
            {
                "key": "db_path",
                "description": "Path to the SQLite database file",
                "default": str(_get_default_db_path()),
            },
            {
                "key": "auto_extract",
                "description": "Automatically extract facts and preferences from conversation turns",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "max_recall",
                "description": "Max number of relevant memories to prefetch per turn",
                "default": "5",
            },
        ]

    def _get_connection(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        """Initialize SQLite database tables, FTS5 virtual table, and triggers."""
        with self._lock:
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = self._get_connection()
                with conn:
                    # Main memories table
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

                    # FTS5 full-text search table
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                            content,
                            category,
                            content='memories',
                            content_rowid='id'
                        );
                    """)

                    # Triggers to keep FTS5 synchronized
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
                conn.close()
                self._initialized = True
            except Exception as e:
                logger.error("Failed to initialize SQLite memory tables: %s", e)

    def initialize(self, session_id: str = "", **kwargs) -> None:
        """Called on agent startup / session initialization."""
        self._session_id = session_id
        self._last_recall_count = 0

        # Load config if available
        try:
            from hermes_cli.config import load_config, cfg_get
            config = load_config()
            custom_path = cfg_get(config, "memory", "sqlite_memory", "db_path", default="")
            if custom_path:
                self._db_path = Path(os.path.expanduser(str(custom_path)))
            else:
                self._db_path = _get_default_db_path()
        except Exception:
            self._db_path = _get_default_db_path()

        self._init_db()
        logger.info("SQLiteMemoryProvider initialized with DB at %s", self._db_path)

    def system_prompt_block(self) -> str:
        return (
            "# Persistent SQLite Memory\n"
            "Status: Active. You have access to persistent long-term memory stored in local SQLite.\n"
            "- Important facts, user preferences, and project context persist across sessions.\n"
            "- Use `sqlite_remember` to proactively save new facts/rules you learn.\n"
            "- Use `sqlite_search` to find relevant past details when needed.\n"
            "- Use `sqlite_forget` if a memory is outdated or contradicted."
        )

    def recall_status(self) -> Optional[RecallStatus]:
        return RecallStatus(
            provider_label="SQLite Memory",
            count=self._last_recall_count,
            glyph=INDICATOR_GLYPH,
        )

    def _sanitize_fts_query(self, query: str) -> str:
        """Sanitize query string for SQLite FTS5."""
        clean = re.sub(r'[^\w\s\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf]', ' ', query)
        terms = [t.strip() for t in clean.split() if len(t.strip()) >= 2]
        if not terms:
            return ""
        return " OR ".join(f'"{t}"' for t in terms[:10])

    def search_memories(self, query: str, limit: int = 5, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search memories using FTS5 with fallback to LIKE search."""
        self._init_db()
        results: List[Dict[str, Any]] = []
        fts_query = self._sanitize_fts_query(query)

        conn = self._get_connection()
        try:
            # 1. Try FTS5 search first
            if fts_query:
                sql = """
                    SELECT m.id, m.category, m.content, m.created_at,
                           bm25(memories_fts) as rank
                    FROM memories_fts f
                    JOIN memories m ON m.id = f.rowid
                    WHERE memories_fts MATCH ?
                """
                params: List[Any] = [fts_query]
                if category:
                    sql += " AND m.category = ?"
                    params.append(category)
                sql += " ORDER BY rank LIMIT ?"
                params.append(limit)

                cursor = conn.execute(sql, params)
                for row in cursor.fetchall():
                    results.append({
                        "id": row["id"],
                        "category": row["category"],
                        "content": row["content"],
                        "created_at": row["created_at"],
                    })

            # 2. Fallback to LIKE if FTS yielded no results or query was plain
            if not results and query.strip():
                keywords = [k.strip() for k in query.split() if len(k.strip()) >= 2]
                if keywords:
                    where_clauses = ["m.content LIKE ?" for _ in keywords[:3]]
                    sql = f"""
                        SELECT m.id, m.category, m.content, m.created_at
                        FROM memories m
                        WHERE ({" OR ".join(where_clauses)})
                    """
                    params = [f"%{k}%" for k in keywords[:3]]
                    if category:
                        sql += " AND m.category = ?"
                        params.append(category)
                    sql += " ORDER BY m.updated_at DESC LIMIT ?"
                    params.append(limit)

                    cursor = conn.execute(sql, params)
                    for row in cursor.fetchall():
                        results.append({
                            "id": row["id"],
                            "category": row["category"],
                            "content": row["content"],
                            "created_at": row["created_at"],
                        })
        except Exception as e:
            logger.debug("SQLite memory search error: %s", e)
        finally:
            conn.close()

        return results

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Search and inject relevant memories prior to LLM turn."""
        if not query or len(query.strip()) < MIN_QUERY_LEN:
            self._last_recall_count = 0
            return ""

        memories = self.search_memories(query, limit=MAX_RECALL_RESULTS)
        self._last_recall_count = len(memories)

        if not memories:
            return ""

        lines = [f"- [ID: {m['id']}] ({m['category']}) {m['content']}" for m in memories]
        formatted = "\n".join(lines)
        return f"## Relevant Long-Term Memories (from SQLite)\n{formatted}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def add_memory(self, content: str, category: str = "general", source: str = "manual") -> int:
        """Insert a new memory row."""
        self._init_db()
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO memories (category, content, source, session_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (category, content.strip(), source, self._session_id),
                )
                return cursor.lastrowid or 0
        finally:
            conn.close()

    def delete_memory(self, memory_id: int) -> bool:
        """Delete a memory row by ID."""
        self._init_db()
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()

    def list_memories(self, category: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """List recently stored memories."""
        self._init_db()
        conn = self._get_connection()
        try:
            sql = "SELECT id, category, content, created_at FROM memories"
            params: List[Any] = []
            if category:
                sql += " WHERE category = ?"
                params.append(category)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [
                {
                    "id": row["id"],
                    "category": row["category"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                }
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Extract explicit preferences/rules if detected in turn."""
        if not user_content or len(user_content.strip()) < 5:
            return

        explicit_patterns = [
            r"(?:remember\s+(?:that|to)?\s*:?\s*)(.+)",
            r"(?:please\s+remember\s*:?\s*)(.+)",
            r"(?:(?:これ|以下|次|私の好みを?)\s*覚えて(?:おいて|ください|ね)?\s*[:：]?\s*)(.+)",
            r"(?:(?:今後|次回から|これからは)\s*(.+)(?:にして|を使って|でやって))",
        ]

        text = user_content.strip()
        extracted = []
        for pat in explicit_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m and m.group(1):
                extracted.append(m.group(1).strip())

        if extracted:
            for item in extracted:
                self.add_memory(content=item, category="preference", source="auto_extract")
                logger.info("SQLiteMemoryProvider auto-extracted memory: %s", item)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror Hermes memory writes to SQLite."""
        if action in {"add", "replace"} and content:
            category = "user" if target == "user" else "general"
            self.add_memory(content=content, category=category, source="system_sync")

    def backup_paths(self) -> List[str]:
        """Paths to include in hermes backup."""
        return [str(self._db_path)]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [REMEMBER_SCHEMA, SEARCH_SCHEMA, FORGET_SCHEMA, LIST_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        try:
            if tool_name == "sqlite_remember":
                content = args.get("content", "").strip()
                category = args.get("category", "general")
                if not content:
                    return json.dumps({"error": "content is required"})
                mem_id = self.add_memory(content=content, category=category, source="tool")
                return json.dumps({"status": "success", "memory_id": mem_id, "message": "Fact stored in SQLite memory."})

            elif tool_name == "sqlite_search":
                query = args.get("query", "").strip()
                limit = int(args.get("limit", 5))
                if not query:
                    return json.dumps({"error": "query is required"})
                results = self.search_memories(query=query, limit=limit)
                return json.dumps({"status": "success", "count": len(results), "results": results})

            elif tool_name == "sqlite_forget":
                mem_id = int(args.get("memory_id", 0))
                if not mem_id:
                    return json.dumps({"error": "memory_id is required"})
                deleted = self.delete_memory(mem_id)
                if deleted:
                    return json.dumps({"status": "success", "message": f"Memory ID {mem_id} deleted."})
                else:
                    return json.dumps({"status": "not_found", "message": f"Memory ID {mem_id} not found."})

            elif tool_name == "sqlite_list_memories":
                category = args.get("category")
                limit = int(args.get("limit", 10))
                memories = self.list_memories(category=category, limit=limit)
                return json.dumps({"status": "success", "count": len(memories), "memories": memories})

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def shutdown(self) -> None:
        pass


def register(ctx) -> None:
    """Register SQLite memory provider plugin."""
    ctx.register_memory_provider(SQLiteMemoryProvider())
