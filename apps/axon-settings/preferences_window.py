"""Preferences window for Axon Settings.

Holds the switches that don't fit the natural-language assistant: how
Spaces move new windows, and the per-app rules the user has saved from
the "Always do this" / "Never move this app" notifications.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

# Shared GTK helpers live in apps/shared next to every app
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from axon_gtk_utils import fit_default_size

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_settings import describe_rule, load_shell_settings


class PreferencesWindow(Adw.ApplicationWindow):
    """Axon preferences: Spaces (and, later, privacy and command approvals)."""

    def __init__(self, shell_settings: Gio.Settings | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("Axon Preferences")
        fit_default_size(self, 560, 620)
        self._shell = shell_settings if shell_settings is not None else load_shell_settings()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)

        self._page = Adw.PreferencesPage()
        toolbar.set_content(self._page)
        self._page.add(self._build_spaces_group())

    # -- Spaces ------------------------------------------------------------

    def _build_spaces_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Spaces")
        if self._shell is None:
            group.set_description(
                "These settings need the Axon Shell extension, which isn't installed here."
            )
            return group

        route = Adw.SwitchRow(
            title="Move new windows to their Space",
            subtitle="Axon picks a Space for each new window and asks when it isn't sure",
        )
        self._shell.bind("auto-route-windows", route, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(route)

        osd = Adw.SwitchRow(
            title="AI summary when switching Spaces",
            subtitle="Sends window titles to the selected model; off shows the window list",
        )
        self._shell.bind("osd-ai-summary", osd, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(osd)

        self._rules_row = Adw.ExpanderRow(title="Per-app rules")
        group.add(self._rules_row)
        self._rule_rows: list[Gtk.Widget] = []
        self._shell.connect("changed::space-rules", lambda *_a: self._refresh_rules())
        self._refresh_rules()
        return group

    def _rules(self) -> dict[str, str]:
        assert self._shell is not None
        return dict(self._shell.get_value("space-rules").unpack())

    def _refresh_rules(self) -> None:
        for row in self._rule_rows:
            self._rules_row.remove(row)
        self._rule_rows = []
        rules = self._rules()
        self._rules_row.set_subtitle(
            f"{len(rules)} saved" if rules else "None yet. Use “Always do this” on a notification."
        )
        for key in sorted(rules):
            title, subtitle = describe_rule(key, rules[key])
            row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=subtitle)
            remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_tooltip_text("Forget this rule")
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Forget rule for {title}"])
            remove.connect("clicked", lambda _b, k=key: self._remove_rule(k))
            row.add_suffix(remove)
            self._rules_row.add_row(row)
            self._rule_rows.append(row)

    def _remove_rule(self, key: str) -> None:
        assert self._shell is not None
        rules = self._rules()
        rules.pop(key, None)
        self._shell.set_value("space-rules", GLib.Variant("a{ss}", rules))
