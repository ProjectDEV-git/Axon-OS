"""Small GTK helpers shared by the Axon apps.

Apps add ``apps/shared`` to ``sys.path`` and import this module; it sits next
to the apps in the source tree, the .deb and install.sh layouts.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

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
    item = monitors.get_item(0) if monitors.get_n_items() else None
    if item is None:
        return 0, 0
    monitor = cast(Gdk.Monitor, item)
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
BASE_CSS = "axon-base.css"
ACCENT_DIR = SHARED_DIR / "accents"
DEFAULT_ACCENT = "blue"
_tokens_provider: Gtk.CssProvider | None = None
_accent_provider: Gtk.CssProvider | None = None
_accent_loaded = ""
_accent_settings: Any = None
_tokens_variant = ""
_loaded_app_css: set[str] = set()


def token_variant(dark: bool, high_contrast: bool) -> str:
    """Token file variant for the system appearance."""
    if high_contrast:
        return "high-contrast"
    return "dark" if dark else "light"


def accent_file(accent: str, variant: str) -> Path:
    """The GTK accent override for *accent*; unknown names fall back to blue."""
    path = ACCENT_DIR / f"axon-accent-{accent}-{variant}.css"
    if not path.is_file():
        path = ACCENT_DIR / f"axon-accent-{DEFAULT_ACCENT}-{variant}.css"
    return path


def _accent_setting() -> str:
    """The accent chosen in Axon Preferences (the Shell's accent-color key)."""
    global _accent_settings
    if _accent_settings is None:
        try:
            sys.path.insert(0, str(SHARED_DIR.parent / "axon-settings"))
            from shell_settings import load_shell_settings

            _accent_settings = load_shell_settings() or False
            if _accent_settings:
                _accent_settings.connect("changed::accent-color", _apply_token_variant)
        except Exception:  # no schema: keep the default accent
            _accent_settings = False
    if not _accent_settings:
        return DEFAULT_ACCENT
    try:
        return str(_accent_settings.get_string("accent-color"))
    except Exception:  # an older schema without the key
        return DEFAULT_ACCENT


def _apply_token_variant(*_args: object) -> None:
    """(Re)load the token and accent files that match the appearance right now."""
    global _tokens_variant, _accent_loaded
    if _tokens_provider is None:
        return
    manager = Adw.StyleManager.get_default()
    variant = token_variant(manager.get_dark(), manager.get_high_contrast())
    if variant != _tokens_variant:
        _tokens_provider.load_from_path(str(SHARED_DIR / TOKEN_FILES[variant]))
        _tokens_variant = variant
    if _accent_provider is not None:
        path = accent_file(_accent_setting(), variant)
        if str(path) != _accent_loaded:
            _accent_provider.load_from_path(str(path))
            _accent_loaded = str(path)


def install_axon_style(display: Gdk.Display | None = None) -> None:
    """Install the design tokens, the accent and the shared base style once.

    Every Axon window calls this (load_app_css does it for apps with their
    own stylesheet). The token variant follows the system appearance through
    Adw.StyleManager (light, dark, high contrast) and the accent follows Axon
    Preferences; both are swapped live when they change.
    """
    global _tokens_provider, _accent_provider
    display = display or Gdk.Display.get_default()
    if display is None or _tokens_provider is not None:
        return
    _tokens_provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(
        display, _tokens_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    # Added after the tokens, so its @define-color lines win
    _accent_provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(
        display, _accent_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _apply_token_variant()
    # Shared press feedback and type details, under each app's own CSS
    base = Gtk.CssProvider()
    base.load_from_path(str(SHARED_DIR / BASE_CSS))
    Gtk.StyleContext.add_provider_for_display(
        display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    manager = Adw.StyleManager.get_default()
    manager.connect("notify::dark", _apply_token_variant)
    manager.connect("notify::high-contrast", _apply_token_variant)


def load_app_css(css_path: Path, display: Gdk.Display | None = None) -> None:
    """Install the Axon style (see install_axon_style), then one app stylesheet.

    App CSS refers to colours as ``@axon_*`` (see apps/shared/
    axon-design-tokens.css, generated from theme/tokens/axon-tokens.json).
    Each file loads once.
    """
    display = display or Gdk.Display.get_default()
    if display is None:
        return
    install_axon_style(display)
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


_a11y_bus: bool | None = None


def accessibility_bus_available() -> bool:
    """True when the AT-SPI accessibility bus answers (cached).

    GTK 4.14's announce() crashes when there is no accessibility bus (seen
    with no session bus at all), so announcements are only sent when the bus
    is there and GTK_A11Y isn't "none".
    """
    global _a11y_bus
    if _a11y_bus is None:
        _a11y_bus = False
        if os.environ.get("GTK_A11Y", "") != "none":
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                bus.call_sync(
                    "org.a11y.Bus",
                    "/org/a11y/bus",
                    "org.a11y.Bus",
                    "GetAddress",
                    None,
                    GLib.VariantType.new("(s)"),
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                )
                _a11y_bus = True
            except Exception:
                _a11y_bus = False
    return _a11y_bus


def announce(widget: Gtk.Widget, text: str, important: bool = False) -> None:
    """Ask screen readers to read *text* (GTK 4.14+; a no-op on older GTK)."""
    if not text or not hasattr(widget, "announce") or not accessibility_bus_available():
        return
    priority = (
        Gtk.AccessibleAnnouncementPriority.HIGH
        if important
        else Gtk.AccessibleAnnouncementPriority.MEDIUM
    )
    widget.announce(text, priority)


def allow_cpu_only(callback: Callable[[bool], None], seconds: int = 120) -> None:
    """Ask Brain to allow the Speed model on the CPU while game mode has the AI paused.

    This is "Run anyway": it never uses the GPU. *callback* gets True once Brain
    agreed, on the GTK thread; the call itself does not block.
    """

    def done(conn: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            ok = bool(conn.call_finish(result).unpack()[0])
        except GLib.Error:
            ok = False
        callback(ok)

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(
            "org.axonos.Brain",
            "/org/axonos/Brain",
            "org.axonos.Brain",
            "AllowCpuOnly",
            GLib.Variant("(u)", (seconds,)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            done,
        )
    except GLib.Error:
        callback(False)
