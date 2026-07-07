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


class TestConversationStoreConnectionLifecycle(unittest.TestCase):
    """Test that connections are reused via threading.local() and properly cleaned up."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_cleanup.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_close_method_releases_connection(self):
        """close() should release the per-thread connection."""
        store = ConversationStore(db_path=self.db_path)
        conn = store._get_connection()
        self.assertIsNotNone(conn)
        # close() releases the connection
        store.close()
        # After close, _get_connection creates a fresh one
        conn2 = store._get_connection()
        self.assertIsNotNone(conn2)

    def test_get_connection_reuses_open_connection(self):
        """_get_connection should return the same connection on repeated calls."""
        store = ConversationStore(db_path=self.db_path)
        conn1 = store._get_connection()
        conn2 = store._get_connection()
        self.assertIs(conn1, conn2)

    def test_get_connection_after_close_creates_new(self):
        """After closing, _get_connection should create a fresh connection."""
        store = ConversationStore(db_path=self.db_path)
        conn1 = store._get_connection()
        store.close()
        conn2 = store._get_connection()
        # Should get a new connection object
        self.assertIsNotNone(conn2)
        self.assertIsNot(conn1, conn2)

    def test_operations_work_without_reconnect(self):
        """Multiple operations should reuse the same connection successfully."""
        store = ConversationStore(db_path=self.db_path)
        conv_id = store.create_conversation(title="Test")
        store.add_message(conv_id, "user", "Hello")
        store.add_message(conv_id, "assistant", "Hi there")
        messages = store.get_messages(conv_id)
        self.assertEqual(len(messages), 2)


if __name__ == "__main__":
    unittest.main()
