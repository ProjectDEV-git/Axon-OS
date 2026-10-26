#!/usr/bin/env python3
"""Axon command confirmation card.

Shows a command the AI wants to run, its risk level and known effects, and
asks the user to allow it once (or always, for the lowest risk level only).
Used by ``services/service_utils.confirm_command`` and the Shell extension
in place of a plain ``zenity --question``.

Exit status 0 means "run it". Anything else means "don't": Cancel, closing
the window, the timeout, a command the allowlist rejects, or any error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# services/ is beside apps/ (source tree, .deb) or inside the install dir.
for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        sys.path.insert(0, str(_base / "services"))
        break

from command_approvals import add_approval, is_approved
from command_risk import (
    CHANGES_FILES,
    DELETES_FILES,
    NEEDS_ROOT,
    READ_ONLY,
    CommandRisk,
    assess,
    can_always_allow,
)

APPROVED = 0
DECLINED = 1

LEVEL_ICONS = {
    READ_ONLY: "dialog-information-symbolic",
    CHANGES_FILES: "document-edit-symbolic",
    DELETES_FILES: "user-trash-symbolic",
    NEEDS_ROOT: "dialog-password-symbolic",
}

_CSS = b"""
.command-text {
    font-family: monospace;
    padding: 10px 12px;
}
.risk-read_only { color: @success_color; }
.risk-changes_files { color: @warning_color; }
.risk-deletes_files, .risk-needs_root { color: @error_color; }
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True, help="exact command to show")
    parser.add_argument("--context", default="", help="optional line shown above it")
    parser.add_argument("--timeout", type=int, default=60, help="seconds before it says no")
    return parser.parse_args(argv)


def build_window(app: object, command: str, context: str, timeout: int) -> object:
    """Create the confirmation window. Its ``result`` stays DECLINED until a click."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, GLib, Gtk, Pango

    risk: CommandRisk = assess(command)

    class ConfirmWindow(Adw.ApplicationWindow):
        def __init__(self) -> None:
            super().__init__(application=app)
            self.result = DECLINED  # fail closed: only a click changes this
            self._remaining = max(1, timeout)
            self.set_title("Run this command?")
            self.set_default_size(460, -1)
            self.set_resizable(False)

            provider = Gtk.CssProvider()
            provider.load_from_data(_CSS)
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )

            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
            self.set_content(toolbar)

            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            body.set_margin_start(18)
            body.set_margin_end(18)
            body.set_margin_bottom(18)
            toolbar.set_content(body)

            heading = Gtk.Label(label="Axon AI wants to run this command", xalign=0.0)
            heading.add_css_class("title-3")
            body.append(heading)
            if context:
                body.append(self._wrap_label(context, "dim-label"))

            # The exact command, as plain text (never markup), selectable
            self.command_label = self._wrap_label(command, "command-text")
            self.command_label.set_selectable(True)
            frame = Gtk.Frame()
            frame.add_css_class("card")
            frame.set_child(self.command_label)
            body.append(frame)

            details = Gtk.ListBox()
            details.add_css_class("boxed-list")
            details.set_selection_mode(Gtk.SelectionMode.NONE)
            risk_row = Adw.ActionRow(title=risk.label, subtitle=risk.description)
            risk_row.set_use_markup(False)
            icon = Gtk.Image.new_from_icon_name(LEVEL_ICONS[risk.level])
            icon.add_css_class(f"risk-{risk.level}")
            risk_row.add_prefix(icon)
            details.append(risk_row)
            admin = Adw.ActionRow(
                title="Admin rights", subtitle="Needed" if risk.needs_root else "Not needed"
            )
            admin.set_use_markup(False)
            details.append(admin)
            for effect in risk.effects:
                row = Adw.ActionRow(title=effect)
                row.set_use_markup(False)
                details.append(row)
            body.append(details)

            self.always_check: Gtk.CheckButton | None = None
            if can_always_allow(risk):
                self.always_check = Gtk.CheckButton(label="Always allow this exact command")
                body.append(self.always_check)

            self.countdown = Gtk.Label(xalign=0.0)
            self.countdown.add_css_class("dim-label")
            body.append(self.countdown)

            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            buttons.set_halign(Gtk.Align.END)
            self.cancel_btn = Gtk.Button(label="Cancel")
            self.cancel_btn.connect("clicked", lambda _b: self.decline())
            self.allow_btn = Gtk.Button(label="Allow Once")
            self.allow_btn.add_css_class(
                "destructive-action" if risk.level != READ_ONLY else "suggested-action"
            )
            self.allow_btn.connect("clicked", lambda _b: self.approve())
            buttons.append(self.cancel_btn)
            buttons.append(self.allow_btn)
            body.append(buttons)
            if self.always_check is not None:
                self.always_check.connect("toggled", self._on_always_toggled)

            # Cancel has focus, so Enter never approves by accident; Escape cancels
            self.set_default_widget(self.cancel_btn)
            self.cancel_btn.grab_focus()
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_key)
            self.add_controller(keys)

            self._tick()
            self._timer = GLib.timeout_add_seconds(1, self._tick)

        @staticmethod
        def _wrap_label(text: str, css: str) -> Gtk.Label:
            label = Gtk.Label(xalign=0.0)
            label.set_text(text)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.add_css_class(css)
            return label

        def _on_always_toggled(self, check: Gtk.CheckButton) -> None:
            self.allow_btn.set_label("Always Allow" if check.get_active() else "Allow Once")

        def _on_key(self, _ctrl: object, keyval: int, _code: int, _state: object) -> bool:
            if keyval == Gdk.KEY_Escape:
                self.decline()
                return True
            return False

        def _tick(self) -> bool:
            if self._remaining <= 0:
                self._timer = 0
                self.decline()  # timeout counts as "no"
                return False
            self.countdown.set_text(f"Cancels automatically in {self._remaining} s")
            self._remaining -= 1
            return True

        def approve(self) -> None:
            if self.always_check is not None and self.always_check.get_active():
                add_approval(command)
            self.result = APPROVED
            self.close()

        def decline(self) -> None:
            self.result = DECLINED
            self.close()

    return ConfirmWindow()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    command = args.command.strip()
    try:
        from service_utils import validate_command

        if validate_command(command) is None:
            return DECLINED  # the allowlist stays the gate, even here
        if is_approved(command):
            return APPROVED  # "Always allow", still lowest risk
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio

        # NON_UNIQUE: two prompts at once must not share one process
        app = Adw.Application(
            application_id="io.github.axon_os.Confirm", flags=Gio.ApplicationFlags.NON_UNIQUE
        )
        state = {"result": DECLINED}

        def on_activate(application: Adw.Application) -> None:
            window = build_window(application, command, args.context, args.timeout)
            window.connect(
                "close-request",
                lambda w: state.__setitem__("result", w.result) or False,
            )
            window.present()

        app.connect("activate", on_activate)
        app.run([])
        return int(state["result"])
    except Exception as exc:  # any failure means "don't run"
        sys.stderr.write(f"axon-confirm: {exc}\n")
        return DECLINED


if __name__ == "__main__":
    sys.exit(main())
