"""SQLite Memory Plugin — Backend API Routes for Hermes Desktop & Dashboard.

Mounted at /api/plugins/sqlite-memory/ (and /api/plugins/sqlite_memory/ for backwards compatibility)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

try:
    from ..memory_stats import collect_memory_stats
except ImportError:
    from memory_stats import collect_memory_stats

try:
    from fastapi import APIRouter, HTTPException, Query, status as http_status
    from pydantic import BaseModel, Field
except ImportError:
    class HTTPException(Exception):  # type: ignore
        def __init__(self, status_code: int = 400, detail: str = ""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    class http_status:  # type: ignore
        HTTP_400_BAD_REQUEST = 400
        HTTP_201_CREATED = 201

    class APIRouter:  # type: ignore
        def get(self, *args, **kwargs): return lambda f: f
        def post(self, *args, **kwargs): return lambda f: f
        def put(self, *args, **kwargs): return lambda f: f
        def delete(self, *args, **kwargs): return lambda f: f

    def Query(default=None, **kwargs):  # type: ignore
        return default

    class BaseModel:  # type: ignore
        def __init__(self, **data: Any):
            for k, v in data.items():
                setattr(self, k, v)

    def Field(default=..., **kwargs):  # type: ignore
        return default

logger = logging.getLogger(__name__)
router = APIRouter()

# 厳格なプロファイル名バリデーション用（英数字、ハイフン、アンダースコアのみ許可）
_PROFILE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ROOT_PROFILE_ALIASES = frozenset(("default", "main", "root", "~/.hermes"))

# 初期化済み DB パスのキャッシュ（リクエスト毎の重複 DDL 実行を抑制）
_INITIALIZED_DBS: Dict[str, tuple[int, int, int]] = {}


def _get_hermes_root() -> Path:
    """Get the true ~/.hermes root directory, even when HERMES_HOME points to a profile."""
    if "HERMES_HOME" in os.environ and os.environ["HERMES_HOME"].strip():
        p = Path(os.path.expanduser(os.environ["HERMES_HOME"].strip()))
        if "profiles" in p.parts:
            parts = list(p.parts)
            idx = parts.index("profiles")
            return Path(*parts[:idx])
        return p
    return Path(os.path.expanduser("~/.hermes"))


def _validate_profile(profile: Optional[str]) -> str:
    """Validate and sanitize profile name to prevent path traversal."""
    if not profile or not isinstance(profile, str):
        return ""
    prof = profile.strip()
    if not prof:
        return ""
    if prof in _ROOT_PROFILE_ALIASES:
        return prof
    if not _PROFILE_RE.match(prof):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid profile name '{prof}': only alphanumeric characters, underscores, and hyphens are allowed."
        )
    return prof


def _validated_profile_dir(root: Path, profile: str) -> Path:
    """Return a profile directory only when its resolved path stays under the Hermes root."""
    prof = _validate_profile(profile)
    prof_dir = root / "profiles" / prof
    try:
        prof_dir.resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Path traversal detected: profile directory is outside Hermes root."
        )
    return prof_dir


def _validated_profile_db_path(root: Path, prof_dir: Path) -> Path:
    """Return the default DB path only when its resolved target stays under the Hermes root."""
    target_path = prof_dir / "memory.db"
    try:
        target_path.resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Path traversal detected: target database is outside Hermes root."
        )
    return target_path


def _db_schema_cache_signature(conn: sqlite3.Connection, db_path: Path) -> Optional[tuple[int, int, int]]:
    """Identify a database file and schema version for safe initialization-cache reuse."""
    try:
        db_stat = db_path.stat()
        schema_row = conn.execute("PRAGMA schema_version").fetchone()
    except (OSError, sqlite3.Error):
        return None
    if schema_row is None:
        return None
    return db_stat.st_dev, db_stat.st_ino, int(schema_row[0])


def _read_config_db_path(config_file: Path) -> Optional[Path]:
    """Prefer sqlite-memory db_path, falling back to the legacy sqlite_memory key."""
    if not config_file.is_file():
        return None
    try:
        try:
            import yaml
            with open(config_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            memory_cfg = data.get("memory")
            if isinstance(memory_cfg, dict):
                for config_key in ("sqlite-memory", "sqlite_memory"):
                    cfg_obj = memory_cfg.get(config_key)
                    if not isinstance(cfg_obj, dict):
                        continue
                    p = cfg_obj.get("db_path")
                    if p:
                        return Path(os.path.expanduser(str(p)))
        except ImportError:
            content = config_file.read_text(encoding="utf-8")
            db_path_pattern = r"db_path:\s*([^\r\n#]+)"
            match = re.search(r"sqlite-memory[\s\S]*?" + db_path_pattern, content)
            if match is None:
                match = re.search(r"sqlite_memory[\s\S]*?" + db_path_pattern, content)
            if match:
                val = match.group(1).strip().strip('"\'')
                if val:
                    return Path(os.path.expanduser(val))
    except Exception as e:
        logger.debug("Failed to read config from %s: %s", config_file, e)
    return None


def _get_available_profiles() -> List[str]:
    """List all available profiles."""
    profiles = ["default"]
    p_dir = _get_hermes_root() / "profiles"
    if p_dir.is_dir():
        found = [p.name for p in sorted(p_dir.iterdir()) if p.is_dir() and not p.name.startswith(".") and p.name != "default"]
        profiles.extend(found)
    return profiles


def _get_db_path(profile: Optional[str] = None) -> Path:
    """Resolve database path for the requested profile safely with config support and boundary checks."""
    prof = _validate_profile(profile)
    root = _get_hermes_root()

    # 1. default / main / root / ~/.hermes が指定された場合
    if prof in _ROOT_PROFILE_ALIASES:
        cfg_path = _read_config_db_path(root / "config.yaml")
        return cfg_path if cfg_path else root / "memory.db"

    # 2. 特定プロファイルが明示指定された場合
    if prof:
        prof_dir = _validated_profile_dir(root, prof)
        cfg_path = _read_config_db_path(prof_dir / "config.yaml") or _read_config_db_path(root / "config.yaml")
        if cfg_path:
            return cfg_path

        return _validated_profile_db_path(root, prof_dir)

    # 3. 未指定の場合（HERMES_HOME、HERMES_PROFILE、active_profile、または root）
    if "HERMES_HOME" in os.environ and os.environ["HERMES_HOME"].strip():
        h_home = Path(os.path.expanduser(os.environ["HERMES_HOME"].strip()))
        cfg_path = _read_config_db_path(h_home / "config.yaml") or _read_config_db_path(root / "config.yaml")
        return cfg_path if cfg_path else h_home / "memory.db"

    # アクティブプロファイルの自動検出フォールバック（Agent本体側の動作と整合）
    prof_env = os.environ.get("HERMES_PROFILE", "").strip()
    if prof_env:
        prof_env = _validate_profile(prof_env)
        if prof_env in _ROOT_PROFILE_ALIASES:
            cfg_path = _read_config_db_path(root / "config.yaml")
            return cfg_path if cfg_path else root / "memory.db"
        prof_dir = _validated_profile_dir(root, prof_env)
        if prof_dir.is_dir():
            cfg_path = _read_config_db_path(prof_dir / "config.yaml") or _read_config_db_path(root / "config.yaml")
            return cfg_path if cfg_path else _validated_profile_db_path(root, prof_dir)

    active_name = ""
    try:
        active_prof_file = root / "active_profile"
        if active_prof_file.is_file():
            active_name = active_prof_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        pass

    if active_name:
        active_name = _validate_profile(active_name)
        if active_name not in _ROOT_PROFILE_ALIASES:
            prof_dir = _validated_profile_dir(root, active_name)
            if prof_dir.is_dir():
                cfg_path = _read_config_db_path(prof_dir / "config.yaml") or _read_config_db_path(root / "config.yaml")
                return cfg_path if cfg_path else _validated_profile_db_path(root, prof_dir)

    cfg_path = _read_config_db_path(root / "config.yaml")
    return cfg_path if cfg_path else root / "memory.db"


def _ensure_db_schema(conn: sqlite3.Connection, db_path: Optional[Path] = None) -> None:
    """Ensure memories tables exist, register FTS triggers, and rebuild FTS if out of sync."""
    path_key = str(db_path.resolve()) if db_path else None
    cache_signature = _db_schema_cache_signature(conn, Path(path_key)) if path_key else None
    if path_key and cache_signature is not None and _INITIALIZED_DBS.get(path_key) == cache_signature:
        return

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
        # Agent 側と同一の 3 つの同期トリガーを追加
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

        # 既存レコードの FTS 同期チェック＆再構築
        try:
            mem_row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            mem_count = mem_row[0] if mem_row else 0
            doc_row = conn.execute("SELECT COUNT(*) FROM memories_fts_docsize").fetchone()
            doc_count = doc_row[0] if doc_row else 0
            if mem_count != doc_count:
                conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild');")
            fts_check_completed = True
        except Exception as e:
            logger.debug("Notice during FTS integrity check: %s", e)

    if path_key and fts_check_completed:
        cache_signature = _db_schema_cache_signature(conn, Path(path_key))
        if cache_signature is not None:
            _INITIALIZED_DBS[path_key] = cache_signature


@contextmanager
def _db_conn(profile: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Managed DB connection with auto-closing."""
    db_path = _get_db_path(profile)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    _ensure_db_schema(conn, db_path)
    try:
        yield conn
    finally:
        conn.close()


