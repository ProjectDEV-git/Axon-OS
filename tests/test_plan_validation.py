"""Tests for plan.py — validate_plan, _check_op, and to_gvariant."""

import sys
from pathlib import Path

import pytest

# Ensure services/ is on sys.path
_services_dir = str(Path(__file__).resolve().parent.parent / "services")
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

# Mock D-Bus dependencies before importing
import types

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

from services.axon_gui_agent.plan import to_gvariant, validate_plan, _check_op


class TestToGvariant:
    """Test GVariant serialisation with proper escaping."""

    def test_bool_true(self):
        assert to_gvariant(True) == "true"

    def test_bool_false(self):
        assert to_gvariant(False) == "false"

    def test_integer(self):
        assert to_gvariant(42) == "42"

    def test_float(self):
        assert to_gvariant(3.14) == "3.14"

    def test_string_simple(self):
        assert to_gvariant("hello") == "'hello'"

    def test_string_with_single_quote_injection(self):
        """Single quotes in values must be escaped to prevent injection."""
        result = to_gvariant("hello'; rm -rf /")
        # The escaped result should contain \' not bare '
        assert "\\'" in result
        assert result == "'hello\\'; rm -rf /'"

    def test_string_with_backslash(self):
        """Backslashes must be escaped."""
        result = to_gvariant("path\\to\\file")
        assert result == "'path\\\\to\\\\file'"

    def test_string_with_both(self):
        """Both quotes and backslashes must be escaped."""
        result = to_gvariant("it's a \\test")
        assert result == "'it\\'s a \\\\test'"

    def test_empty_string(self):
        assert to_gvariant("") == "''"

    def test_list(self):
        result = to_gvariant(["a", "b"])
        assert result == "['a', 'b']"


class TestValidatePlan:
    """Test plan validation."""

    def test_valid_simple_plan(self):
        plan = '[{"type": "launch_app", "app": "firefox"}]'
        ops, errors = validate_plan(plan)
        assert len(ops) == 1
        assert errors == []

    def test_invalid_json(self):
        ops, errors = validate_plan("not json")
        assert ops == []
        assert len(errors) == 1

    def test_unknown_op_type(self):
        plan = '[{"type": "execute_code", "code": "import os"}]'
        ops, errors = validate_plan(plan)
        assert ops == []
        assert any("unknown" in e.lower() for e in errors)

    def test_gsettings_missing_value(self):
        plan = '[{"type": "gsettings_set", "schema": "org.gnome.desktop.interface", "key": "font-name"}]'
        ops, errors = validate_plan(plan)
        assert ops == []
        assert any("missing value" in e.lower() for e in errors)

    def test_gsettings_disallowed_schema(self):
        plan = '[{"type": "gsettings_set", "schema": "org.gnome.desktop.lockdown", "key": "disable-command-line", "value": true}]'
        ops, errors = validate_plan(plan)
        assert ops == []
        assert any("not allowed" in e.lower() for e in errors)

    def test_launch_app_empty(self):
        plan = '[{"type": "launch_app", "app": ""}]'
        ops, errors = validate_plan(plan)
        assert ops == []
        assert any("missing app" in e.lower() for e in errors)

    def test_launch_app_metacharacters(self):
        plan = '[{"type": "launch_app", "app": "firefox; rm -rf /"}]'
        ops, errors = validate_plan(plan)
        assert ops == []
        assert any("illegal" in e.lower() for e in errors)

    def test_markdown_fence_stripped(self):
        plan = '```json\n[{"type": "launch_app", "app": "firefox"}]\n```'
        ops, errors = validate_plan(plan)
        assert len(ops) == 1

    def test_max_ops_limit(self):
        import json
        ops_list = [{"type": "notify", "message": f"msg {i}"} for i in range(20)]
        plan = json.dumps(ops_list)
        ops, errors = validate_plan(plan)
        assert len(ops) == 12
        assert any("truncated" in e.lower() for e in errors)


class TestCheckOp:
    """Test individual operation validation."""

    def test_gsettings_valid(self):
        op = {"type": "gsettings_set", "schema": "org.gnome.desktop.interface", "key": "font-name", "value": "Sans 12"}
        assert _check_op(op) is None

    def test_gsettings_semicolon_in_key(self):
        op = {"type": "gsettings_set", "schema": "org.gnome.desktop.interface", "key": "font; evil", "value": "x"}
        assert _check_op(op) is not None

    def test_notify_valid(self):
        op = {"type": "notify", "message": "Hello"}
        assert _check_op(op) is None

    def test_notify_empty_message(self):
        op = {"type": "notify", "message": ""}
        assert _check_op(op) is not None
