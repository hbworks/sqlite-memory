"""Freshness lifecycle tests: migration, supersede, review and verification."""

import datetime
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

plugin_dir = Path(__file__).resolve().parent.parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

import __init__ as sqlite_memory_module  # noqa: E402
from __init__ import SQLiteMemoryProvider  # noqa: E402
from memory_schema import ensure_memory_schema  # noqa: E402


def _days_ago(days: int) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


class TestFreshnessLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "freshness.db"
        self.provider = SQLiteMemoryProvider()
        self.provider._db_path = self.db_path
        self.provider._initialized = False
        self.provider._ensure_db()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _age(self, memory_id: int, days: int) -> None:
        with self.provider._conn() as conn:
            with conn:
                conn.execute(
                    "UPDATE memories SET verified_at = ?, created_at = ?, updated_at = ? WHERE id = ?",
                    (_days_ago(days), _days_ago(days), _days_ago(days), memory_id),
                )

    def _row(self, memory_id: int):
        with self.provider._conn() as conn:
            return conn.execute(
                "SELECT id, status, superseded_by, verified_at FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()

    # --- migration ---

    def test_legacy_database_gains_lifecycle_columns_and_backfill(self):
        legacy_path = Path(self.tmpdir) / "legacy.db"
        with sqlite3.connect(str(legacy_path)) as conn:
            conn.execute("""
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT DEFAULT 'general',
                    content TEXT NOT NULL,
                    source TEXT DEFAULT 'manual',
                    session_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO memories (category, content, updated_at) "
                "VALUES ('hermes-config', 'legacy fact', '2026-01-02 03:04:05')"
            )
            ensure_memory_schema(conn)

            columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
            self.assertTrue({"status", "verified_at", "superseded_by"} <= columns)
            row = conn.execute("SELECT status, verified_at FROM memories").fetchone()
            self.assertEqual(row[0], "active")
            self.assertEqual(row[1], "2026-01-02 03:04:05")

    def test_migration_is_idempotent(self):
        with self.provider._conn() as conn:
            ensure_memory_schema(conn)
            ensure_memory_schema(conn)
            columns = [row[1] for row in conn.execute("PRAGMA table_info(memories)")]
        self.assertEqual(columns.count("status"), 1)

    # --- write path ---

    def test_supersedes_retires_the_old_row_and_hides_it_from_search(self):
        old_id = self.provider.add_memory("The default model is gpt-6-luna", category="hermes-config")
        result = self.provider._write_memory(
            "The default model is deepseek-v4.1-flash", "hermes-config", "tool", [old_id]
        )

        self.assertTrue(result["created"])
        self.assertEqual(result["superseded"], [old_id])
        row = self._row(old_id)
        self.assertEqual(row["status"], "superseded")
        self.assertEqual(row["superseded_by"], result["memory_id"])

        found = self.provider.search_memories("default model")
        self.assertEqual([entry["id"] for entry in found], [result["memory_id"]])

    def test_repeating_the_same_fact_refreshes_instead_of_duplicating(self):
        first_id = self.provider.add_memory("Release branch is named alpha", category="project")
        self._age(first_id, 40)

        second_id = self.provider.add_memory("Release  branch is named alpha", category="project")
        self.assertEqual(first_id, second_id)

        with self.provider._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertLessEqual(self.provider.search_memories("release branch")[0]["age_days"], 1)

    def test_list_hides_superseded_rows_unless_requested(self):
        old_id = self.provider.add_memory("Old fact", category="general")
        self.provider._write_memory("New fact", "general", "tool", [old_id])

        self.assertEqual([row["content"] for row in self.provider.list_memories()], ["New fact"])
        everything = self.provider.list_memories(include_superseded=True)
        self.assertEqual({row["status"] for row in everything}, {"active", "superseded"})

    # --- freshness reporting ---

    def test_search_marks_entries_past_their_category_ttl(self):
        fresh_id = self.provider.add_memory("Fresh config fact", category="hermes-config")
        stale_id = self.provider.add_memory("Stale config fact", category="hermes-config")
        self._age(stale_id, 45)

        by_id = {entry["id"]: entry for entry in self.provider.search_memories("config fact")}
        self.assertFalse(by_id[fresh_id]["stale"])
        self.assertTrue(by_id[stale_id]["stale"])
        self.assertEqual(by_id[stale_id]["age_days"], 45)
        self.assertEqual(by_id[stale_id]["ttl_days"], 30)

    def test_durable_categories_do_not_expire(self):
        memory_id = self.provider.add_memory("User prefers headless fetching", category="preference")
        self._age(memory_id, 400)
        entry = self.provider.search_memories("headless fetching")[0]
        self.assertFalse(entry["stale"])
        self.assertEqual(entry["ttl_days"], None)

    def test_date_pinned_claims_are_flagged_even_in_durable_categories(self):
        memory_id = self.provider.add_memory(
            "2026-11-01 時点のモデル割り当ては gpt-6-luna", category="preference"
        )
        self._age(memory_id, 5)
        entry = self.provider.search_memories("モデル割り当て")[0]
        self.assertTrue(entry["date_pinned"])
        self.assertEqual(entry["id"], memory_id)

    def test_a_leading_date_stamp_also_marks_an_entry_as_date_pinned(self):
        memory_id = self.provider.add_memory(
            "2026-11-01 確認: assistant の主モデルは gpt-6-luna", category="hermes-config"
        )
        self._age(memory_id, 5)
        entry = self.provider.search_memories("主モデル")[0]
        self.assertTrue(entry["date_pinned"])
        self.assertEqual(entry["id"], memory_id)

    def test_a_provenance_date_is_not_treated_as_a_decaying_claim(self):
        memory_id = self.provider.add_memory(
            "バージョン規約（2026-11-01 ユーザー指示）: 修正は第3位を上げる", category="general"
        )
        self._age(memory_id, 30)
        entry = self.provider.search_memories("バージョン規約")[0]
        self.assertFalse(entry["date_pinned"])
        self.assertEqual(self.provider.review_memories(), [])

    def test_a_freshly_written_dated_claim_is_not_flagged(self):
        memory_id = self.provider.add_memory(
            "2026-11-07 確認: 既定モデルは grok-4.7", category="hermes-config"
        )
        entry = self.provider.search_memories("既定モデル")[0]
        self.assertFalse(entry["date_pinned"])
        self.assertFalse(entry["stale"])
        self.assertEqual(self.provider.review_memories(), [])

    def test_prefetch_shows_age_and_stale_marker(self):
        stale_id = self.provider.add_memory("Stale config fact", category="hermes-config")
        self._age(stale_id, 45)

        block = self.provider.prefetch("config fact")
        self.assertIn(f"[ID: {stale_id}]", block)
        self.assertIn("45日前", block)
        self.assertIn(sqlite_memory_module.STALE_MARKER, block)

    # --- prefetch review hint ---

    def test_prefetch_hint_names_the_overdue_ids_and_the_tools(self):
        stale_id = self.provider.add_memory("Stale environment fact", category="environment")
        self._age(stale_id, 60)
        self.provider.add_memory("Fresh environment fact", category="environment")

        block = self.provider.prefetch("environment fact")
        self.assertIn("1 memory past their freshness window", block)
        self.assertIn(f"ID: {stale_id}", block)
        self.assertIn("sqlite_verify", block)
        self.assertIn("supersedes=[id]", block)

    def test_prefetch_hint_is_absent_when_nothing_is_overdue(self):
        self.provider.add_memory("Fresh environment fact", category="environment")
        self.assertNotIn("Review:", self.provider.prefetch("environment fact"))

    def test_prefetch_hint_ignores_fresh_dated_claims(self):
        self.provider.add_memory("2026-11-07 確認: 既定モデルは grok-4.7", category="hermes-config")
        self.assertNotIn("Review:", self.provider.prefetch("既定モデル"))

    def test_prefetch_hint_caps_the_listed_ids(self):
        for index in range(5):
            memory_id = self.provider.add_memory(f"Stale environment fact {index}", category="environment")
            self._age(memory_id, 60)

        hint = [line for line in self.provider.prefetch("environment fact").splitlines() if line.startswith("Review:")]
        self.assertEqual(len(hint), 1)
        self.assertIn("5 memories", hint[0])
        self.assertIn("(+2 more)", hint[0])

    def test_prefetch_hint_skips_superseded_rows(self):
        old_id = self.provider.add_memory("Old environment fact", category="environment")
        self._age(old_id, 60)
        self.provider._write_memory("New environment fact", "environment", "tool", [old_id])

        block = self.provider.prefetch("environment fact")
        self.assertNotIn("Review:", block)

    # --- review / verify ---

    def test_review_lists_stale_and_date_pinned_entries_only(self):
        fresh_id = self.provider.add_memory("Fresh environment fact", category="environment")
        stale_id = self.provider.add_memory("Stale environment fact", category="environment")
        self._age(stale_id, 60)
        pinned_id = self.provider.add_memory("2026-10-01 時点では port 8080", category="preference")
        self._age(pinned_id, 5)

        flagged = {entry["id"]: entry for entry in self.provider.review_memories()}
        self.assertEqual(set(flagged), {stale_id, pinned_id})
        self.assertIn("TTL 30日超過", flagged[stale_id]["reason"])
        self.assertIn("時点付きの記述", flagged[pinned_id]["reason"])
        self.assertNotIn(fresh_id, flagged)

    def test_verify_resets_the_freshness_clock(self):
        memory_id = self.provider.add_memory("Stale environment fact", category="environment")
        self._age(memory_id, 60)
        self.assertEqual(self.provider.verify_memories([memory_id]), 1)

        entry = self.provider.search_memories("environment fact")[0]
        self.assertEqual(entry["age_days"], 0)
        self.assertFalse(entry["stale"])
        self.assertEqual(self.provider.review_memories(), [])

    # --- tool surface ---

    def test_remember_tool_accepts_supersedes_and_reports_the_retired_ids(self):
        old_id = self.provider.add_memory("Old rule", category="rule")
        payload = sqlite_memory_module.json.loads(self.provider.handle_tool_call(
            "sqlite_remember",
            {"content": "New rule", "category": "rule", "supersedes": [old_id]},
        ))
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["superseded"], [old_id])
        self.assertNotEqual(payload["memory_id"], old_id)

    def test_review_and_verify_tools_round_trip(self):
        memory_id = self.provider.add_memory("Stale environment fact", category="environment")
        self._age(memory_id, 60)

        review = sqlite_memory_module.json.loads(
            self.provider.handle_tool_call("sqlite_review", {})
        )
        self.assertEqual(review["count"], 1)
        self.assertEqual(review["needs_review"][0]["id"], memory_id)

        verified = sqlite_memory_module.json.loads(
            self.provider.handle_tool_call("sqlite_verify", {"memory_ids": [memory_id]})
        )
        self.assertEqual(verified["verified"], 1)
        self.assertEqual(self.provider.review_memories(), [])

    def test_tool_arguments_are_validated(self):
        self.assertIn(
            "error",
            sqlite_memory_module.json.loads(self.provider.handle_tool_call("sqlite_verify", {})),
        )
        self.assertIn(
            "error",
            sqlite_memory_module.json.loads(
                self.provider.handle_tool_call("sqlite_remember", {"content": "x", "supersedes": "1"})
            ),
        )

    # --- shared resolution / consistency ---

    def test_null_verified_at_falls_back_to_the_last_write_everywhere(self):
        memory_id = self.provider.add_memory("Legacy environment fact", category="environment")
        with self.provider._conn() as conn:
            with conn:
                conn.execute(
                    "UPDATE memories SET verified_at = NULL, updated_at = ?, created_at = ? WHERE id = ?",
                    (_days_ago(60), _days_ago(60), memory_id),
                )

        entry = self.provider.search_memories("legacy environment")[0]
        self.assertEqual(entry["age_days"], 60)
        self.assertTrue(entry["stale"])
        self.assertEqual([row["id"] for row in self.provider.review_memories()], [memory_id])

        hint_line = [line for line in self.provider.prefetch("legacy environment").splitlines()
                     if line.startswith("Review:")]
        self.assertEqual(len(hint_line), 1)
        self.assertIn(f"ID: {memory_id}", hint_line[0])

    def test_hint_and_review_agree_on_the_same_rows(self):
        dated_id = self.provider.add_memory("2026-10-01 時点の port は 8080", category="preference")
        self._age(dated_id, 5)
        fresh_id = self.provider.add_memory("Fresh preference note", category="preference")

        review_ids = {row["id"] for row in self.provider.review_memories()}
        hint_line = [line for line in self.provider.prefetch("preference note port").splitlines()
                     if line.startswith("Review:")]
        self.assertEqual(review_ids, {dated_id})
        self.assertNotIn(fresh_id, review_ids)
        self.assertEqual(len(hint_line), 1)
        self.assertIn(f"ID: {dated_id}", hint_line[0])

    # --- supersede reporting ---

    def test_supersedes_reports_ids_that_were_not_retired(self):
        live_id = self.provider.add_memory("Live general fact", category="general")
        retired_id = self.provider.add_memory("Retired general fact", category="general")
        self.provider._write_memory("Replacement", "general", "tool", [retired_id])

        written = self.provider._write_memory(
            "Another replacement", "general", "tool", [live_id, retired_id, 9999]
        )
        self.assertEqual(written["superseded"], [live_id])
        self.assertEqual(written["not_found"], [retired_id, 9999])
        self.assertEqual(self._row(live_id)["status"], "superseded")

    def test_remember_tool_surfaces_unknown_supersede_ids(self):
        payload = json.loads(self.provider.handle_tool_call(
            "sqlite_remember", {"content": "Fact with a typo", "supersedes": [4242]}
        ))
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["not_found"], [4242])

    # --- verify scope ---

    def test_verify_ignores_retired_rows_and_does_not_touch_updated_at(self):
        old_id = self.provider.add_memory("Old environment fact", category="environment")
        live_id = self.provider.add_memory("Live environment fact", category="environment")
        self.provider._write_memory("New environment fact", "environment", "tool", [old_id])
        with self.provider._conn() as conn:
            with conn:
                conn.execute(
                    "UPDATE memories SET verified_at = ? WHERE id = ?", (_days_ago(30), live_id)
                )

        with self.provider._conn() as conn:
            before = conn.execute(
                "SELECT updated_at, verified_at FROM memories WHERE id = ?", (live_id,)
            ).fetchone()
        self.assertEqual(self.provider.verify_memories([old_id]), 0)
        self.assertEqual(self.provider.verify_memories([live_id]), 1)

        with self.provider._conn() as conn:
            after = conn.execute(
                "SELECT updated_at, verified_at FROM memories WHERE id = ?", (live_id,)
            ).fetchone()
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertNotEqual(after["verified_at"], before["verified_at"])
        self.assertEqual(self.provider.search_memories("live environment")[0]["age_days"], 0)

    # --- stats semantics ---

    def test_stats_total_matches_category_sum_and_separates_retired_rows(self):
        live_id = self.provider.add_memory("Live fact", category="project")
        retired_id = self.provider.add_memory("Retired fact", category="project")
        self.provider._write_memory("Replacement fact", "project", "tool", [retired_id])

        stats = self.provider.get_stats()
        self.assertEqual(stats["total_memories"], 2)
        self.assertEqual(stats["active_memories"], 2)
        self.assertEqual(sum(stats["categories"].values()), stats["total_memories"])
        self.assertEqual(stats["all_memories"], 3)
        self.assertIn(live_id, [row["id"] for row in self.provider.list_memories()])


if __name__ == "__main__":
    unittest.main()