class MemoryCreateRequest(BaseModel):
    content: str = Field(..., description="Fact or memory content")
    category: str = Field("general", description="Category for grouping")


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    category: Optional[str] = None


@router.get("/stats")
async def get_stats(profile: Optional[str] = Query(None, description="Profile name")) -> Dict[str, Any]:
    """Get overview statistics for persistent memories."""
    db_path = _get_db_path(profile)
    try:
        with _db_conn(profile) as conn:
            stats = collect_memory_stats(conn, db_path)
            return {
                **stats,
                "db_path": str(db_path),
                "profile": profile or "default",
                "available_profiles": _get_available_profiles(),
            }
    except Exception as e:
        logger.error("Error fetching memory stats: %s", e)
        return {
            "total_memories": 0,
            "categories": {},
            "db_path": str(db_path),
            "db_size_bytes": 0,
            "profile": profile or "default",
            "available_profiles": _get_available_profiles(),
        }


@router.get("/memories")
async def list_memories(
    query: Optional[str] = Query(None, description="Search query"),
    category: Optional[str] = Query(None, description="Filter by category"),
    profile: Optional[str] = Query(None, description="Profile name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List or search memories."""
    try:
        with _db_conn(profile) as conn:
            where_clauses: List[str] = []
            params: List[Any] = []

            if query and query.strip():
                where_clauses.append("m.content LIKE ?")
                params.append(f"%{query.strip()}%")

            if category and category != "all":
                where_clauses.append("m.category = ?")
                params.append(category)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            # Fetch items
            items_sql = f"""
                SELECT id, category, content, source, created_at, updated_at
                FROM memories m
                {where_sql}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """
            items = [dict(r) for r in conn.execute(items_sql, params + [limit, offset]).fetchall()]

            # Fetch total count
            count_sql = f"SELECT COUNT(*) as cnt FROM memories m {where_sql}"
            count_row = conn.execute(count_sql, params).fetchone()
            total = count_row["cnt"] if count_row else 0

            return {"items": items, "total": total, "limit": limit, "offset": offset}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing memories: %s", e)
        return {"items": [], "total": 0, "limit": limit, "offset": offset}


@router.post("/memories", status_code=http_status.HTTP_201_CREATED)
async def create_memory(
    req: MemoryCreateRequest,
    profile: Optional[str] = Query(None, description="Profile name"),
) -> Dict[str, Any]:
    """Create a new memory manually."""
    if not req.content or not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    try:
        with _db_conn(profile) as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO memories (category, content, source) VALUES (?, ?, 'desktop_ui')",
                    (req.category.strip() or "general", req.content.strip()),
                )
                row = conn.execute("SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating memory: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: int,
    req: MemoryUpdateRequest,
    profile: Optional[str] = Query(None, description="Profile name"),
) -> Dict[str, Any]:
    """Update an existing memory."""
    if req.content is not None and not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    try:
        with _db_conn(profile) as conn:
            with conn:
                existing = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Memory not found")

                content = req.content.strip() if req.content is not None else existing["content"]
                category = (req.category.strip() or existing["category"]) if req.category is not None else existing["category"]

                conn.execute(
                    "UPDATE memories SET content = ?, category = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (content, category, memory_id),
                )
                row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating memory: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: int,
    profile: Optional[str] = Query(None, description="Profile name"),
) -> Dict[str, Any]:
    """Delete a memory by ID."""
    try:
        with _db_conn(profile) as conn:
            with conn:
                cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Memory not found")
                return {"status": "success", "deleted_id": memory_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting memory: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
