"""Helpers for GTK smoke tests.

GTK4 needs a display. These helpers skip the calling test module when GTK,
libadwaita or a display is missing (CI has the libraries but no display), so
the smoke tests only run where they can, e.g. under ``xvfb-run``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


def require_gtk() -> tuple[Any, Any, Any]:
    """Return ``(Gtk, Adw, GLib)`` or skip the module if GTK can't start."""
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Adw, Gdk, GLib, Gtk
    except (ImportError, ValueError) as exc:
        pytest.skip(f"GTK4/libadwaita not available: {exc}", allow_module_level=True)
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        pytest.skip("no display for GTK", allow_module_level=True)
    Adw.init()
    return Gtk, Adw, GLib


def load_module(name: str, rel_path: str) -> ModuleType:
    """Import a file from a hyphenated app directory under *name*."""
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pump(glib: Any, rounds: int = 50) -> None:
    """Run pending main-loop work (idle callbacks, redraws)."""
    ctx = glib.MainContext.default()
    for _ in range(rounds):
        if not ctx.iteration(False):
            break


def load_intent_bar_window() -> ModuleType:
    """Import ``apps/intent-bar/ui/window.py`` with its package-relative imports."""
    import importlib
    import types

    for name, path in (
        ("intent_bar", ROOT / "apps" / "intent-bar"),
        ("intent_bar.ui", ROOT / "apps" / "intent-bar" / "ui"),
    ):
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(path)]
            sys.modules[name] = pkg
    return importlib.import_module("intent_bar.ui.window")
