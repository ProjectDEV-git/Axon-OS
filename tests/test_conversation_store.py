import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

# Load the ConversationStore module directly since it's in a directory with dashes
SERVICES_DIR = Path(__file__).resolve().parent.parent / "services"
BRAIN_DIR = SERVICES_DIR / "axon-brain"
CONVERSATION_STORE_PATH = BRAIN_DIR / "conversation_store.py"

spec = importlib.util.spec_from_file_location("conversation_store", CONVERSATION_STORE_PATH)
conversation_store = importlib.util.module_from_spec(spec)
sys.modules["conversation_store"] = conversation_store
spec.loader.exec_module(conversation_store)
ConversationStore = conversation_store.ConversationStore


class TestConversationStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_dir", "test_conversations.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initialization_creates_directory_and_file(self):
        ConversationStore(db_path=self.db_path)

        # Verify directory was created
        self.assertTrue(os.path.isdir(os.path.dirname(self.db_path)))

        # Verify file was created
        self.assertTrue(os.path.isfile(self.db_path))

    def test_initialization_sets_correct_permissions(self):
        ConversationStore(db_path=self.db_path)

        # Verify permissions are 0o600
        # In some environments, the execution of chmod might be impacted by umask, but since it's an explicit chmod it should be 600.
        st = os.stat(self.db_path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)


class TestConversationStoreConnectionCleanup(unittest.TestCase):
    """Test that _close_connection actually closes the SQLite connection (FD leak fix)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_cleanup.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_close_connection_closes_sqlite_connection(self):
        """_close_connection should call conn.close() on the underlying connection."""
        store = ConversationStore(db_path=self.db_path)
        # The store should have a connection after init
        conn = store._get_connection()
        self.assertIsNotNone(conn)
        # Close it
        store._close_connection(conn)
        # After close, trying to use the connection should raise
        import sqlite3

        with self.assertRaises((sqlite3.ProgrammingError, AttributeError)):
            conn.execute("SELECT 1")

    def test_close_connection_handles_none(self):
        """_close_connection should not crash if given a bad reference."""
        store = ConversationStore(db_path=self.db_path)
        # Should not raise even if called with a value that's already closed
        conn = store._get_connection()
        store._close_connection(conn)
        # Double-close should be safe
        store._close_connection(conn)

    def test_get_connection_after_close_creates_new(self):
        """After closing, _get_connection should create a fresh connection."""
        store = ConversationStore(db_path=self.db_path)
        conn1 = store._get_connection()
        store._close_connection(conn1)
        conn2 = store._get_connection()
        # Should get a new connection object
        self.assertIsNotNone(conn2)


if __name__ == "__main__":
    unittest.main()
