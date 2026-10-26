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
from axon_gtk_utils import close_on_escape, fit_default_size, install_axon_style

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_settings import describe_rule, load_shell_settings

# services/ is beside apps/ (source tree, .deb) or inside the install dir.
for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        sys.path.insert(0, str(_base / "services"))
        break
import command_approvals
import context_settings
from i18n import N_, _

# Accent swatches: (setting value, name, fill). The fills are the accents'
# "accent" colour in theme/tokens/axon-tokens.json; a test keeps them in sync.
ACCENTS: list[tuple[str, str, str]] = [
    ("blue", N_("Blue"), "#2b6fde"),
    ("teal", N_("Teal"), "#1d7c79"),
    ("green", N_("Green"), "#2a7e49"),
    ("orange", N_("Orange"), "#b35914"),
    ("pink", N_("Pink"), "#ca2f7d"),
    ("graphite", N_("Graphite"), "#6a717c"),
]
# GNOME 47+ has its own accent key; Axon's graphite is GNOME's slate
GNOME_ACCENTS = {"graphite": "slate"}

_SWATCH_CSS = "".join(
    f".accent-swatch-{key} {{ background: {fill}; min-width: 28px; min-height: 28px; "
    "padding: 0; border-radius: 9999px; }\n"
    f".accent-swatch-{key}:checked {{ box-shadow: 0 0 0 2px @window_bg_color, "
    f"0 0 0 4px {fill}; }}\n"
    for key, _name, fill in ACCENTS
)


