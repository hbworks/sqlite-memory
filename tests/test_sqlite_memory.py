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

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_database_initialization(self):
        """Should create tables, triggers, and FTS5 virtual table."""
        self.assertTrue(self.db_path.exists())
        with self.provider._conn() as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            self.assertIn("memories", tables)
            self.assertIn("memories_fts", tables)

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


if __name__ == "__main__":
    unittest.main()
