"""Tests for GUI Agent _validate_app_name security fix."""

import re

# Import the validation function directly since the module has D-Bus deps
import sys
import types

# We need to mock dbus before importing
import pytest


@pytest.fixture(autouse=True)
def mock_dbus(monkeypatch):
    """Mock dbus module so gui_agent_service can be imported without D-Bus."""
    dbus_mock = types.ModuleType("dbus")
    dbus_mock.service = types.ModuleType("dbus.service")
    dbus_mock.service.method = lambda *a, **kw: lambda f: f
    dbus_mock.service.signal = lambda *a, **kw: lambda f: f
    dbus_mock.service.Object = type("Object", (), {})
    dbus_mock.service.BusName = type("BusName", (), {"__init__": lambda *a, **kw: None})
    dbus_mock.exceptions = types.ModuleType("dbus.exceptions")
    dbus_mock.exceptions.DBusException = type("DBusException", (Exception,), {})
    dbus_mock.mainloop = types.ModuleType("dbus.mainloop")
    dbus_mock.mainloop.glib = types.ModuleType("dbus.mainloop.glib")
    dbus_mock.mainloop.glib.DBusGMainLoop = lambda **kw: None
    sys.modules.setdefault("dbus", dbus_mock)
    sys.modules.setdefault("dbus.service", dbus_mock.service)
    sys.modules.setdefault("dbus.exceptions", dbus_mock.exceptions)
    sys.modules.setdefault("dbus.mainloop", dbus_mock.mainloop)
    sys.modules.setdefault("dbus.mainloop.glib", dbus_mock.mainloop.glib)


def _import_validate():
    """Import _validate_app_name from gui_agent_service."""
    from services.axon_gui_agent.gui_agent_service import _validate_app_name

    return _validate_app_name


class TestValidateAppName:
    """Test that _validate_app_name blocks injection and path traversal."""

    def test_valid_app_names(self):
        validate = _import_validate()
        assert validate("firefox") == "firefox"
        assert validate("org.gnome.TextEditor") == "org.gnome.TextEditor"
        assert validate("nautilus") == "nautilus"
        assert validate("code-oss") == "code-oss"
        assert validate("app_v2") == "app_v2"

    def test_strips_whitespace(self):
        validate = _import_validate()
        assert validate("  firefox  ") == "firefox"

    def test_rejects_empty(self):
        validate = _import_validate()
        assert validate("") is None
        assert validate("   ") is None

    def test_rejects_too_long(self):
        validate = _import_validate()
        assert validate("a" * 129) is None
        assert validate("a" * 128) == "a" * 128

    def test_rejects_path_traversal(self):
        validate = _import_validate()
        assert validate("../etc/passwd") is None
        assert validate("../../bin/sh") is None
        assert validate("/usr/bin/evil") is None

    def test_rejects_shell_metacharacters(self):
        validate = _import_validate()
        assert validate("app;rm -rf /") is None
        assert validate("app|cat /etc/passwd") is None
        assert validate("app$(whoami)") is None
        assert validate("app`id`") is None
        assert validate("app&bg") is None

    def test_rejects_spaces(self):
        validate = _import_validate()
        assert validate("my app name") is None

    def test_must_start_with_alphanumeric(self):
        validate = _import_validate()
        assert validate(".hidden") is None
        assert validate("-bad") is None
        assert validate("_bad") is None
        assert validate("good-app") == "good-app"
