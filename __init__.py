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
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

try:
    from .memory_stats import collect_memory_stats
except ImportError:
    from memory_stats import collect_memory_stats

try:
    from .config_fields import CONFIG_FIELD_DEFINITIONS, CONFIG_SECTION_KEYS
except ImportError:
    from config_fields import CONFIG_FIELD_DEFINITIONS, CONFIG_SECTION_KEYS

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

STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_stats",
        "description": "Get counts, categories, last update time, and SQLite database/WAL sizes.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def _get_hermes_home_dir(hermes_home: Optional[str] = None) -> Path:
    """Prefer the active profile home supplied by MemoryProvider.initialize()."""
    if hermes_home and str(hermes_home).strip():
        return Path(os.path.expanduser(str(hermes_home).strip()))

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


def _get_memory_config_value(
    config: Any,
    cfg_get: Callable[..., Any],
    key: str,
    default: Any = None,
    *,
    skip_empty: bool = False,
) -> Any:
    """Read a provider setting, preferring the canonical key spelling."""
    for section in CONFIG_SECTION_KEYS:
        value = cfg_get(config, "memory", section, key, default=None)
        if value is not None and not (skip_empty and not value):
            return value
    return default


def _setup_field_dict(
    key: str,
    label: str,
    description: Optional[str],
    default: Any,
    secret: bool,
    kind: str,
    env_key: Optional[str] = None,
    options=(),
) -> Dict[str, Any]:
    item = {
        "key": key,
        "description": description or label,
        "default": default,
        "secret": secret,
    }
    if env_key:
        item["env_var"] = env_key
    if kind == "bool":
        item["choices"] = ["true", "false"]
    elif kind == "select":
        item["choices"] = [option.value for option in options]
    return item


def _setup_schema_fields(schema) -> List[Dict[str, Any]]:
    """Adapt the UI config schema to the field-dictionary contract used by memory setup."""
    return [
        _setup_field_dict(
            key=field.key,
            label=field.label,
            description=field.description,
            default=field.default,
            secret=field.is_secret,
            kind=field.kind,
            env_key=field.env_key,
            options=getattr(field, "options", ()),
        )
        for field in schema.fields
    ]


def _normalize_memory_content(content: str) -> str:
    """Normalize text conservatively for matching mirrored entries."""
    normalized = unicodedata.normalize("NFKC", content)
    return " ".join(normalized.split())


def _get_hermes_backup_root(hermes_home: Path) -> Path:
    """Resolve the root covered by Hermes backup for a profile-scoped home."""
    resolved_home = hermes_home.expanduser().resolve()
    try:
        from hermes_constants import get_default_hermes_root
        backup_root = Path(get_default_hermes_root()).expanduser().resolve()
        resolved_home.relative_to(backup_root)
        return backup_root
    except (ImportError, OSError, RuntimeError, ValueError):
        pass

    parts = resolved_home.parts
    if ".hermes" in parts:
        return Path(*parts[:parts.index(".hermes") + 1])
    if resolved_home.parent.name == "profiles":
        return resolved_home.parent.parent
    return resolved_home


