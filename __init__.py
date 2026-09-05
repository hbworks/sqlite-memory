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
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

try:
    from agent.memory_provider import MemoryProvider, RecallStatus
except ImportError:
    class MemoryProvider:  # type: ignore
        pass

    class RecallStatus:  # type: ignore
        def __init__(self, provider_label: str = "", count: int = 0, glyph: str = ""):
            self.provider_label = provider_label
            self.count = count
            self.glyph = glyph

logger = logging.getLogger(__name__)

MIN_QUERY_LEN = 2
MAX_RECALL_RESULTS = 5
INDICATOR_GLYPH = "💾"

# --- Tool Schemas ---
REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_remember",
        "description": "Store a piece of information or fact into persistent long-term SQLite memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact, preference, rule, or context to remember."},
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
        "description": "Search persistent SQLite memory using keywords or full-text query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords or phrase."},
                "limit": {"type": "integer", "description": "Maximum number of results (default 5).", "default": 5},
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
            "properties": {"memory_id": {"type": "integer", "description": "The ID of the memory to remove."}},
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
                "category": {"type": "string", "description": "Optional category filter."},
                "limit": {"type": "integer", "description": "Maximum number of memories (default 10).", "default": 10},
            },
        },
    },
}


def _get_hermes_home_dir() -> Path:
    """Resolve active Hermes home directory respecting active profiles."""
    if os.environ.get("HERMES_HOME", "").strip():
        return Path(os.path.expanduser(os.environ["HERMES_HOME"].strip()))

    prof = os.environ.get("HERMES_PROFILE", "").strip()
    if prof:
        prof_dir = Path(os.path.expanduser(f"~/.hermes/profiles/{prof}"))
        if prof_dir.exists():
            return prof_dir

    try:
        active_prof = Path(os.path.expanduser("~/.hermes/active_profile"))
        if active_prof.exists():
            prof_name = active_prof.read_text(encoding="utf-8").strip()
            if prof_name:
                p_dir = Path(os.path.expanduser(f"~/.hermes/profiles/{prof_name}"))
                if p_dir.exists():
                    return p_dir
    except Exception:
        pass

    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except Exception:
        pass

    return Path(os.path.expanduser("~/.hermes"))


def _get_configured_db_path() -> Path:
    """Read custom DB path from config or fallback to default."""
    try:
        from hermes_cli.config import load_config, cfg_get
        custom_path = cfg_get(load_config(), "memory", "sqlite_memory", "db_path", default="")
        if custom_path:
            return Path(os.path.expanduser(str(custom_path)))
    except Exception:
        pass
    return _get_hermes_home_dir() / "memory.db"


