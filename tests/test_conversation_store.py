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


if __name__ == "__main__":
    unittest.main()
