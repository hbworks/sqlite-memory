"""SQLite Memory Plugin — Backend API Routes for Hermes Desktop & Dashboard.

Mounted at /api/plugins/sqlite_memory/
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_db_path() -> Path:
    try:
        from hermes_cli.config import load_config, cfg_get
        config = load_config()
        custom_path = cfg_get(config, "memory", "sqlite_memory", "db_path", default="")
        if custom_path:
            return Path(os.path.expanduser(str(custom_path)))
    except Exception:
        pass

    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home() / "memory.db"
    except Exception:
        return Path(os.path.expanduser("~/.hermes/memory.db"))


def _ensure_db_initialized(conn: sqlite3.Connection) -> None:
    """Ensure memories tables and triggers exist."""
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
                content,
                category,
                content='memories',
                content_rowid='id'
            );
        """)


def _get_conn() -> sqlite3.Connection:
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    _ensure_db_initialized(conn)
    return conn


class MemoryCreateRequest(BaseModel):
    content: str = Field(..., description="Fact or memory content")
    category: str = Field("general", description="Category for grouping")


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    category: Optional[str] = None


@router.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """Get overview statistics for persistent memories."""
    try:
        conn = _get_conn()
        try:
            total_cursor = conn.execute("SELECT COUNT(*) as cnt FROM memories")
            total_row = total_cursor.fetchone()
            total = total_row["cnt"] if total_row else 0

            cat_cursor = conn.execute("SELECT category, COUNT(*) as cnt FROM memories GROUP BY category")
            categories = {row["category"]: row["cnt"] for row in cat_cursor.fetchall()}

            db_path = _get_db_path()
            size_bytes = db_path.stat().st_size if db_path.exists() else 0

            return {
                "total_memories": total,
                "categories": categories,
                "db_path": str(db_path),
                "db_size_bytes": size_bytes,
            }
        finally:
            conn.close()
    except Exception as e:
        logger.error("Error fetching memory stats: %s", e)
        return {
            "total_memories": 0,
            "categories": {},
            "db_path": str(_get_db_path()),
            "db_size_bytes": 0,
        }


@router.get("/memories")
async def list_memories(
    query: Optional[str] = Query(None, description="Search query"),
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List or search memories."""
    try:
        conn = _get_conn()
        try:
            memories = []
            if query and query.strip():
                clean = query.strip()
                sql = """
                    SELECT m.id, m.category, m.content, m.source, m.created_at, m.updated_at
                    FROM memories m
                    WHERE m.content LIKE ?
                """
                params: List[Any] = [f"%{clean}%"]
                if category and category != "all":
                    sql += " AND m.category = ?"
                    params.append(category)
                sql += " ORDER BY m.updated_at DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                cursor = conn.execute(sql, params)
                for row in cursor.fetchall():
                    memories.append(dict(row))
            else:
                sql = "SELECT id, category, content, source, created_at, updated_at FROM memories"
                params = []
                if category and category != "all":
                    sql += " WHERE category = ?"
                    params.append(category)
                sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                cursor = conn.execute(sql, params)
                for row in cursor.fetchall():
                    memories.append(dict(row))

            count_sql = "SELECT COUNT(*) as cnt FROM memories"
            count_params = []
            if category and category != "all":
                count_sql += " WHERE category = ?"
                count_params.append(category)
            count_row = conn.execute(count_sql, count_params).fetchone()
            total = count_row["cnt"] if count_row else 0

            return {
                "items": memories,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        finally:
            conn.close()
    except Exception as e:
        logger.error("Error listing memories: %s", e)
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
        }


@router.post("/memories", status_code=http_status.HTTP_201_CREATED)
async def create_memory(req: MemoryCreateRequest) -> Dict[str, Any]:
    """Create a new memory manually."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    conn = _get_conn()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO memories (category, content, source)
                VALUES (?, ?, 'desktop_ui')
                """,
                (req.category.strip() or "general", req.content.strip()),
            )
            new_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (new_id,)).fetchone()
            return dict(row)
    except Exception as e:
        logger.error("Error creating memory: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.put("/memories/{memory_id}")
async def update_memory(memory_id: int, req: MemoryUpdateRequest) -> Dict[str, Any]:
    """Update an existing memory."""
    conn = _get_conn()
    try:
        with conn:
            existing = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Memory not found")

            new_content = req.content.strip() if req.content is not None else existing["content"]
            new_category = req.category.strip() if req.category is not None else existing["category"]

            conn.execute(
                """
                UPDATE memories
                SET content = ?, category = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_content, new_category, memory_id),
            )
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating memory: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: int) -> Dict[str, Any]:
    """Delete a memory by ID."""
    conn = _get_conn()
    try:
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
    finally:
        conn.close()