class SQLiteMemoryProvider(MemoryProvider):
    """Local SQLite-backed persistent memory provider with FTS5 search."""

    def __init__(self) -> None:
        super().__init__()
        self._db_path = _get_configured_db_path()
        self._auto_extract = True
        self._max_recall = MAX_RECALL_RESULTS
        self._session_id = ""
        self._last_recall_count = 0
        self._lock = threading.Lock()
        self._initialized = False
        self._ensure_db()

    @property
    def name(self) -> str:
        return "sqlite_memory"

    def is_available(self) -> bool:
        return True

    def get_config_schema(self):
        try:
            from .config_schema import CONFIG_SCHEMA
            return CONFIG_SCHEMA
        except Exception:
            return [
                {"key": "db_path", "description": "Path to SQLite database", "default": str(self._db_path)},
                {"key": "auto_extract", "description": "Auto extract rules/preferences", "default": "true"},
                {"key": "max_recall", "description": "Max prefetch memories", "default": "5"},
            ]

    def _get_connection(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _ensure_db(self) -> None:
        """Initialize SQLite tables and FTS5 triggers once."""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = self._get_connection()
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
                conn.close()
                self._initialized = True
            except Exception as e:
                logger.error("Failed to initialize SQLite memory tables: %s", e)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Managed SQLite connection context."""
        self._ensure_db()
        conn = self._get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self, session_id: str = "", **kwargs) -> None:
        """Called on agent startup / session initialization."""
        self._session_id = session_id
        self._last_recall_count = 0

        try:
            from hermes_cli.config import load_config, cfg_get
            config = load_config()
            custom_path = cfg_get(config, "memory", "sqlite_memory", "db_path", default="")
            self._db_path = Path(os.path.expanduser(str(custom_path))) if custom_path else _get_configured_db_path()
            self._auto_extract = str(cfg_get(config, "memory", "sqlite_memory", "auto_extract", default="true")).lower() in ("true", "1", "yes")
            self._max_recall = int(cfg_get(config, "memory", "sqlite_memory", "max_recall", default=5))
        except Exception:
            self._db_path = _get_configured_db_path()

        self._initialized = False
        self._ensure_db()
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
        clean = re.sub(r'[^\w\s\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf]', ' ', query)
        terms = [t.strip() for t in clean.split() if len(t.strip()) >= 2]
        return " OR ".join(f'"{t}"' for t in terms[:10]) if terms else ""

    def search_memories(self, query: str, limit: int = 5, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search memories using FTS5 with fallback to LIKE search."""
        results: List[Dict[str, Any]] = []
        fts_query = self._sanitize_fts_query(query)

        with self._conn() as conn:
            try:
                if fts_query:
                    sql = """
                        SELECT m.id, m.category, m.content, m.created_at, bm25(memories_fts) as rank
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

                    for row in conn.execute(sql, params).fetchall():
                        results.append({"id": row["id"], "category": row["category"], "content": row["content"], "created_at": row["created_at"]})

                # Fallback to LIKE search if FTS returned nothing
                if not results and query.strip():
                    keywords = [k.strip() for k in query.split() if len(k.strip()) >= 2][:3]
                    if keywords:
                        where = " OR ".join(["m.content LIKE ?" for _ in keywords])
                        sql = f"SELECT id, category, content, created_at FROM memories m WHERE ({where})"
                        params = [f"%{k}%" for k in keywords]
                        if category:
                            sql += " AND m.category = ?"
                            params.append(category)
                        sql += " ORDER BY m.updated_at DESC LIMIT ?"
                        params.append(limit)

                        for row in conn.execute(sql, params).fetchall():
                            results.append({"id": row["id"], "category": row["category"], "content": row["content"], "created_at": row["created_at"]})
            except Exception as e:
                logger.debug("SQLite memory search error: %s", e)

        return results

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query or len(query.strip()) < MIN_QUERY_LEN:
            self._last_recall_count = 0
            return ""

        memories = self.search_memories(query, limit=self._max_recall)
        self._last_recall_count = len(memories)
        if not memories:
            return ""

        formatted = "\n".join([f"- [ID: {m['id']}] ({m['category']}) {m['content']}" for m in memories])
        return f"## Relevant Long-Term Memories (from SQLite)\n{formatted}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def add_memory(self, content: str, category: str = "general", source: str = "manual") -> int:
        with self._conn() as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO memories (category, content, source, session_id) VALUES (?, ?, ?, ?)",
                    (category, content.strip(), source, self._session_id),
                )
                return cursor.lastrowid or 0

    def delete_memory(self, memory_id: int) -> bool:
        with self._conn() as conn:
            with conn:
                return conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def list_memories(self, category: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            sql = "SELECT id, category, content, created_at FROM memories"
            params: List[Any] = []
            if category:
                sql += " WHERE category = ?"
                params.append(category)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._auto_extract or not user_content or len(user_content.strip()) < 5:
            return

        explicit_patterns = [
            r"(?:remember\s+(?:that|to)?\s*:?\s*)(.+)",
            r"(?:please\s+remember\s*:?\s*)(.+)",
            r"(?:(?:これ|以下|次|私の好みを?)\s*覚えて(?:おいて|ください|ね)?\s*[:：]?\s*)(.+)",
            r"(?:(?:今後|次回から|これからは)\s*(.+)(?:にして|を使って|でやって))",
        ]
        text = user_content.strip()
        for pat in explicit_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m and m.group(1):
                item = m.group(1).strip()
                self.add_memory(content=item, category="preference", source="auto_extract")
                logger.info("SQLiteMemoryProvider auto-extracted memory: %s", item)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if action in {"add", "replace"} and content:
            category = "user" if target == "user" else "general"
            self.add_memory(content=content, category=category, source="system_sync")

    def backup_paths(self) -> List[str]:
        return [str(self._db_path)]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [REMEMBER_SCHEMA, SEARCH_SCHEMA, FORGET_SCHEMA, LIST_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        try:
            if tool_name == "sqlite_remember":
                content = args.get("content", "").strip()
                if not content:
                    return json.dumps({"error": "content is required"})
                mem_id = self.add_memory(content=content, category=args.get("category", "general"), source="tool")
                return json.dumps({"status": "success", "memory_id": mem_id, "message": "Fact stored in SQLite memory."})

            if tool_name == "sqlite_search":
                query = args.get("query", "").strip()
                if not query:
                    return json.dumps({"error": "query is required"})
                results = self.search_memories(query=query, limit=int(args.get("limit", 5)))
                return json.dumps({"status": "success", "count": len(results), "results": results})

            if tool_name == "sqlite_forget":
                mem_id = int(args.get("memory_id", 0))
                if not mem_id:
                    return json.dumps({"error": "memory_id is required"})
                deleted = self.delete_memory(mem_id)
                return json.dumps({"status": "success" if deleted else "not_found", "message": f"Memory ID {mem_id} {'deleted' if deleted else 'not found'}."})

            if tool_name == "sqlite_list_memories":
                memories = self.list_memories(category=args.get("category"), limit=int(args.get("limit", 10)))
                return json.dumps({"status": "success", "count": len(memories), "memories": memories})

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def shutdown(self) -> None:
        pass


def register(ctx) -> None:
    """Register SQLite memory provider plugin and tools."""
    provider = SQLiteMemoryProvider()
    if hasattr(ctx, "register_memory_provider"):
        ctx.register_memory_provider(provider)

    if hasattr(ctx, "register_tool"):
        for schema in provider.get_tool_schemas():
            name = schema.get("function", {}).get("name")
            if name:
                ctx.register_tool(
                    name=name,
                    toolset="sqlite_memory",
                    schema=schema,
                    handler=lambda args, _name=name, **kwargs: provider.handle_tool_call(_name, args, **kwargs),
                )
