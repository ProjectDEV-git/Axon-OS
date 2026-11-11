"""panel_actions.py — Error bubbles and the action card (run command / open app) under an answer.

Mixed into ``AIPanelWindow`` (see ``panel.py``); methods rely on its widgets/state.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Importing panel_compat first puts services/ and apps/shared on sys.path.
import panel_compat  # noqa: F401
from gi.repository import Gio, Gtk

# isort: split
from axon_gtk_utils import announce
from command_risk import assess
from i18n import _
from panel_bubble import MessageBubble
from ui_logic import (
    announcement,
    describe_action,
    is_game_mode_error,
)


class ActionsMixin:
    """Error bubbles and the action card (run command / open app) under an answer."""

    def _show_bubble_error(self, bubble: MessageBubble, message: str, offline: bool) -> None:
        """Turn *bubble* into an error with recovery buttons."""
        bubble.set_error(message)
        announce(self, announcement("error", message), important=True)
        prompt = self._stream_prompt
        if is_game_mode_error(message):
            # The AI is paused for a game: retrying would fail the same way
            bubble.add_action(_("Run anyway"), lambda: self._run_anyway(bubble, prompt))
            return
        bubble.add_action(_("Try again"), lambda: self._retry(bubble, prompt))
        if offline:
            bubble.add_action(_("Diagnostics"), self._open_diagnostics)

    def _show_action_card(self, bubble: MessageBubble, action: dict[str, Any]) -> None:
        """Show a proposed action as a card instead of its JSON.

        Nothing runs from the card itself: commands go through the allowlist
        and the confirmation card (confirm_and_exec), apps launch by desktop id.
        """
        bubble.set_final_text(describe_action(action))
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("action-card")
        kind = action.get("action")
        if kind == "run_command":
            command = str(action.get("command", ""))
            risk = assess(command)
            command_label = Gtk.Label(xalign=0.0)
            command_label.set_text(command)  # plain text, never markup
            command_label.set_selectable(True)
            command_label.set_wrap(True)
            command_label.add_css_class("monospace")
            card.append(command_label)
            risk_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            risk_row.append(Gtk.Image.new_from_icon_name(self._RISK_ICONS[risk.level]))
            risk_row.append(
                Gtk.Label(label=f"{risk.label}. {risk.description}", xalign=0.0, wrap=True)
            )
            card.append(risk_row)
            run = Gtk.Button(label=_("Review and Run…"))
            run.set_tooltip_text(_("Opens the confirmation card first"))
            run.connect("clicked", lambda _b: self._run_card_command(bubble, card, command))
            primary = run
        elif kind == "open_app":
            name = str(action.get("app", ""))
            primary = Gtk.Button(label=_("Open {app}").format(app=name))
            primary.connect("clicked", lambda _b: self._open_card_app(bubble, card, name))
        else:
            bubble.set_final_text(_("Axon suggested an action it can't perform here."))
            return
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        dismiss = Gtk.Button(label=_("Dismiss"))
        dismiss.add_css_class("flat")
        dismiss.connect("clicked", lambda _b: card.set_visible(False))
        primary.add_css_class("suggested-action")
        buttons.append(dismiss)
        buttons.append(primary)
        card.append(buttons)
        bubble.append_widget(card)

    def _card_result(self, bubble: MessageBubble, card: Gtk.Box, text: str) -> bool:
        card.set_visible(False)
        announce(self, announcement("command", text))
        result = Gtk.Label(xalign=0.0)
        result.set_text(text)
        result.set_selectable(True)
        result.set_wrap(True)
        result.add_css_class("monospace")
        bubble.append_widget(result)
        return False

    def _open_card_app(self, bubble: MessageBubble, card: Gtk.Box, name: str) -> None:
        launched = False
        for app_id in (name, f"{name}.desktop"):
            try:
                info = Gio.DesktopAppInfo.new(app_id)
            except TypeError:
                info = None
            if info is not None:
                launched = bool(info.launch([], None))
                break
        self._card_result(
            bubble,
            card,
            (_("Opened {app}.") if launched else _("No installed app called “{app}”.")).format(
                app=name
            ),
        )

    def _open_diagnostics(self) -> None:
        """Open the Diagnostics window in Axon Settings, if installed."""
        self._open_settings_action("diagnostics")

    @staticmethod
    def _open_settings_action(action: str) -> None:
        """Run an Axon Settings desktop action ("diagnostics", "preferences")."""
        try:
            info = Gio.DesktopAppInfo.new("axon-settings.desktop")
            if info is not None:
                info.launch_action(action, None)
        except Exception:
            pass
