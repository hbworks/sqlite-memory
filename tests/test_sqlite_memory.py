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


if __name__ == "__main__":
    unittest.main()
