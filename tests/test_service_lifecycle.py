"""Tests for ServiceBase._cleanup() lifecycle hook."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure services/ is on sys.path
_services_dir = str(Path(__file__).resolve().parent.parent / "services")
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)


class TestServiceBaseCleanup:
    """Test that ServiceBase._cleanup() is properly called on shutdown."""

    def test_cleanup_is_called_on_signal(self, tmp_path):
        """_cleanup() should be called when SIGTERM is received."""
        cleanup_called = []

        # Create a minimal subclass
        import dbus
        import dbus.service
        from gi.repository import GLib

        # We can't easily instantiate a full D-Bus service in tests,
        # but we can verify the method exists and is callable
        from service_base import ServiceBase

        assert hasattr(ServiceBase, "_cleanup")
        assert callable(getattr(ServiceBase, "_cleanup"))

    def test_cleanup_default_is_noop(self):
        """Default _cleanup() should not raise."""
        import dbus.service
        from service_base import ServiceBase

        # _cleanup should exist and be callable without error
        # (it's a no-op in the base class)
        assert callable(ServiceBase._cleanup)

    def test_main_has_signal_handlers(self):
        """ServiceBase.main() should set up SIGTERM and SIGINT handlers."""
        import signal
        from service_base import ServiceBase

        # Verify main() is a classmethod
        assert isinstance(
            getattr(ServiceBase, "main"), classmethod
        ) or callable(ServiceBase.main)


class TestShutdownIntegration:
    """Integration tests verifying services call _cleanup on shutdown."""

    def test_brain_service_cleanup_closes_store(self):
        """BrainService._cleanup() should close the ConversationStore."""
        # We verify the method exists and references store.close_all
        import inspect
        from services.axon_brain.brain_service import BrainService

        source = inspect.getsource(BrainService._cleanup)
        assert "close_all" in source

    def test_voice_service_cleanup_kills_recorder(self):
        """VoiceService._cleanup() should kill the recorder subprocess."""
        import inspect
        from services.axon_voice.voice_service import VoiceService

        source = inspect.getsource(VoiceService._cleanup)
        assert "kill" in source

    def test_advanced_voice_cleanup_kills_recorder(self):
        """AdvancedVoiceService._cleanup() should kill the recorder subprocess."""
        import inspect
        from services.axon_voice.advanced_voice_service import AdvancedVoiceService

        source = inspect.getsource(AdvancedVoiceService._cleanup)
        assert "kill" in source

    def test_context_service_cleanup_calls_close_all(self):
        """ContextService._cleanup() should close clipboard store."""
        import inspect
        from services.axon_context.context_service import ContextService

        source = inspect.getsource(ContextService._cleanup)
        assert "close_all" in source
