"""SQLite Memory Plugin for Hermes Agent.

Provides zero-dependency, local-first persistent long-term memory using
Python's built-in sqlite3 and FTS5 full-text search.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Set

try:
    from .memory_stats import collect_memory_stats
except ImportError:
    from memory_stats import collect_memory_stats

try:
    from .memory_schema import ensure_memory_schema
except ImportError:
    from memory_schema import ensure_memory_schema

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
MAX_MEMORY_RESULTS = 100
MAX_JAPANESE_SEARCH_TERMS = 500
INDICATOR_GLYPH = "💾"

# --- Freshness policy ---
# Days after which a memory of this category is treated as needing re-verification.
# None = never expires (durable preferences and identity facts).
CATEGORY_TTL_DAYS: Dict[str, Optional[int]] = {
    "hermes-config": 30,
    "environment": 30,
    "project": 90,
    "general": 180,
    "workflow": 180,
    "decision": 180,
    "skill": 180,
    "team": 180,
    "preference": None,
    "user": None,
    "user_profile": None,
}
DEFAULT_TTL_DAYS = 180
STALE_MARKER = "⚠要確認"
# SQL expression for "the moment this row was last known to be true"; mirrors _known_at().
_KNOWN_AT_SQL = "COALESCE(verified_at, updated_at, created_at)"
# A sentence pinned to a moment (a date followed by a state word: 時点 / 確認 / 現在 / 実測)
# decays even inside a durable category — it asserts a state, not a timeless rule. A bare
# provenance date ("（2026-11-01 ユーザー指示）") does not.
_DATE_PREFIX = r"\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?"
TIME_BOUND_PATTERN = re.compile(
    rf"({_DATE_PREFIX}[^\n]{{0,16}}時点|時点で|\bas of\b|^\s*{_DATE_PREFIX}\s*(?:確認|現在|実測))",
    re.IGNORECASE,
)

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
                "supersedes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of memories this fact replaces. They are marked superseded and stop appearing in search.",
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
                "limit": {"type": "integer", "description": "Maximum number of results (default 5, bounded to 1–100).", "default": 5},
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
                "limit": {"type": "integer", "description": "Maximum number of memories (default 10, bounded to 1–100).", "default": 10},
                "include_superseded": {
                    "type": "boolean",
                    "description": "Also list memories that a newer memory replaced (default false).",
                    "default": False,
                },
            },
        },
    },
}

REVIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_review",
        "description": "List memories past their freshness window (category TTL, unknown date, or a date-pinned claim) so they can be re-verified or replaced.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Also flag memories older than this many days. 0 (default) applies the per-category policy only.",
                    "default": 0,
                },
                "category": {"type": "string", "description": "Optional category filter."},
                "limit": {"type": "integer", "description": "Maximum number of entries (default 50, bounded to 1–100).", "default": 50},
            },
        },
    },
}

VERIFY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sqlite_verify",
        "description": "Mark memories as still true right now, resetting their freshness clock.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of the memories that were confirmed.",
                },
            },
            "required": ["memory_ids"],
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
    for section in ("sqlite-memory", "sqlite_memory"):
        value = cfg_get(config, "memory", section, key, default=None)
        if value is not None and not (skip_empty and not value):
            return value
    return default


_MEMORY_PROVIDER_CONFIG_KEYS = ("db_path", "auto_extract", "max_recall")


def _is_path_within_home(path: Path, hermes_home: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(hermes_home.expanduser().resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _desktop_provider_config_path(hermes_home: Path, provider_name: str):
    home = hermes_home.expanduser().resolve()
    provider_dir = home / provider_name
    config_path = provider_dir / "config.json"
    if (
        provider_dir.is_symlink()
        or config_path.is_symlink()
        or not _is_path_within_home(config_path, home)
    ):
        logger.warning(
            "Ignoring SQLite memory Desktop settings outside the active profile: %s",
            config_path,
        )
        return None
    return home, config_path


def _read_desktop_provider_config(path: Path):
    if path.parent.is_symlink() or path.is_symlink():
        return True, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return False, {}
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning("Could not read SQLite memory Desktop settings at %s: %s", path, exc)
        return True, {}
    if not isinstance(data, dict):
        logger.warning("Ignoring non-object SQLite memory Desktop settings at %s", path)
        return True, {}
    return True, {
        key: data[key]
        for key in _MEMORY_PROVIDER_CONFIG_KEYS
        if key in data and data[key] is not None
    }


def _migrate_yaml_provider_config(path: Path, hermes_home: Path, yaml_values: Dict[str, Any]):
    """Seed the Desktop flat config once, without replacing a concurrent UI save."""
    if not yaml_values:
        return {}

    temp_path = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            path.parent.is_symlink()
            or path.is_symlink()
            or not _is_path_within_home(path, hermes_home)
        ):
            logger.warning("Refusing to migrate SQLite memory settings outside the profile: %s", path)
            return {}

        fd, temp_name = tempfile.mkstemp(
            prefix=".sqlite-memory-config-", dir=str(path.parent)
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(yaml_values, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        try:
            os.link(str(temp_path), str(path))
        except FileExistsError:
            _exists, current_values = _read_desktop_provider_config(path)
            return current_values
        return dict(yaml_values)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Could not migrate SQLite memory settings to %s: %s", path, exc)
        return {}
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _load_desktop_provider_config(
    hermes_home: Path, provider_name: str, yaml_values: Dict[str, Any]
) -> Dict[str, Any]:
    paths = _desktop_provider_config_path(hermes_home, provider_name)
    if paths is None:
        return {}
    home, path = paths
    exists, desktop_values = _read_desktop_provider_config(path)
    if exists:
        return desktop_values
    return _migrate_yaml_provider_config(path, home, yaml_values)


def _yaml_provider_config_values(config, cfg_get) -> Dict[str, Any]:
    values = {}
    for key in _MEMORY_PROVIDER_CONFIG_KEYS:
        value = _get_memory_config_value(
            config,
            cfg_get,
            key,
            default=None,
            skip_empty=(key == "db_path"),
        )
        if value is not None:
            values[key] = value
    return values


def _setup_schema_fields(schema) -> List[Dict[str, Any]]:
    """Adapt the UI config schema to the field-dictionary contract used by memory setup."""
    fields = []
    for field in schema.fields:
        item = {
            "key": field.key,
            "description": field.description or field.label,
            "default": field.default,
            "secret": field.is_secret,
        }
        if field.env_key:
            item["env_var"] = field.env_key
        if field.kind == "bool":
            item["choices"] = ["true", "false"]
        elif field.kind == "select":
            item["choices"] = [option.value for option in field.options]
        for bound_key in ("minimum", "maximum", "step"):
            bound = getattr(field, bound_key, None)
            if bound is not None:
                item[bound_key] = bound
        fields.append(item)
    return fields


def _normalize_memory_content(content: str) -> str:
    """Normalize text conservatively for deduplication and mirror matching."""
    normalized = unicodedata.normalize("NFKC", content)
    return " ".join(normalized.split())


def _bounded_limit(value: Any, default: int) -> int:
    """Normalize a result limit and prevent SQLite's negative-LIMIT behavior."""
    if value is None:
        value = default
    if isinstance(value, bool):
        raise ValueError("limit must be an integer")
    try:
        limit = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("limit must be an integer") from exc
    return max(1, min(limit, MAX_MEMORY_RESULTS))


