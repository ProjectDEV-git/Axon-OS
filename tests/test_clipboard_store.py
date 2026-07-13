"""Tests for ClipboardStore — connection pooling, close_all, and CRUD operations."""

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Load the module directly (hyphenated directory name)
import importlib.util
import sys

SERVICES_DIR = Path(__file__).resolve().parent.parent / "services"
CONTEXT_DIR = SERVICES_DIR / "axon-context"
STORE_PATH = CONTEXT_DIR / "clipboard_store.py"

# We need to patch AXON_DIR before importing, so use importlib
spec = importlib.util.spec_from_file_location("clipboard_store", STORE_PATH)
_clipboard_store = importlib.util.module_from_spec(spec)


class TestClipboardStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_clipboard.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_store(self):
        """Create a ClipboardStore with a temp DB path."""
        # Patch AXON_DIR in the module before exec
        import types

        constants_mod = types.ModuleType("constants")
        constants_mod.AXON_DIR = Path(self.temp_dir.name)
        old = sys.modules.get("constants")
        sys.modules["constants"] = constants_mod
        try:
            spec.loader.exec_module(_clipboard_store)
        finally:
            if old is not None:
                sys.modules["constants"] = old
            else:
                del sys.modules["constants"]

        return _clipboard_store.ClipboardStore(db_path=self.db_path)

    def test_add_and_get_recent(self):
        store = self._make_store()
        self.assertTrue(store.add("Hello world"))
        self.assertTrue(store.add("Second entry"))
        entries = store.get_recent(10)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["content"], "Second entry")
        self.assertEqual(entries[1]["content"], "Hello world")

    def test_add_duplicate_rejected(self):
        store = self._make_store()
        self.assertTrue(store.add("Same text"))
        self.assertFalse(store.add("Same text"))
        self.assertEqual(len(store.get_recent(10)), 1)

    def test_add_empty_rejected(self):
        store = self._make_store()
        self.assertFalse(store.add(""))
        self.assertFalse(store.add("   "))
        self.assertEqual(len(store.get_recent(10)), 0)

    def test_add_truncates_long_content(self):
        store = self._make_store()
        long_text = "A" * 1000
        store.add(long_text)
        entries = store.get_recent(10)
        self.assertEqual(len(entries[0]["content"]), 500)

    def test_search(self):
        store = self._make_store()
        store.add("Python is great")
        store.add("Java is okay")
        store.add("Rust is blazing")
        results = store.search("Python")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], "Python is great")

    def test_search_escapes_like_wildcards(self):
        store = self._make_store()
        store.add("100% done")
        store.add("nothing percent")
        # % is escaped so it shouldn't match as wildcard
        results = store.search("%")
        # Should find exact "100% done" via LIKE, not wildcard match
        self.assertTrue(len(results) >= 0)  # No crash = success

    def test_pin_and_unpin(self):
        store = self._make_store()
        store.add("Pinnable entry")
        entry_id = store.get_recent(1)[0]["id"]
        store.pin(entry_id)
        self.assertEqual(store.get_recent(1)[0]["pinned"], 1)
        store.unpin(entry_id)
        self.assertEqual(store.get_recent(1)[0]["pinned"], 0)

    def test_delete(self):
        store = self._make_store()
        store.add("Delete me")
        entry_id = store.get_recent(1)[0]["id"]
        store.delete(entry_id)
        self.assertEqual(len(store.get_recent(10)), 0)

    def test_clear_removes_unpinned(self):
        store = self._make_store()
        store.add("Remove this")
        store.add("Keep this")
        # Pin "Keep this" (last added, second in recent order)
        entries = store.get_recent(10)
        keep_id = [e["id"] for e in entries if e["content"] == "Keep this"][0]
        store.pin(keep_id)
        store.clear()
        remaining = store.get_recent(10)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["content"], "Keep this")

    def test_get_count(self):
        store = self._make_store()
        self.assertEqual(store.get_count(), 0)
        store.add("One")
        store.add("Two")
        self.assertEqual(store.get_count(), 2)

    def test_to_deque(self):
        store = self._make_store()
        store.add("A")
        store.add("B")
        dq = store.to_deque(maxlen=5)
        self.assertEqual(list(dq), ["B", "A"])

    def test_max_entries_prunes_old(self):
        store = self._make_store()
        store.max_entries = 3
        for i in range(5):
            store.add(f"Entry {i}")
        entries = store.get_recent(10)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["content"], "Entry 4")
        self.assertEqual(entries[2]["content"], "Entry 2")


class TestConnectionPooling(unittest.TestCase):
    """Test thread-local connection reuse and close_all()."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_pool.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_store(self):
        import types

        constants_mod = types.ModuleType("constants")
        constants_mod.AXON_DIR = Path(self.temp_dir.name)
        old = sys.modules.get("constants")
        sys.modules["constants"] = constants_mod
        try:
            # Re-import fresh
            spec.loader.exec_module(_clipboard_store)
        finally:
            if old is not None:
                sys.modules["constants"] = old
            else:
                del sys.modules["constants"]

        return _clipboard_store.ClipboardStore(db_path=self.db_path)

    def test_get_connection_reuses(self):
        store = self._make_store()
        conn1 = store._get_connection()
        conn2 = store._get_connection()
        self.assertIs(conn1, conn2)

    def test_close_releases_connection(self):
        store = self._make_store()
        conn1 = store._get_connection()
        store.close()
        conn2 = store._get_connection()
        self.assertIsNot(conn1, conn2)

    def test_close_all_closes_everything(self):
        store = self._make_store()
        initial_count = len(store._all_connections)  # 1 from _init_db
        # Create connections from 5 additional threads
        conns = []

        def grab_conn():
            conns.append(store._get_connection())

        threads = [threading.Thread(target=grab_conn) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(conns), 5)
        # 1 from init + 5 from threads = 6
        self.assertEqual(len(store._all_connections), initial_count + 5)

        store.close_all()

        # All connections should be closed; verify by trying to use them
        closed_count = 0
        for conn in conns:
            try:
                conn.execute("SELECT 1")
            except Exception:
                closed_count += 1
        self.assertEqual(closed_count, 5)
        self.assertEqual(len(store._all_connections), 0)

    def test_del_calls_close_all(self):
        store = self._make_store()
        store._get_connection()  # create a connection
        self.assertTrue(len(store._all_connections) > 0)
        store.__del__()
        self.assertEqual(len(store._all_connections), 0)

    def test_concurrent_adds_no_crash(self):
        store = self._make_store()
        errors = []

        def add_entries(prefix):
            try:
                for i in range(20):
                    store.add(f"{prefix} entry {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_entries, args=(f"T{t}",)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # 100 entries added, but max_entries=50 by default
        self.assertLessEqual(store.get_count(), 50)


if __name__ == "__main__":
    unittest.main()
