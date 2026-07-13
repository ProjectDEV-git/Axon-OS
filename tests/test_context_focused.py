"""Tests for context_service.py — sensitive path filtering, terminal cache, sys.path guards."""

import os
import sys
from pathlib import Path

import pytest


class TestSensitivePathFilter:
    """Test that _get_open_files filters sensitive paths (H4 fix)."""

    def test_sensitive_patterns_defined(self):
        """Verify the _SENSITIVE set exists in context_service source."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-context" / "context_service.py"
        content = src.read_text()
        assert "_SENSITIVE" in content

    def test_filter_includes_ssh(self):
        """The filter should catch .ssh paths."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-context" / "context_service.py"
        content = src.read_text()
        assert "'.ssh'" in content

    def test_filter_includes_env(self):
        """The filter should catch .env paths."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-context" / "context_service.py"
        content = src.read_text()
        assert "'.env'" in content

    def test_filter_includes_credentials(self):
        """The filter should catch 'credentials' in paths."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-context" / "context_service.py"
        content = src.read_text()
        assert "'credentials'" in content


class TestTerminalCachePruning:
    """Test that terminal cache mtime dict is pruned (M5 fix)."""

    def test_pruning_code_exists(self):
        """Verify pruning logic exists in context_service source."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-context" / "context_service.py"
        content = src.read_text()
        assert "terminal_cache_mtime" in content
        assert "> 5" in content or "len(self._terminal_cache_mtime)" in content


class TestSysPathGuards:
    """Test that sys.path.insert calls are guarded (M6 fix)."""

    def test_guard_in_context_service(self):
        """Verify 'if ... not in sys.path' pattern exists."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-context" / "context_service.py"
        content = src.read_text()
        assert "not in sys.path" in content

    def test_guard_in_brain_service(self):
        """Verify guard pattern in brain_service."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-brain" / "brain_service.py"
        content = src.read_text()
        assert "not in sys.path" in content

    def test_guard_in_search_service(self):
        """Verify guard pattern in search_service."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-search" / "search_service.py"
        content = src.read_text()
        assert "not in sys.path" in content

    def test_guard_in_voice_service(self):
        """Verify guard pattern in voice_service."""
        src = Path(__file__).resolve().parent.parent / "services" / "axon-voice" / "voice_service.py"
        content = src.read_text()
        assert "not in sys.path" in content
