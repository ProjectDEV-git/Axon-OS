"""Helpers for GTK smoke tests.

GTK4 needs a display. These helpers skip the calling test module when GTK,
libadwaita or a display is missing, so the smoke tests only run where they
can, e.g. under ``xvfb-run`` (as in CI).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The UI font Axon OS sets (font-name in build/config/chroot-setup.sh). Without
# GNOME's settings daemon GTK uses "Sans", which is DejaVu Sans on a bare CI
# runner and much wider, so layout tests would measure a font users never see.
OS_FONT = "Inter 11"


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
    Gtk.Settings.get_default().set_property("gtk-font-name", OS_FONT)
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


class ThreadRecorder:
    """Replaces ``module.threading.Thread`` to keep and later join workers."""

    def __init__(self, module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        import threading

        self.threads: list[threading.Thread] = []
        real = threading.Thread

        def make(*args: Any, **kwargs: Any) -> threading.Thread:
            t = real(*args, **kwargs)
            self.threads.append(t)
            return t

        monkeypatch.setattr(module.threading, "Thread", make)

    def join(self) -> None:
        for t in self.threads:
            t.join(timeout=5)
