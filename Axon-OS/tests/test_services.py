#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
import unittest
import dbus
from pathlib import Path

# Paths
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
BRAIN_SCRIPT = PROJECT_ROOT / "services" / "axon-brain" / "brain_service.py"
CONTEXT_SCRIPT = PROJECT_ROOT / "services" / "axon-context" / "context_service.py"

class TestAxonServices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Spawns the D-Bus Brain and Context daemons in background processes for testing."""
        # Ensure they are executable
        os.chmod(BRAIN_SCRIPT, 0o755)
        os.chmod(CONTEXT_SCRIPT, 0o755)

        # Spawn daemons
        cls.brain_proc = subprocess.Popen(
            [sys.executable, str(BRAIN_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        cls.context_proc = subprocess.Popen(
            [sys.executable, str(CONTEXT_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Wait for services to register on Session Bus
        time.sleep(2.0)

        # Connect DBus
        cls.bus = dbus.SessionBus()
        cls.brain = cls.bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
        cls.context = cls.bus.get_object("org.axonos.Context", "/org/axonos/Context")

    @classmethod
    def tearDownClass(cls):
        # Tear down daemons
        cls.brain_proc.terminate()
        cls.context_proc.terminate()
        cls.brain_proc.wait()
        cls.context_proc.wait()

    # ------------------------------------------------------------------
    # Brain Service Tests
    # ------------------------------------------------------------------

    def test_brain_status_endpoint(self):
        status_json = self.brain.GetStatus(dbus_interface="org.axonos.Brain")
        status = json.loads(status_json)
        self.assertIn("ollama_online", status)
        self.assertIn("configured_models", status)
        self.assertIn("speed_model", status["configured_models"])

    def test_brain_list_models(self):
        models_json = self.brain.ListModels(dbus_interface="org.axonos.Brain")
        models = json.loads(models_json)
        self.assertTrue(isinstance(models, (list, dict)))

    def test_brain_conversations_crud(self):
        # Create
        conv_id = self.brain.CreateConversation("Test System Prompt", "Test Chat", dbus_interface="org.axonos.Brain")
        self.assertTrue(len(conv_id) > 0)

        # Add message
        success = self.brain.AddMessage(conv_id, "user", "Hello Brain!", dbus_interface="org.axonos.Brain")
        self.assertTrue(success)

        # Get messages
        messages_json = self.brain.GetMessages(conv_id, dbus_interface="org.axonos.Brain")
        messages = json.loads(messages_json)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "Hello Brain!")

        # List
        convs_json = self.brain.ListConversations(dbus_interface="org.axonos.Brain")
        convs = json.loads(convs_json)
        self.assertTrue(any(c["id"] == conv_id for c in convs))

        # Update title
        updated = self.brain.UpdateTitle(conv_id, "New Title", dbus_interface="org.axonos.Brain")
        self.assertTrue(updated)

        # Delete
        deleted = self.brain.DeleteConversation(conv_id, dbus_interface="org.axonos.Brain")
        self.assertTrue(deleted)

    def test_brain_get_embeddings(self):
        embeddings_json = self.brain.GetEmbeddings("Test embedding query", "", dbus_interface="org.axonos.Brain")
        data = json.loads(embeddings_json)
        self.assertTrue(isinstance(data, (list, dict)))

    # ------------------------------------------------------------------
    # Context Service Tests
    # ------------------------------------------------------------------

    def test_context_window_tracking(self):
        success = self.context.SetActiveWindow("Visual Studio Code", "code", dbus_interface="org.axonos.Context")
        self.assertTrue(success)

        # Query context
        context_json = self.context.GetActiveContext(dbus_interface="org.axonos.Context")
        context = json.loads(context_json)
        self.assertEqual(context["active_window"]["title"], "Visual Studio Code")
        self.assertEqual(context["active_window"]["app"], "code")

    def test_context_space_tracking(self):
        success = self.context.SetActiveSpace("Code Space", dbus_interface="org.axonos.Context")
        self.assertTrue(success)

        # Query context
        context_json = self.context.GetActiveContext(dbus_interface="org.axonos.Context")
        context = json.loads(context_json)
        self.assertEqual(context["active_space"], "Code Space")

    def test_context_string_formatting(self):
        # Setup window/space for test context
        self.context.SetActiveWindow("Visual Studio Code", "code", dbus_interface="org.axonos.Context")
        self.context.SetActiveSpace("Code Space", dbus_interface="org.axonos.Context")
        
        ctx_str = self.context.GetContextString(dbus_interface="org.axonos.Context")
        self.assertIn("Code Space", ctx_str)
        self.assertIn("Visual Studio Code", ctx_str)

if __name__ == "__main__":
    unittest.main()