def gnome_accent_settings() -> Gio.Settings | None:
    """org.gnome.desktop.interface when it has accent-color (GNOME 47+), else None."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.interface", True) if source else None
    if schema is None or not schema.has_key("accent-color"):
        return None
    return Gio.Settings.new_full(schema, None, None)


class PreferencesWindow(Adw.ApplicationWindow):
    """Axon preferences: Spaces (and, later, privacy and command approvals)."""

    def __init__(
        self,
        shell_settings: Gio.Settings | None = None,
        context_path: Path | None = None,
        approvals_path: Path | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        install_axon_style(self.get_display())
        self.set_title(_("Axon Preferences"))
        fit_default_size(self, 560, 620)
        self._shell = shell_settings if shell_settings is not None else load_shell_settings()
        self._context_path = context_path or context_settings.CONFIG_PATH
        self._approvals_path = approvals_path or command_approvals.APPROVALS_PATH

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)

        close_on_escape(self)
        self._page = Adw.PreferencesPage()
        toolbar.set_content(self._page)
        self._page.add(self._build_privacy_group())
        self._page.add(self._build_approvals_group())
        self._page.add(self._build_spaces_group())
        self._page.add(self._build_appearance_group())

    # -- Privacy: what context the AI receives ------------------------------

    _CONTEXT_ROWS = (  # (settings field, title, subtitle)
        ("share_active_app", "Active app", "The focused window's title and the current Space"),
        ("share_open_files", "Open files", "Names of files open in editors"),
        ("share_terminal_history", "Terminal history", "Recent commands and the last error"),
        ("share_clipboard", "Clipboard", "Recent clipboard entries"),
    )

    def _build_privacy_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=_("Context sent to the AI"),
            description=_(
                "Desktop context helps answers fit what you are doing. With a cloud "
                "model it leaves this computer, and Axon asks before the first time."
            ),
        )
        settings = context_settings.load_settings(self._context_path)
        self._context_master = Adw.SwitchRow(title=_("Send desktop context"))
        self._context_master.set_active(settings.share_context)
        group.add(self._context_master)
        self._context_switches: dict[str, Adw.SwitchRow] = {}
        for field, title, subtitle in self._CONTEXT_ROWS:
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(bool(getattr(settings, field)))
            row.set_sensitive(settings.share_context)
            row.connect("notify::active", self._on_context_changed)
            group.add(row)
            self._context_switches[field] = row
        self._context_master.connect("notify::active", self._on_context_changed)
        return group

    def _on_context_changed(self, *_args: object) -> None:
        master = self._context_master.get_active()
        for row in self._context_switches.values():
            row.set_sensitive(master)
        values = {f: row.get_active() for f, row in self._context_switches.items()}
        try:
            context_settings.save_settings(
                context_settings.ContextSettings(share_context=master, **values),
                self._context_path,
            )
        except OSError:
            pass  # read-only home: the switches still show the choice

    # -- Commands that run without asking ---------------------------------

    def _build_approvals_group(self) -> Adw.PreferencesGroup:
        self._approvals_group = Adw.PreferencesGroup(
            title=_("Commands that run without asking"),
            description=_(
                "Read-only commands you chose to “Always allow” on the "
                "confirmation card. Everything else always asks."
            ),
        )
        clear = Gtk.Button(label=_("Clear All"))
        clear.add_css_class("flat")
        clear.set_valign(Gtk.Align.CENTER)
        clear.connect("clicked", lambda _b: self._clear_approvals())
        self._approvals_group.set_header_suffix(clear)
        self._approval_rows: list[Gtk.Widget] = []
        self._refresh_approvals()
        return self._approvals_group

    def _refresh_approvals(self) -> None:
        for row in self._approval_rows:
            self._approvals_group.remove(row)
        self._approval_rows = []
        commands = command_approvals.load_approvals(self._approvals_path)
        if not commands:
            empty = Adw.ActionRow(title=_("None"))
            self._approvals_group.add(empty)
            self._approval_rows.append(empty)
            return
        for command in commands:
            row = Adw.ActionRow(title=command)
            row.set_use_markup(False)
            row.add_css_class("monospace")
            remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_tooltip_text(_("Ask again for this command"))
            remove.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [_("Stop allowing {command}").format(command=command)],
            )
            remove.connect("clicked", lambda _b, c=command: self._remove_approval(c))
            row.add_suffix(remove)
            self._approvals_group.add(row)
            self._approval_rows.append(row)

    def _remove_approval(self, command: str) -> None:
        command_approvals.remove_approval(command, self._approvals_path)
        self._refresh_approvals()

    def _clear_approvals(self) -> None:
        command_approvals.clear_approvals(self._approvals_path)
        self._refresh_approvals()

    # -- Spaces ------------------------------------------------------------

    def _build_spaces_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Spaces"))
        if self._shell is None:
            group.set_description(
                _("These settings need the Axon Shell extension, which isn't installed here.")
            )
            return group

        route = Adw.SwitchRow(
            title=_("Move new windows to their Space"),
            subtitle=_("Axon picks a Space for each new window and asks when it isn't sure"),
        )
        self._shell.bind("auto-route-windows", route, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(route)

        osd = Adw.SwitchRow(
            title=_("AI summary when switching Spaces"),
            subtitle=_("Sends window titles to the selected model; off shows the window list"),
        )
        self._shell.bind("osd-ai-summary", osd, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(osd)

        self._rules_row = Adw.ExpanderRow(title=_("Per-app rules"))
        group.add(self._rules_row)
        self._rule_rows: list[Gtk.Widget] = []
        self._shell.connect("changed::space-rules", lambda *_a: self._refresh_rules())
        self._refresh_rules()
        return group

    # -- Appearance --------------------------------------------------------

    def _build_appearance_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Appearance"))
        if self._shell is None:
            group.set_description(
                _("These settings need the Axon Shell extension, which isn't installed here.")
            )
            return group
        group.add(self._build_accent_row())

        solid = Adw.SwitchRow(
            title=_("Reduce transparency"),
            subtitle=_("Draw the taskbar on a solid surface. High Contrast does this too"),
        )
        self._shell.bind("reduce-transparency", solid, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(solid)
        self._reduce_transparency_row = solid
        return group

    def _build_accent_row(self) -> Adw.ActionRow:
        """Round swatches, like macOS and GNOME 47: one choice, applied at once."""
        assert self._shell is not None
        provider = Gtk.CssProvider()
        provider.load_from_string(_SWATCH_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2
        )
        row = Adw.ActionRow(
            title=_("Accent colour"),
            subtitle=_("Focus rings, selections and the main action"),
        )
        box = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER)
        current = self._shell.get_string("accent-color")
        self._accent_buttons: dict[str, Gtk.ToggleButton] = {}
        first: Gtk.ToggleButton | None = None
        for key, name, _fill in ACCENTS:
            button = Gtk.ToggleButton()
            button.add_css_class(f"accent-swatch-{key}")
            button.set_tooltip_text(_(name))
            button.update_property([Gtk.AccessibleProperty.LABEL], [_(name)])
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.set_active(key == current)
            button.connect("toggled", self._on_accent_toggled, key)
            box.append(button)
            self._accent_buttons[key] = button
        row.add_suffix(box)
        return row

    def _on_accent_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active() or self._shell is None:
            return
        self._shell.set_string("accent-color", key)
        gnome = gnome_accent_settings()
        if gnome is not None:
            gnome.set_string("accent-color", GNOME_ACCENTS.get(key, key))

    def _rules(self) -> dict[str, str]:
        assert self._shell is not None
        return dict(self._shell.get_value("space-rules").unpack())

    def _refresh_rules(self) -> None:
        for row in self._rule_rows:
            self._rules_row.remove(row)
        self._rule_rows = []
        rules = self._rules()
        self._rules_row.set_subtitle(
            _("{count} saved").format(count=len(rules))
            if rules
            else _("None yet. Use “Always do this” on a notification.")
        )
        for key in sorted(rules):
            title, subtitle = describe_rule(key, rules[key])
            row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=subtitle)
            remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_tooltip_text(_("Forget this rule"))
            remove.update_property(
                [Gtk.AccessibleProperty.LABEL], [_("Forget rule for {app}").format(app=title)]
            )
            remove.connect("clicked", lambda _b, k=key: self._remove_rule(k))
            row.add_suffix(remove)
            self._rules_row.add_row(row)
            self._rule_rows.append(row)

    def _remove_rule(self, key: str) -> None:
        assert self._shell is not None
        rules = self._rules()
        rules.pop(key, None)
        self._shell.set_value("space-rules", GLib.Variant("a{ss}", rules))