def _bounded_max_recall(value: Any, default: int = MAX_RECALL_RESULTS) -> int:
    """Clamp the configured recall count to the settings range (5–100)."""
    return max(MAX_RECALL_RESULTS, _bounded_limit(value, default=default))


# --- Freshness helpers ---

def _parse_timestamp(value: Any) -> Optional[datetime.datetime]:
    """Parse the timestamp formats SQLite holds (CURRENT_TIMESTAMP, ISO, epoch)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            moment = datetime.datetime.fromtimestamp(float(value), datetime.timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        moment = None
        for parse in (
            lambda t: datetime.datetime.fromisoformat(t.replace("Z", "+00:00")),
            lambda t: datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S"),
            lambda t: datetime.datetime.strptime(t, "%Y-%m-%d"),
        ):
            try:
                moment = parse(text)
                break
            except ValueError:
                continue
        if moment is None:
            return None
    if moment.tzinfo is None:
        # SQLite CURRENT_TIMESTAMP is UTC.
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def _age_days(value: Any) -> Optional[int]:
    moment = _parse_timestamp(value)
    if moment is None:
        return None
    delta = datetime.datetime.now(datetime.timezone.utc) - moment
    return max(0, int(delta.total_seconds() // 86400))


def _ttl_days(category: Optional[str]) -> Optional[int]:
    if not category:
        return DEFAULT_TTL_DAYS
    return CATEGORY_TTL_DAYS.get(category, DEFAULT_TTL_DAYS)


def _freshness(category: Optional[str], verified_at: Any, content: str = "") -> Dict[str, Any]:
    """Describe how trustworthy an entry is right now, without deciding for the reader."""
    age = _age_days(verified_at)
    ttl = _ttl_days(category)
    # A dated claim written/verified moments ago is simply current; flag it once it ages,
    # otherwise every fresh write would be reported as needing review.
    dated = bool(TIME_BOUND_PATTERN.search(content or "")) and (age is None or age > 1)
    return {
        "verified_at": verified_at,
        "age_days": age,
        "ttl_days": ttl,
        "stale": (ttl is not None and age is not None and age > ttl),
        "date_pinned": dated,
    }


def _known_at(row: Any) -> Any:
    """The row's last known-good moment: verified_at, else the last write.

    Resolved in one place so search, review and the prefetch hint cannot disagree about
    how old a row is (a NULL verified_at used to read as "date unknown" in search but as
    a dated row in review).
    """
    return row["verified_at"] or row["updated_at"] or row["created_at"]


def _overdue_reasons(freshness: Dict[str, Any], days: int = 0) -> List[str]:
    """Why this entry needs re-verification, if it does. One policy for every caller."""
    reasons: List[str] = []
    if freshness["age_days"] is None:
        reasons.append("日付不明")
    if freshness["stale"]:
        reasons.append(f"TTL {freshness['ttl_days']}日超過")
    if freshness["date_pinned"]:
        reasons.append("時点付きの記述")
    if days and days > 0 and freshness["age_days"] is not None and freshness["age_days"] > days:
        reasons.append(f"{days}日超過")
    return reasons


def _coerce_memory_ids(raw_ids: Any) -> List[int]:
    """Positive, de-duplicated IDs from loose tool input; anything unusable is dropped."""
    ids: List[int] = []
    seen: Set[int] = set()
    for raw in raw_ids or []:
        if isinstance(raw, bool):
            continue
        try:
            memory_id = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if memory_id <= 0 or memory_id in seen:
            continue
        seen.add(memory_id)
        ids.append(memory_id)
    return ids


def _freshness_label(fields: Dict[str, Any]) -> str:
    """One-line marker for the injected memory block."""
    label = f"{fields['age_days']}日前" if fields.get("age_days") is not None else "日付不明"
    if fields.get("stale"):
        return f"{label}, {STALE_MARKER}"
    if fields.get("date_pinned"):
        return f"{label}, 時点付き"
    return label


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
        self._agent_context = "primary"
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
                {
                    "key": "db_path",
                    "description": "Optional; leave blank for the active profile's memory.db.",
                    "default": "",
                    "secret": False,
                },
                {
                    "key": "auto_extract",
                    "description": "Save explicit remember requests and reply preferences from primary agent context only",
                    "default": "true",
                    "secret": False,
                    "choices": ["true", "false"],
                },
                {
                    "key": "max_recall",
                    "description": "Max prefetch memories (bounded to 5–100)",
                    "default": "5",
                    "secret": False,
                    "minimum": 5,
                    "maximum": 100,
                    "step": 1,
                },
            ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Persist provider settings in the active profile's config.yaml section."""
        from hermes_cli.config import load_config, save_config as write_config
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        home_token = (
            set_hermes_home_override(hermes_home) if hermes_home else None
        )
        try:
            config = load_config()
            if not isinstance(config, dict):
                config = {}

            memory_config = config.get("memory")
            if not isinstance(memory_config, dict):
                memory_config = config["memory"] = {}

            current = memory_config.get(self.name)
            current = current if isinstance(current, dict) else {}
            allowed_keys = {"db_path", "auto_extract", "max_recall"}
            updates = {key: value for key, value in values.items() if key in allowed_keys}
            if not updates:
                return

            memory_config[self.name] = {**current, **updates}
            write_config(config)
        finally:
            if home_token is not None:
                reset_hermes_home_override(home_token)

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
            conn = None
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = self._get_connection()
                ensure_memory_schema(conn)
                self._initialized = True
            except Exception as e:
                logger.error("Failed to initialize SQLite memory tables: %s", e)
                raise
            finally:
                if conn is not None:
                    conn.close()

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
        agent_context = kwargs.get("agent_context", "primary")
        self._agent_context = agent_context if isinstance(agent_context, str) else ""
        self._last_recall_count = 0
        hermes_home = kwargs.get("hermes_home")
        self._hermes_home = _get_hermes_home_dir(hermes_home)

        db_path = self._hermes_home / "memory.db"
        auto_extract = True
        max_recall = MAX_RECALL_RESULTS
        try:
            from hermes_cli.config import load_config, cfg_get
            config = load_config()
            yaml_values = _yaml_provider_config_values(config, cfg_get)
            desktop_values = _load_desktop_provider_config(
                self._hermes_home, self.name, yaml_values
            )
            effective_values = {**yaml_values, **desktop_values}

            custom_path = effective_values.get("db_path", "")
            if custom_path:
                db_path = Path(os.path.expanduser(str(custom_path)))

            auto_ext = effective_values.get("auto_extract", "true")
            auto_extract = str(auto_ext).lower() in ("true", "1", "yes")

            max_rec = effective_values.get("max_recall", MAX_RECALL_RESULTS)
            try:
                max_recall = _bounded_max_recall(max_rec, default=MAX_RECALL_RESULTS)
            except ValueError:
                logger.warning(
                    "Invalid SQLite memory max_recall %r; using default %d",
                    max_rec,
                    MAX_RECALL_RESULTS,
                )
                max_recall = MAX_RECALL_RESULTS
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
            "- Injected/search results carry an age and ⚠要確認 when past their freshness window: "
            "verify such a memory against reality before relying on it.\n"
            "- When a new fact replaces an old memory, pass `supersedes: [id]` to `sqlite_remember`; "
            "the old row stops appearing in search but stays auditable.\n"
            "- Use `sqlite_review` to list memories that need re-verification, and `sqlite_verify` "
            "once you have confirmed them.\n"
            "- Use `sqlite_forget` if a memory is outdated or contradicted."
        )

    def recall_status(self) -> Optional[RecallStatus]:
        if self._last_recall_count <= 0:
            return None
        return RecallStatus(
            provider_label="SQLite Memory",
            count=self._last_recall_count,
            glyph=INDICATOR_GLYPH,
        )

    @staticmethod
    def _is_japanese_char(char: str) -> bool:
        codepoint = ord(char)
        return (
            0x3040 <= codepoint <= 0x30FA
            or 0x30FC <= codepoint <= 0x30FF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0xFF66 <= codepoint <= 0xFF9F
            or 0x20000 <= codepoint <= 0x323AF
            or char in "々〆〇〻"
        )

    @classmethod
    def _contains_japanese(cls, text: str) -> bool:
        return any(cls._is_japanese_char(char) for char in text)

    @staticmethod
    def _sample_starts(total: int, limit: int) -> List[int]:
        """Pick indexes across the whole run, including both ends."""
        if limit <= 0 or total <= 0:
            return []
        if total <= limit:
            return list(range(total))
        if limit == 1:
            return [0]
        last = total - 1
        chosen = {0, last}
        if limit > 2:
            step = last / (limit - 1)
            for index in range(1, limit - 1):
                chosen.add(min(last, int(round(index * step))))
        if len(chosen) < limit:
            for index in range(last, -1, -1):
                chosen.add(index)
                if len(chosen) >= limit:
                    break
        return sorted(chosen)[:limit]

    @staticmethod
    def _proportional_quotas(totals: List[int], room: int) -> List[int]:
        """Share a term budget across runs so a later short run is not dropped."""
        if room <= 0 or not totals:
            return [0 for _ in totals]
        if sum(totals) <= room:
            return list(totals)
        if room < len(totals):
            order = sorted(range(len(totals)), key=lambda index: totals[index], reverse=True)
            quotas = [0 for _ in totals]
            for index in order[:room]:
                quotas[index] = 1
            return [min(quotas[index], totals[index]) for index in range(len(totals))]

        quotas = [1 for _ in totals]
        room -= len(totals)
        weights = [max(0, total - 1) for total in totals]
        weight_sum = sum(weights)
        if room == 0 or weight_sum == 0:
            return [min(quotas[index], totals[index]) for index in range(len(totals))]
        raw = [room * weight / weight_sum for weight in weights]
        extra = [int(value) for value in raw]
        used = sum(extra)
        remainders = sorted(
            range(len(totals)),
            key=lambda index: (raw[index] - extra[index], totals[index]),
            reverse=True,
        )
        for index in remainders:
            if used >= room:
                break
            if quotas[index] + extra[index] < totals[index]:
                extra[index] += 1
                used += 1
        return [min(totals[index], quotas[index] + extra[index]) for index in range(len(totals))]

    def _add_search_term(self, terms: List[str], seen_terms: Set[str], term: str) -> bool:
        if not term or term in seen_terms:
            return len(terms) < MAX_JAPANESE_SEARCH_TERMS
        if len(terms) >= MAX_JAPANESE_SEARCH_TERMS:
            return False
        seen_terms.add(term)
        terms.append(term)
        return True

    def _search_terms(self, query: str) -> List[str]:
        clean = re.sub(r'[^\w\s\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf]', ' ', query)
        japanese_runs: List[str] = []
        other_words: List[str] = []
        for token in clean.split():
            run_start = 0
            run_is_japanese = self._is_japanese_char(token[0])
            for index, char in enumerate(token[1:], start=1):
                is_japanese = self._is_japanese_char(char)
                if is_japanese != run_is_japanese:
                    run = token[run_start:index]
                    if run_is_japanese:
                        japanese_runs.append(run)
                    elif len(run) >= 2:
                        other_words.append(run)
                    run_start = index
                    run_is_japanese = is_japanese
            run = token[run_start:]
            if run_is_japanese:
                japanese_runs.append(run)
            elif len(run) >= 2:
                other_words.append(run)

        terms: List[str] = []
        seen_terms: Set[str] = set()
        # Keep Latin tokens even when Japanese fragments fill the rest of the budget.
        for word in other_words[:10]:
            if not self._add_search_term(terms, seen_terms, word):
                return terms

        # Keep 5-grams for stronger Japanese matches; shorter fragments are recall fallbacks.
        for size in (5, 4, 3, 2):
            usable = [run for run in japanese_runs if len(run) >= size]
            if not usable or len(terms) >= MAX_JAPANESE_SEARCH_TERMS:
                continue
            totals = [len(run) - size + 1 for run in usable]
            quotas = self._proportional_quotas(totals, MAX_JAPANESE_SEARCH_TERMS - len(terms))
            for run, quota in zip(usable, quotas):
                for start in self._sample_starts(len(run) - size + 1, quota):
                    if not self._add_search_term(terms, seen_terms, run[start:start + size]):
                        return terms
        return terms

    def _sanitize_fts_query(self, query: str) -> str:
        terms = self._search_terms(query)
        if self._contains_japanese(query):
            # Short Japanese fragments are handled by the ranked substring query,
            # not as low-precision alternatives in FTS.
            terms = [
                term for term in terms
                if not self._contains_japanese(term) or len(term) >= 3
            ]
        max_terms = MAX_JAPANESE_SEARCH_TERMS if self._contains_japanese(query) else 10
        terms = terms[:max_terms]
        return " OR ".join(f'"{term}"' for term in terms) if terms else ""

    def _search_japanese_memories(
        self,
        conn: sqlite3.Connection,
        terms: List[str],
        limit: int,
        category: Optional[str],
    ) -> List[sqlite3.Row]:
        """Rank by the rarest useful fragment, then fragment length and recency."""
        if not terms:
            return []

        values = ", ".join("(?)" for _ in terms)
        sql = f"""
            WITH search_terms(term) AS (VALUES {values}),
            matched_terms AS (
                SELECT m.id, m.category, m.content, m.created_at, m.updated_at,
                       m.verified_at, search_terms.term
                FROM memories AS m
                JOIN search_terms
                  ON m.content LIKE '%' || search_terms.term || '%'
                WHERE m.status = 'active'
        """
        params: List[Any] = list(terms)
        if category:
            sql += " AND m.category = ?"
            params.append(category)
        # Two-character grams are a fallback when this memory has no longer match.
        sql += """
            ),
            term_frequencies AS (
                SELECT term, COUNT(*) AS document_frequency
                FROM matched_terms
                GROUP BY term
            ),
            scored_memories AS (
                SELECT matched_terms.id, matched_terms.category,
                       matched_terms.content, matched_terms.created_at,
                       MAX(length(matched_terms.term)) AS match_length,
                       COALESCE(
                           MIN(CASE WHEN length(matched_terms.term) >= 3
                               THEN term_frequencies.document_frequency END),
                           MIN(term_frequencies.document_frequency)
                       ) AS rarest_fragment_frequency,
                       matched_terms.updated_at,
                       MAX(matched_terms.verified_at) AS verified_at
                FROM matched_terms
                JOIN term_frequencies USING (term)
                GROUP BY matched_terms.id
            )
            SELECT id, category, content, created_at, match_length,
                   rarest_fragment_frequency, updated_at, verified_at
            FROM scored_memories
            ORDER BY rarest_fragment_frequency ASC,
                     match_length DESC, updated_at DESC
            LIMIT ?
        """
        params.append(limit)
        return conn.execute(sql, params).fetchall()

    def search_memories(self, query: str, limit: int = 5, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search with FTS5 and merge ranked Japanese substring matches."""
        limit = _bounded_limit(limit, default=5)
        results: List[Dict[str, Any]] = []
        rows = []
        fts_query = self._sanitize_fts_query(query)

        with self._conn() as conn:
            if fts_query:
                sql = """
                    SELECT m.id, m.category, m.content, m.created_at, m.updated_at, m.verified_at,
                           bm25(memories_fts) as rank
                    FROM memories_fts f
                    JOIN memories m ON m.id = f.rowid
                    WHERE memories_fts MATCH ? AND m.status = 'active'
                """
                params: List[Any] = [fts_query]
                if category:
                    sql += " AND m.category = ?"
                    params.append(category)
                sql += " ORDER BY rank LIMIT ?"
                params.append(limit)

                rows = conn.execute(sql, params).fetchall()

            if self._contains_japanese(query):
                fts_rows = rows
                japanese_terms = [
                    term for term in self._search_terms(query)
                    if self._contains_japanese(term)
                ]
                japanese_rows = self._search_japanese_memories(
                    conn, japanese_terms, limit, category
                )
                # Interleave the independently ranked sources so a full
                # Japanese result set cannot crowd out FTS-only (often Latin) hits.
                seen_ids = set()
                rows = []
                for index in range(max(len(japanese_rows), len(fts_rows))):
                    for source_rows in (japanese_rows, fts_rows):
                        if index >= len(source_rows):
                            continue
                        row = source_rows[index]
                        if row["id"] in seen_ids:
                            continue
                        seen_ids.add(row["id"])
                        rows.append(row)
                        if len(rows) >= limit:
                            break
                    if len(rows) >= limit:
                        break
            elif not rows and query.strip():
                keywords = [
                    keyword.strip()
                    for keyword in query.split()
                    if len(keyword.strip()) >= 2
                ][:3]
                if keywords:
                    where = " OR ".join(["m.content LIKE ?" for _ in keywords])
                    sql = f"SELECT id, category, content, created_at, updated_at, verified_at FROM memories m WHERE ({where}) AND m.status = 'active'"
                    params = [f"%{k}%" for k in keywords]
                    if category:
                        sql += " AND m.category = ?"
                        params.append(category)
                    sql += " ORDER BY m.updated_at DESC LIMIT ?"
                    params.append(limit)

                    rows = conn.execute(sql, params).fetchall()

            results = []
            for row in rows:
                freshness = _freshness(row["category"], _known_at(row), row["content"])
                results.append(
                    {
                        "id": row["id"],
                        "category": row["category"],
                        "content": row["content"],
                        "created_at": row["created_at"],
                        "verified_at": freshness["verified_at"],
                        "age_days": freshness["age_days"],
                        "ttl_days": freshness["ttl_days"],
                        "stale": freshness["stale"],
                        "date_pinned": freshness["date_pinned"],
                    }
                )

        return results

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query or len(query.strip()) < MIN_QUERY_LEN:
            self._last_recall_count = 0
            return ""

        self._last_recall_count = 0
        try:
            memories = self.search_memories(query, limit=self._max_recall)
        except sqlite3.Error as e:
            logger.warning("SQLite memory search failed: %s", e)
            raise
        self._last_recall_count = len(memories)
        if not memories:
            return ""

        formatted = "\n".join([
            f"- [ID: {m['id']}] ({m['category']}, {_freshness_label(m)}) {m['content']}"
            for m in memories
        ])
        block = (
            "## Relevant Long-Term Memories (from SQLite)\n"
            f"Age is shown per entry. {STALE_MARKER} = past its freshness window; "
            "verify before relying on it.\n"
            f"{formatted}"
        )
        hint = self._prefetch_review_hint()
        return f"{block}\n{hint}" if hint else block

    def _prefetch_review_hint(self, max_ids: int = 3) -> str:
        """A one-line reminder of which memories are overdue, so the agent need not browse for them.

        Only rows older than two days are scanned: a fresher row can be neither TTL-expired
        nor date-pinned, so the scan stays bounded and cannot flag a fresh write.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, category, content, verified_at, updated_at, created_at FROM memories "
                f"WHERE status = 'active' AND {_KNOWN_AT_SQL} < datetime('now', '-2 days') "
                f"ORDER BY {_KNOWN_AT_SQL} ASC LIMIT 200"
            ).fetchall()

        overdue: List[int] = []
        for row in rows:
            freshness = _freshness(row["category"], _known_at(row), row["content"])
            if _overdue_reasons(freshness):
                overdue.append(int(row["id"]))
        if not overdue:
            return ""

        listed = ", ".join(str(memory_id) for memory_id in overdue[:max_ids])
        extra = f" (+{len(overdue) - max_ids} more)" if len(overdue) > max_ids else ""
        noun = "memory" if len(overdue) == 1 else "memories"
        return (
            f"Review: {len(overdue)} {noun} past their freshness window (ID: {listed}{extra}). "
            "Confirm with sqlite_verify, or replace with sqlite_remember(supersedes=[id])."
        )

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def add_memory(self, content: str, category: str = "general", source: str = "manual",
                   supersedes: Optional[List[int]] = None) -> int:
        """Store a memory and return its ID (see ``_write_memory`` for the full result)."""
        return int(self._write_memory(content, category, source, supersedes)["memory_id"])

    def _write_memory(self, content: str, category: str, source: str,
                      supersedes: Optional[List[int]] = None) -> Dict[str, Any]:
        """Insert a fact, refresh an exact duplicate, and retire the memories it replaces."""
        cleaned_content = content.strip()
        normalized_content = _normalize_memory_content(cleaned_content)
        with self._conn() as conn:
            with conn:
                # Serialize the duplicate check and the insert so two callers cannot
                # both "find nothing" and create the same row twice.
                conn.execute("BEGIN IMMEDIATE")
                memory_id = 0
                created = True
                if normalized_content:
                    # Only collapse restatements from the same writer: a system-sync
                    # mirror must stay 1:1 with the core memory it mirrors.
                    existing_rows = conn.execute(
                        "SELECT id, content FROM memories "
                        "WHERE category = ? AND source = ? AND status = 'active'",
                        (category, source),
                    ).fetchall()
                    for row in existing_rows:
                        if _normalize_memory_content(row["content"]) == normalized_content:
                            memory_id = int(row["id"])
                            created = False
                            break
                if created:
                    cursor = conn.execute(
                        "INSERT INTO memories (category, content, source, session_id, status, verified_at) "
                        "VALUES (?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)",
                        (category, cleaned_content, source, self._session_id),
                    )
                    memory_id = int(cursor.lastrowid or 0)
                else:
                    # Restating a known fact is evidence it is still true.
                    conn.execute(
                        "UPDATE memories SET verified_at = CURRENT_TIMESTAMP, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (memory_id,),
                    )
                superseded = self._mark_superseded(conn, supersedes, memory_id)
        return {
            "memory_id": memory_id,
            "created": created,
            "superseded": superseded["superseded"],
            "not_found": superseded["not_found"],
        }

    @staticmethod
    def _mark_superseded(conn: sqlite3.Connection, supersedes: Optional[List[int]],
                         new_id: int) -> Dict[str, List[int]]:
        """Retire replaced rows without deleting them, and report the ones that were not retired.

        A missing or already-retired ID is reported rather than dropped: silently ignoring it
        would let the caller believe a contradiction was resolved when it was not.
        """
        outcome: Dict[str, List[int]] = {"superseded": [], "not_found": []}
        ids = _coerce_memory_ids(supersedes)
        candidates = [old_id for old_id in ids if old_id != new_id]
        if not candidates:
            return outcome
        placeholders = ", ".join("?" for _ in candidates)
        rows = conn.execute(
            f"SELECT id, status FROM memories WHERE id IN ({placeholders})", candidates
        ).fetchall()
        active_ids = {int(row["id"]) for row in rows if row["status"] == "active"}
        for old_id in candidates:
            if old_id not in active_ids:
                outcome["not_found"].append(old_id)
                continue
            cursor = conn.execute(
                "UPDATE memories SET status = 'superseded', superseded_by = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'active'",
                (new_id, old_id),
            )
            if cursor.rowcount:
                outcome["superseded"].append(old_id)
            else:
                outcome["not_found"].append(old_id)
        return outcome

    def delete_memory(self, memory_id: int) -> bool:
        with self._conn() as conn:
            with conn:
                return conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def list_memories(self, category: Optional[str] = None, limit: int = 10,
                      include_superseded: bool = False) -> List[Dict[str, Any]]:
        limit = _bounded_limit(limit, default=10)
        with self._conn() as conn:
            sql = ("SELECT id, category, content, source, status, created_at, updated_at, verified_at "
                   "FROM memories")
            where: List[str] = []
            params: List[Any] = []
            if category:
                where.append("category = ?")
                params.append(category)
            if not include_superseded:
                where.append("status = 'active'")
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def review_memories(self, days: int = 0, category: Optional[str] = None,
                        limit: int = 50) -> List[Dict[str, Any]]:
        """Active memories whose freshness window has passed, oldest first.

        Rows known to be true within the last two days cannot be overdue (no TTL is shorter
        than 30 days, and a date-pinned claim is only flagged once it ages), so the SQL floor
        bounds the scan the same way the prefetch hint does.
        """
        limit = _bounded_limit(limit, default=50)
        with self._conn() as conn:
            sql = ("SELECT id, category, content, source, status, created_at, updated_at, verified_at "
                   f"FROM memories WHERE status = 'active' AND {_KNOWN_AT_SQL} < datetime('now', '-2 days')")
            params: List[Any] = []
            if category:
                sql += " AND category = ?"
                params.append(category)
            sql += f" ORDER BY {_KNOWN_AT_SQL} ASC"
            rows = conn.execute(sql, params).fetchall()

        flagged: List[Dict[str, Any]] = []
        for row in rows:
            content = row["content"]
            freshness = _freshness(row["category"], _known_at(row), content)
            reasons = _overdue_reasons(freshness, days)
            if not reasons:
                continue
            flagged.append(
                {
                    "id": row["id"],
                    "category": row["category"],
                    "content": content,
                    "age_days": freshness["age_days"],
                    "ttl_days": freshness["ttl_days"],
                    "reason": ", ".join(reasons),
                    "verified_at": freshness["verified_at"],
                }
            )
            if len(flagged) >= limit:
                break
        return flagged

    def verify_memories(self, memory_ids: List[int]) -> int:
        """Reset the freshness clock on active memories that are still true.

        Retired rows are ignored: confirming a fact that a newer one replaced would revive a
        stale claim. ``updated_at`` is left alone so the list ordering only reflects writes.
        """
        ids = _coerce_memory_ids(memory_ids)
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self._conn() as conn:
            with conn:
                cursor = conn.execute(
                    f"UPDATE memories SET verified_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}) AND status = 'active'",
                    ids,
                )
                return int(cursor.rowcount or 0)

    def get_stats(self) -> Dict[str, Any]:
        """Return counts and on-disk sizes for the active provider database."""
        with self._conn() as conn:
            return collect_memory_stats(conn, self._db_path)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Conversation-derived persistence is reserved for the primary agent.
        if self._agent_context != "primary" or not self._auto_extract or not user_content:
            return

        text = user_content.strip()
        if len(text) < 5:
            return

        # Only inspect a leading directive line; searching the whole turn can
        # capture quoted code, examples, or Markdown table text as a memory.
        directive_line = text.splitlines()[0].strip()

        explicit_patterns = [
            r"(?:(?:please\s+)?remember(?:\s+(?:that|to))?(?:\s*[:：,，]\s*|\s+))(.+)",
            r"(?:(?:これ(?:を)?|以下(?:を)?|次|私の好みを?)\s*)?覚えて(?:おいて|ください|ね)?(?:\s*[:：,，、]\s*|\s+)(.+)",
            r"(?:今後(?:は)?|次回から(?:は)?|これからは)\s*(.+(?:にして|を使って|でやって|で答えて(?:ください)?))",
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
        return [REMEMBER_SCHEMA, SEARCH_SCHEMA, FORGET_SCHEMA, LIST_SCHEMA, STATS_SCHEMA,
                REVIEW_SCHEMA, VERIFY_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        try:
            if not isinstance(args, dict):
                return json.dumps({"error": "arguments must be an object"})

            if tool_name == "sqlite_remember":
                content = args.get("content", "")
                if not isinstance(content, str):
                    return json.dumps({"error": "content must be a string"})
                content = content.strip()
                if not content:
                    return json.dumps({"error": "content is required"})
                category = args.get("category", "general")
                if not isinstance(category, str):
                    return json.dumps({"error": "category must be a string"})
                raw_supersedes = args.get("supersedes")
                if raw_supersedes is not None and not isinstance(raw_supersedes, list):
                    return json.dumps({"error": "supersedes must be an array of memory ids"})
                written = self._write_memory(content, category, "tool", raw_supersedes)
                message = (
                    "Fact stored in SQLite memory." if written["created"]
                    else "Existing memory refreshed (the same fact was restated)."
                )
                return json.dumps({"status": "success", **written, "message": message})

            if tool_name == "sqlite_search":
                query = args.get("query", "")
                if not isinstance(query, str):
                    return json.dumps({"error": "query must be a string"})
                query = query.strip()
                if not query:
                    return json.dumps({"error": "query is required"})
                try:
                    results = self.search_memories(query=query, limit=args.get("limit", 5))
                except sqlite3.Error as e:
                    logger.warning("SQLite memory search failed: %s", e)
                    return json.dumps({"error": "SQLite memory search failed"})
                return json.dumps({"status": "success", "count": len(results), "results": results})

            if tool_name == "sqlite_forget":
                if "memory_id" not in args:
                    return json.dumps({"error": "memory_id is required"})
                raw_memory_id = args["memory_id"]
                ids = _coerce_memory_ids([raw_memory_id])
                if not ids:
                    error = (
                        "memory_id must be a positive integer"
                        if isinstance(raw_memory_id, int) and not isinstance(raw_memory_id, bool)
                        else "memory_id must be an integer"
                    )
                    return json.dumps({"error": error})
                mem_id = ids[0]
                deleted = self.delete_memory(mem_id)
                return json.dumps({"status": "success" if deleted else "not_found", "message": f"Memory ID {mem_id} {'deleted' if deleted else 'not found'}."})

            if tool_name == "sqlite_list_memories":
                category = args.get("category")
                if category is not None and not isinstance(category, str):
                    return json.dumps({"error": "category must be a string"})
                include_superseded = bool(args.get("include_superseded", False))
                memories = self.list_memories(
                    category=category,
                    limit=args.get("limit", 10),
                    include_superseded=include_superseded,
                )
                return json.dumps({"status": "success", "count": len(memories), "memories": memories})

            if tool_name == "sqlite_review":
                days = args.get("days", 0)
                if isinstance(days, bool) or not isinstance(days, int):
                    return json.dumps({"error": "days must be an integer"})
                category = args.get("category")
                if category is not None and not isinstance(category, str):
                    return json.dumps({"error": "category must be a string"})
                pending = self.review_memories(
                    days=days,
                    category=category,
                    limit=args.get("limit", 50),
                )
                return json.dumps({
                    "status": "success",
                    "count": len(pending),
                    "needs_review": pending,
                })

            if tool_name == "sqlite_verify":
                raw_ids = args.get("memory_ids")
                if not isinstance(raw_ids, list):
                    return json.dumps({"error": "memory_ids must be an array of integers"})
                verified = self.verify_memories(raw_ids)
                return json.dumps({"status": "success", "verified": verified})

            if tool_name == "sqlite_stats":
                return json.dumps({"status": "success", **self.get_stats()})

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            logger.warning("SQLite memory tool %s failed: %s", tool_name, e)
            return json.dumps({"error": "SQLite memory operation failed"})

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
