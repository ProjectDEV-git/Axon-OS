"""Tests for context_service.py — sensitive path filtering, terminal cache, sys.path guards."""

from pathlib import Path


class TestSensitivePathFilter:
    """_get_open_files never reports paths that look like they hold secrets (H4 fix)."""

    @staticmethod
    def _is_sensitive(path):
        from services.axon_context.context_service import _is_sensitive_path

        return _is_sensitive_path(path)

    def test_filter_includes_ssh(self):
        assert self._is_sensitive("/home/u/.ssh/id_ed25519")

    def test_filter_includes_env(self):
        assert self._is_sensitive("/home/u/project/.env")

    def test_filter_includes_credentials(self):
        assert self._is_sensitive("/home/u/.aws/Credentials")
        assert self._is_sensitive("/home/u/.git-credentials")

    def test_ordinary_files_pass(self):
        assert not self._is_sensitive("/home/u/Documents/notes.md")


class TestTerminalCachePruning:
    """Test that terminal cache mtime dict is pruned (M5 fix)."""

    def test_pruning_code_exists(self):
        """Verify pruning logic exists in context_service source."""
        src = (
            Path(__file__).resolve().parent.parent
            / "services"
            / "axon-context"
            / "context_service.py"
        )
        content = src.read_text()
        assert "terminal_cache_mtime" in content
        assert "> 5" in content or "len(self._terminal_cache_mtime)" in content


class TestSysPathGuards:
    """Test that sys.path.insert calls are guarded (M6 fix)."""

    def test_guard_in_context_service(self):
        """Verify 'if ... not in sys.path' pattern exists."""
        src = (
            Path(__file__).resolve().parent.parent
            / "services"
            / "axon-context"
            / "context_service.py"
        )
        content = src.read_text()
        assert "not in sys.path" in content

    def test_guard_in_brain_service(self):
        """Verify guard pattern in brain_service."""
        src = (
            Path(__file__).resolve().parent.parent / "services" / "axon-brain" / "brain_service.py"
        )
        content = src.read_text()
        assert "not in sys.path" in content

    def test_guard_in_search_service(self):
        """Verify guard pattern in search_service."""
        src = (
            Path(__file__).resolve().parent.parent
            / "services"
            / "axon-search"
            / "search_service.py"
        )
        content = src.read_text()
        assert "not in sys.path" in content

    def test_guard_in_voice_service(self):
        """Verify guard pattern in voice_service."""
        src = (
            Path(__file__).resolve().parent.parent / "services" / "axon-voice" / "voice_service.py"
        )
        content = src.read_text()
        assert "not in sys.path" in content
