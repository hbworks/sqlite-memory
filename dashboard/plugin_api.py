"""SQLite Memory Plugin — Backend API Routes for Hermes Desktop & Dashboard.

Mounted at /api/plugins/sqlite_memory/
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from fastapi import APIRouter, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_hermes_root() -> Path:
    """Get the true ~/.hermes root directory, even when HERMES_HOME points to a profile."""
    if "HERMES_HOME" in os.environ and os.environ["HERMES_HOME"].strip():
        p = Path(os.path.expanduser(os.environ["HERMES_HOME"].strip()))
        if "profiles" in p.parts:
            parts = list(p.parts)
            idx = parts.index("profiles")
            return Path(*parts[:idx])
    return Path(os.path.expanduser("~/.hermes"))


def _get_available_profiles() -> List[str]:
    """List all available profiles."""
    profiles = ["default"]
    p_dir = _get_hermes_root() / "profiles"
    if p_dir.is_dir():
        profiles.extend([p.name for p in sorted(p_dir.iterdir()) if p.is_dir() and not p.name.startswith(".")])
    return profiles


def _get_db_path(profile: Optional[str] = None) -> Path:
    """Resolve database path for the requested profile."""
    prof = (profile or "").strip()
    root = _get_hermes_root()
    # default / ~/.hermes / root / main が指定された場合は必ず ~/.hermes/memory.db
    if prof in ("default", "main", "root", "~/.hermes"):
        return root / "memory.db"
    # 特定プロファイルが明示指定された場合
    if prof:
        return root / "profiles" / prof / "memory.db"
    # 未指定の場合は現在の HERMES_HOME、なければ root / memory.db
    if "HERMES_HOME" in os.environ and os.environ["HERMES_HOME"].strip():
        return Path(os.path.expanduser(os.environ["HERMES_HOME"].strip())) / "memory.db"
    return root / "memory.db"


def _ensure_db_schema(conn: sqlite3.Connection) -> None:
    """Ensure memories tables exist."""
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


@contextmanager
def _db_conn(profile: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Managed DB connection with auto-closing."""
    db_path = _get_db_path(profile)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    _ensure_db_schema(conn)
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
            total_row = conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()
            total = total_row["cnt"] if total_row else 0
            cat_rows = conn.execute("SELECT category, COUNT(*) as cnt FROM memories GROUP BY category").fetchall()
            categories = {row["category"]: row["cnt"] for row in cat_rows}
            size_bytes = db_path.stat().st_size if db_path.exists() else 0

            return {
                "total_memories": total,
                "categories": categories,
                "db_path": str(db_path),
                "db_size_bytes": size_bytes,
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
    except Exception as e:
        logger.error("Error listing memories: %s", e)
        return {"items": [], "total": 0, "limit": limit, "offset": offset}


@router.post("/memories", status_code=http_status.HTTP_201_CREATED)
async def create_memory(
    req: MemoryCreateRequest,
    profile: Optional[str] = Query(None, description="Profile name"),
) -> Dict[str, Any]:
    """Create a new memory manually."""
    if not req.content.strip():
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
    try:
        with _db_conn(profile) as conn:
            with conn:
                existing = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Memory not found")

                content = req.content.strip() if req.content is not None else existing["content"]
                category = req.category.strip() if req.category is not None else existing["category"]

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
