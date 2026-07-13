"""Tests for SettingsExecutor validation and routing logic."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load module from hyphenated directory
_APPS_DIR = Path(__file__).resolve().parent.parent / "apps"
_EXECUTOR_PATH = _APPS_DIR / "axon-settings" / "settings_executor.py"

_spec = importlib.util.spec_from_file_location("settings_executor", _EXECUTOR_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["settings_executor"] = _mod
_spec.loader.exec_module(_mod)
SettingsExecutor = _mod.SettingsExecutor


class TestValidateValue:
    """Tests for SettingsExecutor._validate_value static method."""

    @staticmethod
    def _validate(value, expected_type=str):
        return SettingsExecutor._validate_value(value, expected_type)

    def test_none_returns_false(self):
        assert self._validate(None) is False

    def test_bool_true_accepted(self):
        assert self._validate(True, expected_type=bool) is True

    def test_bool_false_accepted(self):
        assert self._validate(False, expected_type=bool) is True

    def test_bool_rejects_string(self):
        assert self._validate("true", expected_type=bool) is False

    def test_int_accepts_integer(self):
        assert self._validate(42, expected_type=int) is True

    def test_int_accepts_string_number(self):
        assert self._validate("42", expected_type=int) is True

    def test_int_accepts_negative_string(self):
        assert self._validate("-5", expected_type=int) is True

    def test_int_rejects_non_numeric_string(self):
        assert self._validate("abc", expected_type=int) is False

    def test_float_accepts_float(self):
        assert self._validate(1.5, expected_type=float) is True

    def test_float_accepts_int(self):
        assert self._validate(3, expected_type=float) is True

    def test_float_accepts_string_number(self):
        assert self._validate("3.14", expected_type=float) is True

    def test_float_rejects_non_numeric_string(self):
        assert self._validate("pi", expected_type=float) is False

    def test_str_accepts_alphanumeric(self):
        assert self._validate("dark") is True

    def test_str_accepts_with_hyphen(self):
        assert self._validate("my-theme") is True

    def test_str_accepts_with_underscore(self):
        assert self._validate("my_theme") is True

    def test_str_accepts_with_dot(self):
        assert self._validate("theme.v2") is True

    def test_str_rejects_spaces(self):
        assert self._validate("dark mode") is False

    def test_str_rejects_shell_metacharacters(self):
        for char in [";", "|", "&", "$", "`", "(", ")", "{", "}"]:
            assert self._validate(f"dark{char}") is False

    def test_str_rejects_over_64_chars(self):
        assert self._validate("a" * 65) is False

    def test_str_accepts_exactly_64_chars(self):
        assert self._validate("a" * 64) is True

    def test_unknown_type_returns_false(self):
        assert self._validate("test", expected_type=list) is False


class TestExecuteCommand:
    """Tests for the execute_command routing logic."""

    def _make_executor(self, brain_mock=None):
        executor = SettingsExecutor.__new__(SettingsExecutor)
        executor._bus = MagicMock()
        executor._brain = brain_mock or MagicMock()
        return executor

    def test_offline_brain_returns_error(self):
        executor = self._make_executor(brain_mock=None)
        # Patch _connect to avoid real D-Bus
        with patch.object(executor, "_connect"):
            executor._brain = None
            result = executor.execute_command("turn on wifi")
        assert result["success"] is False
        assert "offline" in result["message"].lower()

    def test_invalid_action_format_rejected(self):
        brain = MagicMock()
        brain.Generate.return_value = json.dumps({"action": "rm -rf /", "value": True})
        executor = self._make_executor(brain_mock=brain)
        result = executor.execute_command("do something bad")
        assert result["success"] is False
        assert "invalid" in result["message"].lower()

    def test_set_theme_dark(self):
        brain = MagicMock()
        brain.Generate.return_value = json.dumps({"action": "set_theme", "value": "dark"})
        executor = self._make_executor(brain_mock=brain)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = executor.execute_command("make it dark")
        assert result["success"] is True
        assert "dark" in result["message"].lower()

    def test_set_theme_invalid_value(self):
        brain = MagicMock()
        brain.Generate.return_value = json.dumps({"action": "set_theme", "value": "blue"})
        executor = self._make_executor(brain_mock=brain)
        result = executor.execute_command("make it blue")
        assert result["success"] is False

    def test_set_volume_mute(self):
        brain = MagicMock()
        brain.Generate.return_value = json.dumps({"action": "set_volume", "value": "mute"})
        executor = self._make_executor(brain_mock=brain)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = executor.execute_command("mute sound")
        assert result["success"] is True

    def test_set_volume_out_of_range_fails(self):
        brain = MagicMock()
        brain.Generate.return_value = json.dumps({"action": "set_volume", "value": 150})
        executor = self._make_executor(brain_mock=brain)
        # Volume 150 fails range check (0-100) and returns error
        result = executor.execute_command("max volume")
        assert result["success"] is False
        assert "150" in result["message"] or "volume" in result["message"].lower()

    def test_set_wifi_on(self):
        brain = MagicMock()
        brain.Generate.return_value = json.dumps({"action": "set_wifi", "value": True})
        executor = self._make_executor(brain_mock=brain)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = executor.execute_command("turn on wifi")
        assert result["success"] is True
        assert "on" in result["message"].lower()

    def test_unknown_action_returns_message(self):
        brain = MagicMock()
        brain.Generate.return_value = json.dumps(
            {"action": "unknown", "message": "I can't do that."}
        )
        executor = self._make_executor(brain_mock=brain)
        result = executor.execute_command("fly to the moon")
        assert result["success"] is False
        assert "can't" in result["message"]

    def test_malformed_json_returns_error(self):
        brain = MagicMock()
        brain.Generate.return_value = "not json at all"
        executor = self._make_executor(brain_mock=brain)
        result = executor.execute_command("break things")
        assert result["success"] is False

    def test_json_with_markdown_fence(self):
        brain = MagicMock()
        brain.Generate.return_value = '```json\n{"action": "set_theme", "value": "light"}\n```'
        executor = self._make_executor(brain_mock=brain)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = executor.execute_command("make it light")
        assert result["success"] is True


class TestSetDisplay:
    """Tests for display setting validation."""

    def _make_executor(self):
        executor = SettingsExecutor.__new__(SettingsExecutor)
        executor._bus = MagicMock()
        executor._brain = MagicMock()
        return executor

    def test_night_light_enable(self):
        executor = self._make_executor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = executor._set_display("night_light", True)
        assert result["success"] is True

    def test_color_temperature_out_of_range(self):
        executor = self._make_executor()
        result = executor._set_display("color_temperature", 500)
        assert result["success"] is False

    def test_scaling_out_of_range(self):
        executor = self._make_executor()
        result = executor._set_display("scaling", 5.0)
        assert result["success"] is False

    def test_unknown_display_setting(self):
        executor = self._make_executor()
        result = executor._set_display("resolution", "1920x1080")
        assert result["success"] is False


class TestSetPower:
    """Tests for power setting validation."""

    def _make_executor(self):
        executor = SettingsExecutor.__new__(SettingsExecutor)
        executor._bus = MagicMock()
        executor._brain = MagicMock()
        return executor

    def test_sleep_timeout_out_of_range(self):
        executor = self._make_executor()
        result = executor._set_power("sleep_timeout", 9999)
        assert result["success"] is False

    def test_power_button_invalid_action(self):
        executor = self._make_executor()
        result = executor._set_power("power_button", "explode")
        assert result["success"] is False

    def test_unknown_power_setting(self):
        executor = self._make_executor()
        result = executor._set_power("cpu_frequency", "high")
        assert result["success"] is False


class TestSetInput:
    """Tests for input setting validation."""

    def _make_executor(self):
        executor = SettingsExecutor.__new__(SettingsExecutor)
        executor._bus = MagicMock()
        executor._brain = MagicMock()
        return executor

    def test_mouse_speed_out_of_range(self):
        executor = self._make_executor()
        result = executor._set_input("mouse_speed", 2.0)
        assert result["success"] is False

    def test_unknown_input_setting(self):
        executor = self._make_executor()
        result = executor._set_input("keyboard_layout", "us")
        assert result["success"] is False
