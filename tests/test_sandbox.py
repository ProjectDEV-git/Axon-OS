"""Tests for SandboxManager fail-closed behavior."""

from unittest.mock import MagicMock, patch


class TestSandboxFailClosed:
    """Verify sandbox defaults to deny on errors."""

    def _make_manager(self):
        """Create a SandboxManager with mocked D-Bus."""
        with patch("dbus.service.BusName"), patch("dbus.service.Object.__init__"):
            from services.axon_sandbox.sandbox_manager import SandboxManager
            manager = SandboxManager.__new__(SandboxManager)
            manager.session_bus = MagicMock()
            return manager

    def test_missing_file_returns_deny(self):
        manager = self._make_manager()
        callback = MagicMock()

        with patch("pathlib.Path.exists", return_value=False):
            manager._do_audit_and_prompt("/nonexistent/script.sh", callback, MagicMock())

        callback.assert_called_once_with("deny")

    def test_directory_returns_deny(self):
        manager = self._make_manager()
        callback = MagicMock()

        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=False):
            manager._do_audit_and_prompt("/tmp", callback, MagicMock())

        callback.assert_called_once_with("deny")

    def test_read_error_returns_deny(self):
        manager = self._make_manager()
        callback = MagicMock()

        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", side_effect=PermissionError("denied")):
            manager._do_audit_and_prompt("/tmp/script.sh", callback, MagicMock())

        callback.assert_called_once_with("deny")

    def test_general_exception_returns_deny(self):
        manager = self._make_manager()
        callback = MagicMock()

        with patch("pathlib.Path.exists", side_effect=RuntimeError("unexpected")):
            manager._do_audit_and_prompt("/tmp/script.sh", callback, MagicMock())

        callback.assert_called_once_with("deny")
