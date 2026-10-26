"""Small GTK helpers shared by the Axon apps.

Apps add ``apps/shared`` to ``sys.path`` and import this module; it sits next
to the apps in the source tree, the .deb and install.sh layouts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

# services/ is beside apps/ (source tree, .deb) or inside the install dir.
for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        if str(_base / "services") not in sys.path:
            sys.path.insert(0, str(_base / "services"))
        break
from ui_logic import fit_to_screen


def monitor_size(display: Gdk.Display | None = None) -> tuple[int, int]:
    """Logical size of the first monitor, or (0, 0) when unknown.

    GTK4 has no portable work-area API (Wayland hides it), so callers keep a
    margin for panels and docks.
    """
    display = display or Gdk.Display.get_default()
    if display is None:
        return 0, 0
    monitors = display.get_monitors()
    monitor = monitors.get_item(0) if monitors.get_n_items() else None
    if monitor is None:
        return 0, 0
    geometry = monitor.get_geometry()
    return int(geometry.width), int(geometry.height)


def fit_default_size(window: Gtk.Window, width: int, height: int) -> tuple[int, int]:
    """``set_default_size`` clamped so the window fits a small screen.

    Targets include 1366×768 at 100% and 1280×800 at 125% (1024×640 logical).
    """
    mon_w, mon_h = monitor_size(window.get_display())
    size = (fit_to_screen(width, mon_w), fit_to_screen(height, mon_h))
    window.set_default_size(*size)
    return size


# ── Shared stylesheet loading ──────────────────────────────────────────────

SHARED_DIR = Path(__file__).resolve().parent
TOKEN_FILES = {
    "dark": "axon-design-tokens.css",
    "light": "axon-design-tokens-light.css",
    "high-contrast": "axon-design-tokens-hc.css",
}
_tokens_provider: Gtk.CssProvider | None = None
_tokens_variant = ""
_loaded_app_css: set[str] = set()


def token_variant(dark: bool, high_contrast: bool) -> str:
    """Token file variant for the system appearance."""
    if high_contrast:
        return "high-contrast"
    return "dark" if dark else "light"


def _apply_token_variant(*_args: object) -> None:
    """(Re)load the token file that matches Adw.StyleManager right now."""
    global _tokens_variant
    if _tokens_provider is None:
        return
    manager = Adw.StyleManager.get_default()
    variant = token_variant(manager.get_dark(), manager.get_high_contrast())
    if variant != _tokens_variant:
        _tokens_provider.load_from_path(str(SHARED_DIR / TOKEN_FILES[variant]))
        _tokens_variant = variant


def load_app_css(css_path: Path, display: Gdk.Display | None = None) -> None:
    """Load the design tokens, then one app stylesheet that uses them.

    App CSS refers to colours as ``@axon_*`` (see apps/shared/
    axon-design-tokens.css, generated from theme/tokens/axon-tokens.json).
    The token variant follows the system appearance through
    Adw.StyleManager (light, dark, high contrast) and is swapped live when
    it changes; app CSS stays loaded. Each file loads once.
    """
    global _tokens_provider
    display = display or Gdk.Display.get_default()
    if display is None:
        return
    if _tokens_provider is None:
        _tokens_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _tokens_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        _apply_token_variant()
        manager = Adw.StyleManager.get_default()
        manager.connect("notify::dark", _apply_token_variant)
        manager.connect("notify::high-contrast", _apply_token_variant)
    key = str(css_path)
    if key in _loaded_app_css:
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(key)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )
    _loaded_app_css.add(key)


def close_on_escape(window: Gtk.Window) -> None:
    """Let Escape close a secondary window, as GNOME dialogs do."""
    keys = Gtk.EventControllerKey()

    def on_key(_ctrl: Gtk.EventControllerKey, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_Escape:
            window.close()
            return True
        return False

    keys.connect("key-pressed", on_key)
    window.add_controller(keys)
