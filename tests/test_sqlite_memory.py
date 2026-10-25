"""Unit tests for SQLite Memory Plugin."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Add plugin dir to path
import sys
plugin_dir = Path(__file__).resolve().parent.parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

import __init__ as sqlite_memory_module
from __init__ import SQLiteMemoryProvider


class TestSQLiteMemoryProvider(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test_memory.db"
        # Override default DB path for isolated testing
        self.provider = SQLiteMemoryProvider()
        self.provider._db_path = self.db_path
        self.provider._initialized = False
        self.provider._ensure_db()

    @staticmethod
    def _hermes_config_patch(config, load_calls=None):
        import types
        from unittest.mock import patch

        def cfg_get(data, *keys, default=None):
            value = data
            for key in keys:
                if not isinstance(value, dict) or key not in value:
                    return default
                value = value[key]
            return value

        def load_config():
            if load_calls is not None:
                load_calls.append("load")
            return config

        hermes_cli = types.ModuleType("hermes_cli")
        hermes_cli.__path__ = []
        config_module = types.ModuleType("hermes_cli.config")
        config_module.load_config = load_config
        config_module.cfg_get = cfg_get
        return patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.config": config_module},
        )

    def test_initialize_uses_runtime_profile_home_for_database(self):
        import sqlite3

        env_home = Path(self.tmpdir) / "environment-profile"
        runtime_home = Path(self.tmpdir) / "runtime-profile"
        env_home.mkdir()
        runtime_home.mkdir()
        previous_home = os.environ.get("HERMES_HOME")
        previous_profile = os.environ.get("HERMES_PROFILE")

        try:
            os.environ["HERMES_HOME"] = str(env_home)
            os.environ.pop("HERMES_PROFILE", None)
            provider = SQLiteMemoryProvider()
            provider.initialize(session_id="path-test", hermes_home=str(runtime_home))

            self.assertEqual(provider._db_path, runtime_home / "memory.db")
            self.assertFalse((env_home / "memory.db").exists())

            provider.add_memory("profile routing sentinel", category="test")
            with sqlite3.connect(str(runtime_home / "memory.db")) as conn:
                stored = conn.execute("SELECT content FROM memories").fetchone()
            self.assertEqual(stored, ("profile routing sentinel",))
        finally:
            if previous_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = previous_home
            if previous_profile is None:
                os.environ.pop("HERMES_PROFILE", None)
            else:
                os.environ["HERMES_PROFILE"] = previous_profile

    def test_initialize_resolves_provider_settings_from_one_config_read(self):
        configured_path = Path(self.tmpdir) / "canonical.db"
        legacy_path = Path(self.tmpdir) / "legacy.db"
        config = {
            "memory": {
                "sqlite-memory": {
                    "db_path": str(configured_path),
                    "auto_extract": False,
                    "max_recall": 7,
                },
                "sqlite_memory": {
                    "db_path": str(legacy_path),
                    "auto_extract": True,
                    "max_recall": 9,
                },
            }
        }
        load_calls = []
        with self._hermes_config_patch(config, load_calls):
            provider = SQLiteMemoryProvider()
            provider.initialize(
                session_id="config-test",
                hermes_home=str(Path(self.tmpdir) / "profile"),
            )

        self.assertEqual(load_calls, ["load"])
        self.assertEqual(provider._db_path, configured_path)
        self.assertFalse(provider._auto_extract)
        self.assertEqual(provider._max_recall, 7)

    def test_initialize_invalid_max_recall_does_not_retain_previous_value(self):
        configured_path = Path(self.tmpdir) / "custom.db"
        config = {
            "memory": {
                "sqlite-memory": {
                    "db_path": str(configured_path),
                    "auto_extract": True,
                    "max_recall": "invalid",
                }
            }
        }
        with self._hermes_config_patch(config):
            provider = SQLiteMemoryProvider()
            provider._max_recall = 12
            with self.assertLogs(sqlite_memory_module.logger, level="WARNING"):
                provider.initialize(
                    session_id="invalid-config-test",
                    hermes_home=str(Path(self.tmpdir) / "profile"),
                )

        self.assertEqual(provider._db_path, configured_path)
        self.assertTrue(provider._auto_extract)
        self.assertEqual(provider._max_recall, sqlite_memory_module.MAX_RECALL_RESULTS)

    def test_initialize_clamps_max_recall_to_supported_range(self):
        for configured_value, expected in ((-1, 5), (0, 5), (3, 5), (4, 5), (5, 5), (100, 100), (101, 100)):
            with self.subTest(configured_value=configured_value):
                config = {
                    "memory": {
                        "sqlite-memory": {"max_recall": configured_value}
                    }
                }
                with self._hermes_config_patch(config):
                    provider = SQLiteMemoryProvider()
                    provider.initialize(
                        session_id="bounded-config-test",
                        hermes_home=str(
                            Path(self.tmpdir) / f"profile-{configured_value}"
                        ),
                    )

                self.assertEqual(provider._max_recall, expected)

    def test_ensure_db_raises_when_initialization_fails(self):
        import sqlite3
        from unittest.mock import patch

        provider = SQLiteMemoryProvider()
        provider._db_path = self.db_path
        with patch.object(
            provider, "_get_connection", side_effect=sqlite3.OperationalError("probe failure")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                provider._ensure_db()

        self.assertFalse(provider._initialized)

    def test_shared_schema_initializer_creates_fts_sync_triggers(self):
        import sqlite3
        from memory_schema import ensure_memory_schema

        db_file = Path(self.tmpdir) / "shared_schema.db"
        conn = sqlite3.connect(db_file)
        try:
            self.assertTrue(ensure_memory_schema(conn))
            with conn:
                conn.execute(
                    "INSERT INTO memories (category, content) VALUES (?, ?)",
                    ("test", "shared schema sentinel"),
                )
            match = conn.execute(
                "SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'sentinel'"
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(match)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_config_schema_fallback_matches_memory_setup_contract(self):
        fields = self.provider.get_config_schema()
        fields_by_key = {field["key"]: field for field in fields}

        self.assertEqual(set(fields_by_key), {"db_path", "auto_extract", "max_recall"})
        self.assertEqual(fields_by_key["db_path"]["default"], "")
        self.assertEqual(fields_by_key["auto_extract"]["choices"], ["true", "false"])
        self.assertFalse(fields_by_key["auto_extract"]["secret"])
        self.assertEqual(fields_by_key["max_recall"]["minimum"], 5)
        self.assertEqual(fields_by_key["max_recall"]["maximum"], 100)
        self.assertEqual(fields_by_key["max_recall"]["step"], 1)

    def test_declared_config_schema_leaves_database_path_blank(self):
        import importlib.util
        import types
        from unittest.mock import patch

        plugins_module = types.ModuleType("plugins")
        plugins_module.__path__ = []
        memory_module = types.ModuleType("plugins.memory")
        memory_module.__path__ = []
        schema_api = types.ModuleType("plugins.memory.config_schema")
        schema_api.KIND_BOOL = "bool"
        schema_api.KIND_NUMBER = "number"
        schema_api.KIND_TEXT = "text"
        schema_api.ProviderField = lambda **values: types.SimpleNamespace(**values)
        schema_api.ProviderConfigSchema = lambda **values: types.SimpleNamespace(**values)

        spec = importlib.util.spec_from_file_location(
            "_sqlite_memory_config_schema_test", plugin_dir / "config_schema.py"
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                "plugins": plugins_module,
                "plugins.memory": memory_module,
                "plugins.memory.config_schema": schema_api,
            },
        ):
            spec.loader.exec_module(module)

        fields_by_key = {field.key: field for field in module.CONFIG_SCHEMA.fields}
        self.assertEqual(fields_by_key["db_path"].default, "")
        self.assertIn("leave blank", fields_by_key["db_path"].description.lower())
        self.assertEqual(fields_by_key["max_recall"].minimum, 5)
        self.assertEqual(fields_by_key["max_recall"].maximum, 100)
        self.assertEqual(fields_by_key["max_recall"].step, 1)
        self.assertIn("5–100", fields_by_key["max_recall"].description)

    def test_config_schema_matches_memory_setup_contract(self):
        """Memory setup expects a list of field dictionaries, not the UI schema object."""
        from types import SimpleNamespace

        schema = SimpleNamespace(fields=(
            SimpleNamespace(key="db_path", label="Database Path", description="Path", default="~/memory.db", kind="text", is_secret=False, env_key=None),
            SimpleNamespace(key="auto_extract", label="Auto Extract", description="Extract", default="true", kind="bool", is_secret=False, env_key=None),
            SimpleNamespace(key="max_recall", label="Max Recall", description="Limit", default="5", kind="number", is_secret=False, env_key=None, minimum=5, maximum=100, step=1),
        ))
        fields = sqlite_memory_module._setup_schema_fields(schema)

        self.assertEqual([field["key"] for field in fields], ["db_path", "auto_extract", "max_recall"])
        self.assertEqual(fields[1]["choices"], ["true", "false"])
        self.assertEqual(fields[2]["minimum"], 5)
        self.assertEqual(fields[2]["maximum"], 100)
        self.assertEqual(fields[2]["step"], 1)
        self.assertFalse(any(field.get("secret") for field in fields))

    def test_provider_name_matches_hyphenated_plugin_id(self):
        """Provider identity matches the directory id selected by memory.provider."""
        self.assertEqual(self.provider.name, "sqlite-memory")

    def test_register_passes_inner_schemas_to_tool_registry(self):
        """PluginContext receives bare function schemas, not OpenAI-wrapped schemas."""
        class CaptureContext:
            def __init__(self):
                self.tools = []

            def register_memory_provider(self, provider):
                self.provider = provider

            def register_tool(self, **registration):
                self.tools.append(registration)

        ctx = CaptureContext()
        sqlite_memory_module.register(ctx)

        self.assertEqual(len(ctx.tools), 5)
        self.assertIn("sqlite_stats", {registration["name"] for registration in ctx.tools})
        for registration in ctx.tools:
            schema = registration["schema"]
            self.assertEqual(schema.get("name"), registration["name"])
            self.assertIsInstance(schema.get("parameters"), dict)
            self.assertNotIn("function", schema)
            self.assertNotIn("type", schema)

    def test_database_initialization(self):
        """Should create tables, triggers, and FTS5 virtual table."""
        self.assertTrue(self.db_path.exists())
        with self.provider._conn() as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            self.assertIn("memories", tables)
            self.assertIn("memories_fts", tables)

    def test_backup_paths_only_lists_databases_outside_hermes_backup_root(self):
        """Hermes backs up in-root databases itself and provider paths are external-only."""
        from types import ModuleType
        from unittest.mock import patch

        hermes_root = Path(self.tmpdir) / ".hermes"
        core_constants = ModuleType("hermes_constants")
        core_constants.get_default_hermes_root = lambda: hermes_root
        with patch.dict(sys.modules, {"hermes_constants": core_constants}):
            hermes_home = hermes_root / "profiles" / "coding"
            self.provider._hermes_home = hermes_home
            self.provider._db_path = hermes_home / "memory.db"
            self.assertEqual(self.provider.backup_paths(), [])

            # A database shared at the Hermes root is already included in the root backup.
            self.provider._db_path = hermes_root / "memory.db"
            self.assertEqual(self.provider.backup_paths(), [])

            # A nested HERMES_HOME beneath the default root still uses that root's backup.
            self.provider._hermes_home = hermes_root / "coding"
            self.assertEqual(self.provider.backup_paths(), [])

            external_db = Path(self.tmpdir) / "external" / "shared-memory.db"
            self.provider._db_path = external_db
            self.assertEqual(self.provider.backup_paths(), [str(external_db.resolve())])

            relative_db = Path("relative-shared.db")
            self.provider._db_path = relative_db
            self.assertEqual(self.provider.backup_paths(), [str(relative_db.resolve())])

    def test_add_and_list_memories(self):
        """Should add memories and retrieve them with list_memories."""
        mem_id1 = self.provider.add_memory("User prefers dark mode UI", category="preference")
        mem_id2 = self.provider.add_memory("Project repository is hermes-plugin", category="project")
        self.assertGreater(mem_id1, 0)
        self.assertGreater(mem_id2, 0)

        all_memories = self.provider.list_memories()
        self.assertEqual(len(all_memories), 2)

        # Filter by category
        pref_memories = self.provider.list_memories(category="preference")
        self.assertEqual(len(pref_memories), 1)
        self.assertEqual(pref_memories[0]["content"], "User prefers dark mode UI")

    def test_search_memories_clamps_negative_limit(self):
        self.provider.add_memory("search limit sentinel one")
        self.provider.add_memory("search limit sentinel two")

        self.assertEqual(
            len(self.provider.search_memories("search limit", limit=-1)), 1
        )

    def test_list_memories_clamps_negative_limit(self):
        self.provider.add_memory("list limit sentinel one")
        self.provider.add_memory("list limit sentinel two")

        self.assertEqual(len(self.provider.list_memories(limit=-1)), 1)

    def test_search_memories_caps_large_limit(self):
        for index in range(101):
            self.provider.add_memory(f"search cap marker {index}")

        self.assertEqual(
            len(self.provider.search_memories("search cap marker", limit=10_000)),
            100,
        )

    def test_search_tool_reports_database_failure(self):
        import json

        self.provider.add_memory("search failure sentinel")
        with self.provider._conn() as conn:
            conn.execute("DROP TABLE memories")

        with self.assertLogs(sqlite_memory_module.logger, level="WARNING") as logs:
            result = json.loads(
                self.provider.handle_tool_call(
                    "sqlite_search", {"query": "search failure"}
                )
            )

        self.assertEqual(result, {"error": "SQLite memory search failed"})
        self.assertTrue(any("SQLite memory search failed" in line for line in logs.output))

    def test_prefetch_search_failure_resets_recall_status(self):
        import sqlite3

        self.provider._last_recall_count = 4
        with self.provider._conn() as conn:
            conn.execute("DROP TABLE memories")

        with self.assertLogs(sqlite_memory_module.logger, level="WARNING"):
            with self.assertRaises(sqlite3.OperationalError):
                self.provider.prefetch("search failure")

        self.assertIsNone(self.provider.recall_status())

    def test_prefetch_without_matches_has_no_recall_status(self):
        self.assertEqual(self.provider.prefetch("no matching memory"), "")

        self.assertIsNone(self.provider.recall_status())

    def test_fts5_search(self):
        """Should perform full-text search via FTS5 and return ranked results."""
        self.provider.add_memory("The quick brown fox jumps over the lazy dog", category="general")
        self.provider.add_memory("Hermes Agent supports autonomous skill evolution", category="feature")
        self.provider.add_memory("SQLite memory uses zero dependencies and stdlib only", category="architecture")

        # Search for Hermes Agent
        results = self.provider.search_memories("Hermes Agent")
        self.assertTrue(len(results) >= 1)
        self.assertIn("autonomous skill evolution", results[0]["content"])

        # Search with category filter
        arch_results = self.provider.search_memories("dependencies", category="architecture")
        self.assertEqual(len(arch_results), 1)
        self.assertIn("zero dependencies", arch_results[0]["content"])

    def test_fts_and_like_search_return_the_same_memory_fields(self):
        memory_id = self.provider.add_memory(
            "The deployment uses canary releases", category="project"
        )

        fts_results = self.provider.search_memories("deployment")
        like_results = self.provider.search_memories("deploy")
        expected_fields = {"id", "category", "content", "created_at"}

        self.assertEqual(len(fts_results), 1)
        self.assertEqual(len(like_results), 1)
        self.assertEqual(set(fts_results[0]), expected_fields)
        self.assertEqual(set(like_results[0]), expected_fields)
        self.assertEqual(fts_results[0]["id"], memory_id)
        self.assertEqual(like_results[0]["id"], memory_id)

    def test_delete_memory(self):
        """Should remove memory from main table and synchronize with FTS5 table."""
        mem_id = self.provider.add_memory("Temporary secret configuration to delete", category="temp")
        self.assertTrue(self.provider.delete_memory(mem_id))

        # Check main table
        memories = self.provider.list_memories()
        self.assertEqual(len(memories), 0)

        # Check FTS5 table
        search_res = self.provider.search_memories("Temporary secret")
        self.assertEqual(len(search_res), 0)

    def test_prefetch_formatting(self):
        """Should format relevant memories into system prompt markdown block."""
        self.provider.add_memory("Always reply in Japanese", category="preference")
        block = self.provider.prefetch("Japanese reply")
        self.assertIn("## Relevant Long-Term Memories (from SQLite)", block)
        self.assertIn("Always reply in Japanese", block)

    def test_auto_extract_preference(self):
        """Should automatically extract explicit rules from conversation turns."""
        self.provider.sync_turn("Please remember that my favorite color is navy blue", "Understood.")
        memories = self.provider.list_memories(category="preference")
        self.assertEqual(len(memories), 1)
        self.assertIn("my favorite color is navy blue", memories[0]["content"])

        # Japanese extraction
        self.provider.sync_turn("私の好みを覚えておいて：ダークテーマを優先すること", "承知いたしました。")
        all_pref = self.provider.list_memories(category="preference")
        self.assertEqual(len(all_pref), 2)

    def test_auto_extract_ignores_incidental_remember_in_markdown_table(self):
        """A `remember` mention in pasted table text is not a memory request."""
        message = "| `sqlite_update` | current tools only have delete+remember しかない） |"
        self.provider.sync_turn(message, "")
        self.assertEqual(self.provider.list_memories(category="preference"), [])

    def test_auto_extract_ignores_preference_regex_in_code_block(self):
        """A regex example containing trigger phrases is not a memory request."""
        message = (
            "```python\n"
            r'r"(?:(?:今後|次回から|これからは)\s*(.+)(?:にして|を使って|でやって))"'
            "\n```"
        )
        self.provider.sync_turn(message, "")
        self.assertEqual(self.provider.list_memories(category="preference"), [])

    def test_auto_extract_does_not_relearn_fragment_from_memory_complaint(self):
        """Quoting the broken memory in a report must not create another copy."""
        message = (
            r"メモリの内容が しかない） |  |次回から|これからは)\s*(.+)"
            r"(?:にして|を使って| など、壊れた感じになっているので問題かどうか確認して"
        )
        self.provider.sync_turn(message, "")
        self.assertEqual(self.provider.list_memories(category="preference"), [])

    def test_on_memory_write_remove_deletes_only_mirrored_row(self):
        """Removing a built-in memory deletes its SQLite mirror, not an independent row."""
        previous_content = "Keep the deployment window on Tuesdays"
        mirrored_id = self.provider.add_memory(
            previous_content, category="general", source="system_sync"
        )
        independent_id = self.provider.add_memory(
            previous_content, category="general", source="manual"
        )

        self.provider.on_memory_write(
            "remove", "memory", "",
            metadata={"previous_content": previous_content},
        )

        with self.provider._conn() as conn:
            rows = conn.execute(
                "SELECT id, source FROM memories WHERE content = ? ORDER BY id",
                (previous_content,),
            ).fetchall()
        self.assertEqual([(row["id"], row["source"]) for row in rows], [(independent_id, "manual")])
        self.assertNotEqual(mirrored_id, independent_id)

    def test_on_memory_write_replace_updates_mirror_in_place(self):
        """Replacing a built-in memory preserves the mirrored row ID and unrelated row."""
        previous_content = "The release branch is named alpha"
        mirrored_id = self.provider.add_memory(
            previous_content, category="general", source="system_sync"
        )
        independent_id = self.provider.add_memory(
            previous_content, category="general", source="manual"
        )

        self.provider.on_memory_write(
            "replace", "memory", "The release branch is named omega",
            metadata={"previous_content": previous_content},
        )

        with self.provider._conn() as conn:
            rows = conn.execute(
                "SELECT id, content, source FROM memories ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [(row["id"], row["content"], row["source"]) for row in rows],
            [
                (mirrored_id, "The release branch is named omega", "system_sync"),
                (independent_id, previous_content, "manual"),
            ],
        )
        self.assertEqual(
            [row["id"] for row in self.provider.search_memories("alpha")],
            [independent_id],
        )
        self.assertEqual(
            [row["id"] for row in self.provider.search_memories("omega")],
            [mirrored_id],
        )

    def test_on_memory_write_replace_matches_normalized_previous_content(self):
        """Replacement matching tolerates Unicode width and whitespace differences."""
        mirrored_id = self.provider.add_memory(
            "Ｐｒｏｊｅｃｔ：  use\tPython", category="general", source="system_sync"
        )

        self.provider.on_memory_write(
            "replace", "memory", "Project now uses Go",
            metadata={"previous_content": "Project: use Python"},
        )

        with self.provider._conn() as conn:
            row = conn.execute(
                "SELECT id, content FROM memories WHERE id = ?", (mirrored_id,)
            ).fetchone()
        self.assertEqual((row["id"], row["content"]), (mirrored_id, "Project now uses Go"))

    def test_on_memory_write_ambiguous_normalized_match_is_ignored(self):
        """A lossy fallback match must not edit multiple distinct mirrored entries."""
        first_content = "Fact:  use Python"
        second_content = "Fact: use  Python"
        first_id = self.provider.add_memory(first_content, source="system_sync")
        second_id = self.provider.add_memory(second_content, source="system_sync")

        with self.assertLogs(sqlite_memory_module.logger, level="WARNING"):
            self.provider.on_memory_write(
                "replace", "memory", "Use Go",
                metadata={"previous_content": "Fact: use Python"},
            )

        with self.provider._conn() as conn:
            rows = conn.execute(
                "SELECT id, content FROM memories WHERE id IN (?, ?) ORDER BY id",
                (first_id, second_id),
            ).fetchall()
        self.assertEqual(
            [(row["id"], row["content"]) for row in rows],
            [(first_id, first_content), (second_id, second_content)],
        )

    def test_on_memory_write_replace_without_previous_content_does_not_insert(self):
        """A legacy replace without authoritative identity must not create a new row."""
        self.provider.on_memory_write("replace", "memory", "New native memory content")

        self.assertEqual(self.provider.list_memories(), [])

    def test_on_memory_write_remove_is_scoped_to_target_category(self):
        """Removing a memory mirror leaves an identical user-target mirror intact."""
        content = "Shared text in two native memory scopes"
        memory_id = self.provider.add_memory(content, category="general", source="system_sync")
        user_id = self.provider.add_memory(content, category="user", source="system_sync")

        self.provider.on_memory_write(
            "remove", "memory", "", metadata={"previous_content": content}
        )

        with self.provider._conn() as conn:
            rows = conn.execute(
                "SELECT id, category FROM memories ORDER BY id"
            ).fetchall()
        self.assertEqual([(row["id"], row["category"]) for row in rows], [(user_id, "user")])
        self.assertNotEqual(memory_id, user_id)

    def test_session_switch_rebinds_session_id(self):
        """Subsequent provider writes are attributed to the switched session."""
        self.provider.on_session_switch(
            "session-after", parent_session_id="session-before", reset=True,
            reason="new_session",
        )
        memory_id = self.provider.add_memory("Session-specific fact")

        with self.provider._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        self.assertEqual(row["session_id"], "session-after")

    def test_handle_tool_call(self):
        """Should dispatch tool calls to corresponding provider methods."""
        # Remember
        res_rem = self.provider.handle_tool_call("sqlite_remember", {"content": "Use port 8080 for web", "category": "config"})
        self.assertIn("Fact stored", res_rem)

        # Search
        res_search = self.provider.handle_tool_call("sqlite_search", {"query": "port 8080"})
        self.assertIn("Use port 8080 for web", res_search)

        # List
        res_list = self.provider.handle_tool_call("sqlite_list_memories", {"category": "config"})
        self.assertIn("port 8080", res_list)

    def test_tool_rejects_non_string_remember_content(self):
        import json

        result = json.loads(
            self.provider.handle_tool_call("sqlite_remember", {"content": None})
        )

        self.assertEqual(result, {"error": "content must be a string"})

    def test_tool_rejects_non_string_search_query(self):
        import json

        result = json.loads(
            self.provider.handle_tool_call("sqlite_search", {"query": None})
        )

        self.assertEqual(result, {"error": "query must be a string"})

    def test_tool_rejects_non_integer_limit(self):
        import json

        result = json.loads(
            self.provider.handle_tool_call(
                "sqlite_search", {"query": "a valid query", "limit": "all"}
            )
        )

        self.assertEqual(result, {"error": "limit must be an integer"})

    def test_tool_rejects_non_integer_memory_id(self):
        import json

        result = json.loads(
            self.provider.handle_tool_call(
                "sqlite_forget", {"memory_id": "not-an-id"}
            )
        )

        self.assertEqual(result, {"error": "memory_id must be an integer"})

    def test_tool_distinguishes_missing_and_non_positive_memory_ids(self):
        import json

        missing = json.loads(
            self.provider.handle_tool_call("sqlite_forget", {})
        )
        self.assertEqual(missing, {"error": "memory_id is required"})

        for value in (0, -1):
            with self.subTest(memory_id=value):
                result = json.loads(
                    self.provider.handle_tool_call(
                        "sqlite_forget", {"memory_id": value}
                    )
                )
                self.assertEqual(
                    result, {"error": "memory_id must be a positive integer"}
                )

    def test_tool_rejects_non_string_category(self):
        import json

        for tool_name, args in (
            ("sqlite_remember", {"content": "valid content", "category": 123}),
            ("sqlite_list_memories", {"category": 123}),
        ):
            with self.subTest(tool=tool_name):
                result = json.loads(self.provider.handle_tool_call(tool_name, args))
                self.assertEqual(result, {"error": "category must be a string"})

    def test_sqlite_stats_tool_returns_metrics(self):
        import json

        self.provider.add_memory("stats sentinel", category="project")
        result = json.loads(self.provider.handle_tool_call("sqlite_stats", {}))

        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result["total_memories"], 1)
        self.assertEqual(result["categories"], {"project": 1})
        self.assertTrue(result["last_updated_at"])
        self.assertGreater(result["db_size_bytes"], 0)
        self.assertIsInstance(result["wal_size_bytes"], int)
        self.assertNotIn("db_path", result)


class TestDashboardSecurityAndIntegrity(unittest.TestCase):
    """Tests for dashboard API security, path traversal prevention, and FTS sync."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_path_traversal_prevention(self):
        """Should reject path traversal attempts and invalid profile names."""
        from dashboard.plugin_api import _validate_profile, HTTPException

        invalid_profiles = [
            "../escape",
            "../../etc/passwd",
            "/tmp/external",
            "profile;rm -rf",
            "foo/bar",
            "test\\profile",
            "profile name with space",
            "profile*wildcard",
        ]
        for prof in invalid_profiles:
            with self.subTest(profile=prof):
                with self.assertRaises(HTTPException) as ctx:
                    _validate_profile(prof)
                self.assertEqual(ctx.exception.status_code, 400)

        # Valid profile names should pass
        valid_profiles = ["assistant", "coding-bot", "research_agent_01", "default", "main"]
        for prof in valid_profiles:
            with self.subTest(profile=prof):
                self.assertEqual(_validate_profile(prof), prof)

        # None or empty string should return empty string
        self.assertEqual(_validate_profile(None), "")
        self.assertEqual(_validate_profile(""), "")

    def test_fts_sync_triggers_and_rebuild(self):
        """Should keep FTS5 synchronized on insert/update/delete, and auto-rebuild if out of sync."""
        import sqlite3
        from dashboard.plugin_api import _ensure_db_schema

        db_file = Path(self.tmpdir) / "test_fts_sync.db"
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row

        # 1. Initialize schema and triggers
        _ensure_db_schema(conn)

        # 2. Insert into memories and verify FTS trigger works
        with conn:
            conn.execute(
                "INSERT INTO memories (category, content, source) VALUES ('preference', 'Always use TypeScript in frontend', 'desktop_ui')"
            )
        fts_match = conn.execute("SELECT * FROM memories_fts WHERE memories_fts MATCH 'TypeScript'").fetchone()
        self.assertIsNotNone(fts_match)

        # 3. Update memory and verify FTS trigger updates
        mem_id = conn.execute("SELECT id FROM memories WHERE content LIKE '%TypeScript%'").fetchone()["id"]
        with conn:
            conn.execute(
                "UPDATE memories SET content = 'Always use Vanilla JS in frontend' WHERE id = ?",
                (mem_id,)
            )
        # Old content should NOT match
        old_match = conn.execute("SELECT * FROM memories_fts WHERE memories_fts MATCH 'TypeScript'").fetchone()
        self.assertIsNone(old_match)
        # New content should match
        new_match = conn.execute("SELECT * FROM memories_fts WHERE memories_fts MATCH 'Vanilla'").fetchone()
        self.assertIsNotNone(new_match)

        # 4. Delete memory and verify FTS trigger cleans up
        with conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        del_match = conn.execute("SELECT * FROM memories_fts WHERE memories_fts MATCH 'Vanilla'").fetchone()
        self.assertIsNone(del_match)

        # 5. Out-of-sync auto-rebuild test
        # Manually insert directly with triggers disabled or populate memories before FTS
        db_rebuild = Path(self.tmpdir) / "test_rebuild.db"
        conn2 = sqlite3.connect(str(db_rebuild))
        with conn2:
            conn2.execute("""
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT DEFAULT 'general',
                    content TEXT NOT NULL,
                    source TEXT DEFAULT 'manual',
                    session_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn2.execute("INSERT INTO memories (category, content) VALUES ('rule', 'Unindexed legacy memory content')")

        # Now run _ensure_db_schema which should create FTS and triggers, detect mismatch (1 vs 0), and rebuild
        _ensure_db_schema(conn2)

        rebuild_match = conn2.execute("SELECT * FROM memories_fts WHERE memories_fts MATCH 'legacy'").fetchone()
        self.assertIsNotNone(rebuild_match)
        conn.close()
        conn2.close()

    def test_schema_cache_reinitializes_database_recreated_at_same_path(self):
        """A replaced DB file at a cached path must receive the schema again."""
        import sqlite3
        from dashboard.plugin_api import _ensure_db_schema

        db_file = Path(self.tmpdir) / "recreated.db"
        conn = sqlite3.connect(str(db_file))
        _ensure_db_schema(conn, db_file)
        conn.close()

        db_file.unlink()
        conn = sqlite3.connect(str(db_file))
        _ensure_db_schema(conn, db_file)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()

        self.assertIn("memories", tables)

    def test_config_db_path_resolution(self):
        """Should resolve db_path from both sqlite_memory and sqlite-memory notations."""
        from dashboard.plugin_api import _read_config_db_path

        # Case 1: sqlite_memory (underscore)
        cfg1 = Path(self.tmpdir) / "config1.yaml"
        cfg1.write_text("""
memory:
  sqlite_memory:
    db_path: "~/custom_underscore.db"
""", encoding="utf-8")
        resolved1 = _read_config_db_path(cfg1)
        self.assertIsNotNone(resolved1)
        self.assertTrue(str(resolved1).endswith("custom_underscore.db"))

        # Case 2: sqlite-memory (hyphen)
        cfg2 = Path(self.tmpdir) / "config2.yaml"
        cfg2.write_text("""
memory:
  sqlite-memory:
    db_path: "~/custom_hyphen.db"
""", encoding="utf-8")
        resolved2 = _read_config_db_path(cfg2)
        self.assertIsNotNone(resolved2)
        self.assertTrue(str(resolved2).endswith("custom_hyphen.db"))

        # Case 3: sqlite_memory section is null / empty
        cfg3 = Path(self.tmpdir) / "config3.yaml"
        cfg3.write_text("""
memory:
  sqlite_memory:
""", encoding="utf-8")
        resolved3 = _read_config_db_path(cfg3)
        self.assertIsNone(resolved3)

    def test_config_db_path_falls_back_between_notations(self):
        """A section without db_path must not hide db_path under the other supported name."""
        import sys
        import types
        from unittest.mock import patch
        from dashboard.plugin_api import _read_config_db_path

        cfg = Path(self.tmpdir) / "both_notations.yaml"
        cfg.write_text("fixture", encoding="utf-8")
        yaml_stub = types.ModuleType("yaml")
        yaml_stub.safe_load = lambda _: {
            "memory": {
                "sqlite_memory": {"auto_extract": True},
                "sqlite-memory": {"db_path": "/tmp/custom_hyphen.db"},
            }
        }

        with patch.dict(sys.modules, {"yaml": yaml_stub}):
            resolved = _read_config_db_path(cfg)

        self.assertEqual(resolved, Path("/tmp/custom_hyphen.db"))

    def test_config_db_path_prefers_hyphen_when_both_notations_are_present(self):
        """The canonical hyphenated setting must win when both keys define a path."""
        import sys
        import types
        from unittest.mock import patch
        from dashboard.plugin_api import _read_config_db_path

        cfg = Path(self.tmpdir) / "conflicting_paths.yaml"
        cfg.write_text("fixture", encoding="utf-8")
        yaml_stub = types.ModuleType("yaml")
        yaml_stub.safe_load = lambda _: {
            "memory": {
                "sqlite_memory": {"db_path": "/tmp/underscore.db"},
                "sqlite-memory": {"db_path": "/tmp/hyphen.db"},
            }
        }

        with patch.dict(sys.modules, {"yaml": yaml_stub}):
            resolved = _read_config_db_path(cfg)

        self.assertEqual(resolved, Path("/tmp/hyphen.db"))

    def test_config_db_path_prefers_hyphen_without_yaml_dependency(self):
        """The zero-dependency parser must apply the same key precedence as YAML parsing."""
        import sys
        from unittest.mock import patch
        from dashboard.plugin_api import _read_config_db_path

        cfg = Path(self.tmpdir) / "conflicting_paths_without_yaml.yaml"
        cfg.write_text("""
memory:
  sqlite_memory:
    db_path: "/tmp/underscore.db"
  sqlite-memory:
    db_path: "/tmp/hyphen.db"
""", encoding="utf-8")

        with patch.dict(sys.modules, {"yaml": None}):
            resolved = _read_config_db_path(cfg)

        self.assertEqual(resolved, Path("/tmp/hyphen.db"))

    def test_hermes_root_resolution_custom_home(self):
        """Should respect HERMES_HOME even when it does not contain 'profiles'."""
        from dashboard.plugin_api import _get_hermes_root

        old_home = os.environ.get("HERMES_HOME")
        try:
            # Use a path whose parents do not include "profiles"; TMPDIR may itself
            # be inside a named Hermes profile during runtime validation.
            custom_dir = Path(__file__).resolve().parent / "isolated_hermes"
            os.environ["HERMES_HOME"] = str(custom_dir)
            root = _get_hermes_root()
            self.assertEqual(root, custom_dir)
        finally:
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home

    def test_auto_detected_environment_profile_rejects_path_traversal(self):
        """An environment-selected profile must not escape the Hermes root."""
        from dashboard.plugin_api import _get_db_path, HTTPException

        old_values = {key: os.environ.get(key) for key in ("HOME", "HERMES_HOME", "HERMES_PROFILE")}
        try:
            home = Path(self.tmpdir) / "home"
            root = home / ".hermes"
            (root / "profiles").mkdir(parents=True)
            (home / "outside").mkdir()
            os.environ["HOME"] = str(home)
            os.environ.pop("HERMES_HOME", None)
            os.environ["HERMES_PROFILE"] = "../../outside"

            with self.assertRaises(HTTPException) as ctx:
                _get_db_path()
            self.assertEqual(ctx.exception.status_code, 400)
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_active_profile_file_rejects_paths_outside_hermes_root(self):
        """A sticky active profile must not escape the Hermes root by traversal or symlink."""
        from dashboard.plugin_api import _get_db_path, HTTPException

        old_values = {key: os.environ.get(key) for key in ("HOME", "HERMES_HOME", "HERMES_PROFILE")}
        try:
            home = Path(self.tmpdir) / "home"
            root = home / ".hermes"
            profiles = root / "profiles"
            profiles.mkdir(parents=True)
            outside = home / "outside"
            outside.mkdir()
            (profiles / "outside_link").symlink_to(outside, target_is_directory=True)
            os.environ["HOME"] = str(home)
            os.environ.pop("HERMES_HOME", None)
            os.environ.pop("HERMES_PROFILE", None)

            for profile_name in ("../../outside", "outside_link"):
                with self.subTest(profile=profile_name):
                    (root / "active_profile").write_text(profile_name, encoding="utf-8")
                    with self.assertRaises(HTTPException) as ctx:
                        _get_db_path()
                    self.assertEqual(ctx.exception.status_code, 400)
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_auto_detected_database_symlink_outside_root_is_rejected(self):
        """An auto-detected profile database symlink must stay inside the Hermes root."""
        from dashboard.plugin_api import _get_db_path, HTTPException

        old_values = {key: os.environ.get(key) for key in ("HOME", "HERMES_HOME", "HERMES_PROFILE")}
        try:
            home = Path(self.tmpdir) / "home"
            root = home / ".hermes"
            profiles = root / "profiles"
            profile_dir = profiles / "linked"
            profile_dir.mkdir(parents=True)
            outside_db = home / "outside.db"
            outside_db.touch()
            (profile_dir / "memory.db").symlink_to(outside_db)
            os.environ["HOME"] = str(home)
            os.environ.pop("HERMES_HOME", None)

            for source in ("environment", "active_profile"):
                with self.subTest(source=source):
                    if source == "environment":
                        os.environ["HERMES_PROFILE"] = "linked"
                    else:
                        os.environ.pop("HERMES_PROFILE", None)
                        (root / "active_profile").write_text("linked", encoding="utf-8")
                    with self.assertRaises(HTTPException) as ctx:
                        _get_db_path()
                    self.assertEqual(ctx.exception.status_code, 400)
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_auto_detected_root_profile_aliases_use_root_database(self):
        """Auto-detected root aliases must match explicit profile resolution."""
        from dashboard.plugin_api import _get_db_path

        old_values = {key: os.environ.get(key) for key in ("HOME", "HERMES_HOME", "HERMES_PROFILE")}
        try:
            home = Path(self.tmpdir) / "home"
            root = home / ".hermes"
            profiles = root / "profiles"
            profiles.mkdir(parents=True)
            for name in ("default", "main", "root"):
                (profiles / name).mkdir()
            os.environ["HOME"] = str(home)
            os.environ.pop("HERMES_HOME", None)
            active_file = root / "active_profile"

            for profile_name in ("default", "main", "root", "~/.hermes"):
                with self.subTest(source="environment", profile=profile_name):
                    os.environ["HERMES_PROFILE"] = profile_name
                    self.assertEqual(_get_db_path(), root / "memory.db")

                with self.subTest(source="active_profile", profile=profile_name):
                    os.environ.pop("HERMES_PROFILE", None)
                    active_file.write_text(profile_name, encoding="utf-8")
                    self.assertEqual(_get_db_path(), root / "memory.db")
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_validate_profile_non_string_safety(self):
        """Should safely handle non-string or Query default objects without AttributeError."""
        from dashboard.plugin_api import _validate_profile, Query

        self.assertEqual(_validate_profile(Query(None)), "")
        self.assertEqual(_validate_profile(None), "")
        self.assertEqual(_validate_profile(123), "")  # type: ignore

    def test_create_memory_invalid_profile_returns_400(self):
        """Should raise HTTPException with 400, not 500, when invalid profile is passed to create_memory."""
        import asyncio
        from dashboard.plugin_api import create_memory, MemoryCreateRequest, HTTPException

        async def run():
            with self.assertRaises(HTTPException) as ctx:
                await create_memory(MemoryCreateRequest(content="hello"), profile="../evil")
            self.assertEqual(ctx.exception.status_code, 400)

        asyncio.run(run())

    def test_list_memories_invalid_profile_returns_400(self):
        """Should not turn an invalid profile error into an empty successful response."""
        import asyncio
        from dashboard.plugin_api import list_memories, HTTPException

        async def run():
            with self.assertRaises(HTTPException) as ctx:
                await list_memories(profile="../evil")
            self.assertEqual(ctx.exception.status_code, 400)

        asyncio.run(run())

    def test_update_memory_empty_content_returns_400(self):
        """Should reject empty or whitespace-only content with 400 on update."""
        import asyncio
        from dashboard.plugin_api import create_memory, update_memory, MemoryCreateRequest, MemoryUpdateRequest, HTTPException

        async def run():
            old_home = os.environ.get("HERMES_HOME")
            try:
                os.environ["HERMES_HOME"] = str(self.tmpdir)
                m = await create_memory(MemoryCreateRequest(content="initial content"), profile="default")
                with self.assertRaises(HTTPException) as ctx:
                    await update_memory(m["id"], MemoryUpdateRequest(content="   "), profile="default")
                self.assertEqual(ctx.exception.status_code, 400)
            finally:
                if old_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_home

        asyncio.run(run())

    def test_stats_endpoint_returns_requested_metrics(self):
        import asyncio
        from dashboard.plugin_api import (
            MemoryCreateRequest,
            create_memory,
            get_stats,
        )

        old_home = os.environ.get("HERMES_HOME")
        hermes_root = Path(self.tmpdir) / ".hermes"
        hermes_root.mkdir()
        os.environ["HERMES_HOME"] = str(hermes_root)

        async def run():
            empty = await get_stats(profile="default")
            await create_memory(
                MemoryCreateRequest(content="stats sentinel", category="project"),
                profile="default",
            )
            populated = await get_stats(profile="default")
            return empty, populated

        try:
            empty, populated = asyncio.run(run())
        finally:
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home

        self.assertEqual(empty["total_memories"], 0)
        self.assertEqual(empty["categories"], {})
        self.assertIsNone(empty["last_updated_at"])
        self.assertGreaterEqual(empty["wal_size_bytes"], 0)

        self.assertEqual(populated["total_memories"], 1)
        self.assertEqual(populated["categories"], {"project": 1})
        self.assertTrue(populated["last_updated_at"])
        self.assertGreater(populated["db_size_bytes"], 0)
        self.assertIsInstance(populated["wal_size_bytes"], int)
        for key in ("db_path", "profile", "available_profiles"):
            self.assertIn(key, populated)

    def test_stats_endpoint_reports_version_from_loaded_plugin_manifest(self):
        import asyncio
        from contextlib import nullcontext
        from unittest.mock import patch
        from dashboard import plugin_api

        package_root = Path(self.tmpdir) / "profile_plugin"
        dashboard_dir = package_root / "dashboard"
        dashboard_dir.mkdir(parents=True)
        plugin_api_file = dashboard_dir / "plugin_api.py"
        plugin_api_file.write_text("", encoding="utf-8")
        (package_root / "plugin.yaml").write_text(
            "name: sqlite-memory\nversion: 9.8.7\n",
            encoding="utf-8",
        )

        with patch.object(plugin_api, "__file__", str(plugin_api_file)), \
                patch.object(plugin_api, "_get_db_path", return_value=package_root / "memory.db"), \
                patch.object(plugin_api, "_db_conn", return_value=nullcontext(object())), \
                patch.object(plugin_api, "collect_memory_stats", return_value={"total_memories": 0}), \
                patch.object(plugin_api, "_get_available_profiles", return_value=["default"]):
            stats = asyncio.run(plugin_api.get_stats(profile="default"))

        self.assertEqual(stats.get("plugin_version"), "9.8.7")

    def test_memory_mutation_failures_do_not_expose_database_errors(self):
        import asyncio
        import sqlite3
        from unittest.mock import patch
        from dashboard.plugin_api import (
            HTTPException,
            MemoryCreateRequest,
            MemoryUpdateRequest,
            create_memory,
            delete_memory,
            update_memory,
        )

        internal_error = "/private/profile/memory.db: locked"
        cases = (
            ("create", lambda: create_memory(MemoryCreateRequest(content="sentinel")), "Failed to create memory"),
            ("update", lambda: update_memory(1, MemoryUpdateRequest(content="updated")), "Failed to update memory"),
            ("delete", lambda: delete_memory(1), "Failed to delete memory"),
        )
        for name, invoke, expected_detail in cases:
            with self.subTest(operation=name):
                with patch(
                    "dashboard.plugin_api._db_conn",
                    side_effect=sqlite3.OperationalError(internal_error),
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        asyncio.run(invoke())

                self.assertEqual(ctx.exception.status_code, 500)
                self.assertEqual(ctx.exception.detail, expected_detail)
                self.assertNotIn(internal_error, ctx.exception.detail)

    def test_stats_endpoint_returns_500_when_database_read_fails(self):
        import asyncio
        import sqlite3
        from unittest.mock import patch
        from dashboard.plugin_api import HTTPException, get_stats

        db_path = Path(self.tmpdir) / "unreadable.db"
        with patch("dashboard.plugin_api._get_db_path", return_value=db_path):
            with patch(
                "dashboard.plugin_api._db_conn",
                side_effect=sqlite3.OperationalError("probe failure"),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(get_stats(profile="default"))

        self.assertEqual(ctx.exception.status_code, 500)

    def test_plugin_api_standalone_import_resolves_dependencies(self):
        """Web server dashboard loads plugin_api via spec_from_file_location without parent package."""
        import importlib.util
        from unittest.mock import patch

        api_file = plugin_dir / "dashboard" / "plugin_api.py"
        spec = importlib.util.spec_from_file_location("standalone_dashboard_plugin_api_test", api_file)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mod = importlib.util.module_from_spec(spec)
        # Verify that even when neither ..memory_stats nor memory_stats is on sys.path,
        # plugin_api resolves them via its parent directory.
        filtered_path = [p for p in sys.path if str(plugin_dir) not in p]
        with patch.object(sys, "path", filtered_path):
            spec.loader.exec_module(mod)
        self.assertTrue(hasattr(mod, "router"))
        self.assertGreater(len(mod.router.routes), 0)


if __name__ == "__main__":
    unittest.main()
