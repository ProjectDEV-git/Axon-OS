"""Tests for apps/shared/axon_gtk_utils.py (skipped without a display)."""

from __future__ import annotations

from tests.gtk_helpers import load_module, require_gtk

Gtk, Adw, GLib = require_gtk()
utils = load_module("axon_gtk_utils", "apps/shared/axon_gtk_utils.py")


def test_fit_default_size_clamps_to_monitor() -> None:
    win = Gtk.Window()
    mon_w, mon_h = utils.monitor_size(win.get_display())
    assert mon_w > 0 and mon_h > 0
    width, height = utils.fit_default_size(win, 5000, 5000)
    assert (width, height) == (mon_w - 48, mon_h - 48)
    assert win.get_default_size() == (width, height)
    assert utils.fit_default_size(win, 400, 300) == (400, 300)  # small sizes are kept
    win.destroy()


def test_token_variant() -> None:
    assert utils.token_variant(dark=True, high_contrast=False) == "dark"
    assert utils.token_variant(dark=False, high_contrast=False) == "light"
    assert utils.token_variant(dark=True, high_contrast=True) == "high-contrast"


def test_app_css_follows_light_and_dark(tmp_path) -> None:
    css = tmp_path / "app.css"
    css.write_text("label.axon-test { color: @axon_fg; }")
    utils.load_app_css(css)
    manager = Adw.StyleManager.get_default()
    win = Gtk.Window()
    label = Gtk.Label(label="x")
    label.add_css_class("axon-test")
    win.set_child(label)
    win.present()

    def fg() -> str:
        from tests.gtk_helpers import pump

        pump(GLib)
        return label.get_color().to_string()

    manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    light = fg()
    manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    dark = fg()
    manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
    assert light == "rgb(22,22,22)"  # tokens: light fg #161616
    assert dark == "rgb(242,242,242)"  # tokens: dark fg #f2f2f2
    win.destroy()


def test_announce_is_skipped_without_an_accessibility_bus(monkeypatch) -> None:
    calls: list[str] = []

    class W:
        def announce(self, text: str, _priority: object) -> None:
            calls.append(text)

    monkeypatch.setattr(utils, "_a11y_bus", False)
    utils.announce(W(), "hello")
    assert calls == []
    monkeypatch.setattr(utils, "_a11y_bus", True)
    utils.announce(W(), "hello")
    utils.announce(W(), "")
    assert calls == ["hello"]
