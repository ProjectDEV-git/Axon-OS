"""Tests for search_service.py — _escape_fts5_token, and structural validation."""

import sys
from pathlib import Path

import pytest

# Ensure services/ is on sys.path
_services_dir = str(Path(__file__).resolve().parent.parent / "services")
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

# Mock D-Bus and system dependencies — save originals for cleanup
_originals = {}
_import_keys = ["dbus", "dbus.service", "dbus.exceptions", "dbus.mainloop", "dbus.mainloop.glib",
                "gi", "gi.repository"]
for _key in _import_keys:
    _originals[_key] = sys.modules.get(_key)

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

gi_mock = types.ModuleType("gi")
gi_repo = types.ModuleType("gi.repository")
gi_repo.GLib = type("GLib", (), {"IO_IN": 1, "IO_HUP": 2, "idle_add": staticmethod(lambda f: f), "timeout_add_seconds": staticmethod(lambda s, f: f), "source_remove": staticmethod(lambda x: None)})
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_repo)

from services.axon_search.search_service import SearchService

# Restore original modules after import
for _key, _orig in _originals.items():
    if _orig is not None:
        sys.modules[_key] = _orig
    elif _key in sys.modules:
        del sys.modules[_key]


class TestEscapeFts5Token:
    """Test FTS5 token escaping."""

    def test_strips_quotes(self):
        assert SearchService._escape_fts5_token('he"llo') == "hello"

    def test_strips_stars(self):
        assert SearchService._escape_fts5_token("test*") == "test"

    def test_strips_parens(self):
        assert SearchService._escape_fts5_token("foo(bar)") == "foobar"

    def test_rejects_or_keyword(self):
        assert SearchService._escape_fts5_token("OR") == ""

    def test_rejects_or_lowercase(self):
        assert SearchService._escape_fts5_token("or") == ""

    def test_clean_token_unchanged(self):
        assert SearchService._escape_fts5_token("hello") == "hello"

    def test_strips_colon(self):
        assert SearchService._escape_fts5_token("key:value") == "keyvalue"

    def test_empty_string(self):
        assert SearchService._escape_fts5_token("") == ""