class SQLiteMemoryProvider(MemoryProvider):
    """Local SQLite-backed persistent memory provider with FTS5 search."""

    def __init__(self) -> None:
        super().__init__()
        self._hermes_home = _get_hermes_home_dir()
        self._db_path = self._hermes_home / "memory.db"
        self._auto_extract = True
        self._max_recall = MAX_RECALL_RESULTS
        self._session_id = ""
        self._last_recall_count = 0
        self._lock = threading.Lock()
        self._initialized = False
        # Wait for initialize() to provide the profile-scoped hermes_home before creating a DB.

    @property
    def name(self) -> str:
        return "sqlite-memory"

    def is_available(self) -> bool:
        return True

    def get_config_schema(self):
        try:
            from .config_schema import CONFIG_SCHEMA
            return _setup_schema_fields(CONFIG_SCHEMA)
        except ImportError:
            return [
                _setup_field_dict(
                    key=field.key,
                    label=field.label,
                    description=field.description,
                    default=(
                        str(self._db_path)
                        if field.key == "db_path"
                        else field.default
                    ),
                    secret=False,
                    kind=field.kind,
                )
                for field in CONFIG_FIELD_DEFINITIONS
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
                    try:
                        mem_row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
                        mem_count = mem_row[0] if mem_row else 0
                        doc_row = conn.execute("SELECT COUNT(*) FROM memories_fts_docsize").fetchone()
                        doc_count = doc_row[0] if doc_row else 0
                        if mem_count != doc_count:
                            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild');")
                    except Exception as rebuild_err:
                        logger.debug("Notice during FTS integrity check: %s", rebuild_err)
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
        hermes_home = kwargs.get("hermes_home")
        self._hermes_home = _get_hermes_home_dir(hermes_home)

        db_path = self._hermes_home / "memory.db"
        auto_extract = True
        max_recall = MAX_RECALL_RESULTS
        try:
            from hermes_cli.config import load_config, cfg_get
            config = load_config()
            custom_path = _get_memory_config_value(
                config, cfg_get, "db_path", default="", skip_empty=True
            )
            if custom_path:
                db_path = Path(os.path.expanduser(str(custom_path)))

            auto_ext = _get_memory_config_value(
                config, cfg_get, "auto_extract", default="true"
            )
            auto_extract = str(auto_ext).lower() in ("true", "1", "yes")

            max_rec = _get_memory_config_value(
                config, cfg_get, "max_recall", default=MAX_RECALL_RESULTS
            )
            try:
                max_recall = int(max_rec)
            except (TypeError, ValueError, OverflowError):
                logger.warning(
                    "Invalid SQLite memory max_recall %r; using default %d",
                    max_rec,
                    MAX_RECALL_RESULTS,
                )
        except Exception as e:
            logger.debug("Failed to load SQLite memory configuration: %s", e)
            db_path = self._hermes_home / "memory.db"
            auto_extract = True
            max_recall = MAX_RECALL_RESULTS

        self._db_path = db_path
        self._auto_extract = auto_extract
        self._max_recall = max_recall

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
        rows = []
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

                    rows = conn.execute(sql, params).fetchall()

                # Fallback to LIKE search if FTS returned nothing
                if not rows and query.strip():
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

                        rows = conn.execute(sql, params).fetchall()

                results = [
                    {
                        "id": row["id"],
                        "category": row["category"],
                        "content": row["content"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
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

    def get_stats(self) -> Dict[str, Any]:
        """Return counts and on-disk sizes for the active provider database."""
        with self._conn() as conn:
            return collect_memory_stats(conn, self._db_path)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._auto_extract or not user_content:
            return

        text = user_content.strip()
        if len(text) < 5:
            return

        # Only inspect a leading directive line; searching the whole turn can
        # capture quoted code, examples, or Markdown table text as a memory.
        directive_line = text.splitlines()[0].strip()

        explicit_patterns = [
            r"(?:(?:please\s+)?remember\s+(?:that|to)?\s*:?\s*)(.+)",
            r"(?:(?:これ|以下|次|私の好みを?)\s*覚えて(?:おいて|ください|ね)?\s*[:：]?\s*)(.+)",
            r"(?:(?:今後|次回から|これからは)\s*(.+)(?:にして|を使って|でやって))",
        ]
        for pat in explicit_patterns:
            m = re.fullmatch(pat, directive_line, re.IGNORECASE)
            if m and m.group(1):
                item = m.group(1).strip()
                self.add_memory(content=item, category="preference", source="auto_extract")
                logger.info("SQLiteMemoryProvider auto-extracted memory: %s", item)
                break

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Rebind subsequent writes after the core switches the active conversation."""
        if new_session_id:
            self._session_id = new_session_id

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror committed built-in memory writes without turning edits into new rows."""
        category = "user" if target == "user" else "general"
        if action == "add":
            if content and content.strip():
                self.add_memory(content=content, category=category, source="system_sync")
            return

        if action not in {"replace", "remove"} or not isinstance(metadata, dict):
            return

        previous_content = metadata.get("previous_content")
        if not isinstance(previous_content, str) or not previous_content.strip():
            # old_text is not authoritative identity; never guess which row to edit.
            return

        with self._conn() as conn:
            exact_matches = conn.execute(
                """SELECT id, content FROM memories
                   WHERE source = 'system_sync' AND category = ? AND content = ?""",
                (category, previous_content),
            ).fetchall()
            if exact_matches:
                matches = exact_matches
            else:
                normalized_previous = _normalize_memory_content(previous_content)
                if not normalized_previous:
                    return
                candidates = conn.execute(
                    "SELECT id, content FROM memories WHERE source = 'system_sync' AND category = ?",
                    (category,),
                ).fetchall()
                matches = [
                    row
                    for row in candidates
                    if _normalize_memory_content(row["content"]) == normalized_previous
                ]
                if len(matches) > 1:
                    logger.warning(
                        "Skipping ambiguous normalized SQLite memory %s",
                        action,
                    )
                    return

            if not matches:
                return

            new_content = content.strip() if isinstance(content, str) else ""
            if action == "replace" and not new_content:
                return

            with conn:
                for row in matches:
                    memory_id = int(row["id"])
                    if action == "remove":
                        conn.execute(
                            """DELETE FROM memories
                               WHERE id = ? AND source = 'system_sync' AND category = ? AND content = ?""",
                            (memory_id, category, row["content"]),
                        )
                    else:
                        conn.execute(
                            """UPDATE memories
                               SET content = ?, category = ?, updated_at = CURRENT_TIMESTAMP
                               WHERE id = ? AND source = 'system_sync' AND category = ? AND content = ?""",
                            (new_content, category, memory_id, category, row["content"]),
                        )

    def backup_paths(self) -> List[str]:
        """Return provider state outside the root already covered by Hermes backup."""
        try:
            db_path = self._db_path.expanduser().resolve()
            backup_root = _get_hermes_backup_root(self._hermes_home)
            db_path.relative_to(backup_root)
            return []
        except ValueError:
            return [str(db_path)]
        except (OSError, RuntimeError):
            # Include an unresolved path rather than silently omitting declared state.
            return [str(self._db_path.expanduser().absolute())]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [REMEMBER_SCHEMA, SEARCH_SCHEMA, FORGET_SCHEMA, LIST_SCHEMA, STATS_SCHEMA]

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

            if tool_name == "sqlite_stats":
                return json.dumps({"status": "success", **self.get_stats()})

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
                    schema=schema.get("function", schema),
                    handler=lambda args, _name=name, **kwargs: provider.handle_tool_call(_name, args, **kwargs),
                )
