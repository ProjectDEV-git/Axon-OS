"""panel_bubble.py — chat message widget and Markdown-ish markup for the AI panel."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
import panel_compat  # noqa: F401  # puts services/ on sys.path
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

# isort: split
from i18n import _


def open_file(widget: Gtk.Widget, path: str) -> None:
    """Open *path* in the user's default app."""
    gfile = Gio.File.new_for_path(path)
    launcher_cls = getattr(Gtk, "FileLauncher", None)
    root = widget.get_root()
    try:
        if launcher_cls is not None:
            launcher_cls.new(gfile).launch(root if isinstance(root, Gtk.Window) else None, None)
        else:
            Gio.AppInfo.launch_default_for_uri(gfile.get_uri(), None)
    except GLib.Error:
        pass


# ---------------------------------------------------------------------------
# MessageBubble


class MessageBubble(Gtk.Box):
    """A single chat message displayed as a styled bubble with a copy button."""

    def __init__(self, role: str, text: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(8)
        self.set_margin_end(8)

        self._role = role
        self._text = text

        # Outer alignment box
        align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # Bubble content box
        self._inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._inner.set_spacing(4)

        # Message label
        self._label = Gtk.Label(label=text)
        self._label.set_wrap(True)
        self._label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._label.set_xalign(0.0)
        self._label.set_selectable(True)
        self._label.set_max_width_chars(44)
        self._label.set_hexpand(True)

        # Shown only for errors, so they don't rely on colour alone
        self._error_icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        self._error_icon.set_valign(Gtk.Align.START)
        self._error_icon.set_visible(False)
        text_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text_row.append(self._error_icon)
        text_row.append(self._label)
        self._inner.append(text_row)

        # Button row: recovery actions (errors only) and copy
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_row.set_halign(Gtk.Align.END)
        self._btn_row = btn_row

        # Under answers: model and response time, and files sent as context
        self._meta_label = Gtk.Label(xalign=0.0)
        self._meta_label.add_css_class("bubble-meta")
        self._meta_label.set_visible(False)
        self._inner.append(self._meta_label)
        self._sources_label = Gtk.Label(xalign=0.0)
        self._sources_label.add_css_class("bubble-meta")
        self._sources_label.set_selectable(True)
        self._sources_label.set_wrap(True)
        self._sources_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._sources_label.set_visible(False)
        self._inner.append(self._sources_label)
        # Files the agent searched or quoted: one button each, opens the file
        self._found_box = Gtk.FlowBox()
        self._found_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._found_box.set_max_children_per_line(3)
        self._found_box.set_visible(False)
        self._found_box.update_property([Gtk.AccessibleProperty.LABEL], [_("Files used")])
        self._inner.append(self._found_box)
        self._copy_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self._copy_btn.add_css_class("copy-btn")
        self._copy_btn.add_css_class("flat")
        self._copy_btn.set_tooltip_text(_("Copy to clipboard"))
        self._copy_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Copy message")])
        self._copy_btn.connect("clicked", self._on_copy_clicked)
        btn_row.append(self._copy_btn)
        self._inner.append(btn_row)

        if role == "user":
            self.set_halign(Gtk.Align.END)
            self._inner.add_css_class("bubble-user")
            self._inner.set_margin_start(48)
        else:
            self.set_halign(Gtk.Align.START)
            self._inner.add_css_class("bubble-assistant")
            self._inner.set_margin_end(48)

        align_box.append(self._inner)
        self.append(align_box)

    def _on_copy_clicked(self, btn: Gtk.Button) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            clipboard = display.get_clipboard()
            clipboard.set(self._text)

    def get_text(self) -> str:
        """Return the message text received so far."""
        return self._text

    def append_widget(self, widget: Gtk.Widget) -> None:
        """Add a widget (e.g. an action card) under the message text."""
        self._inner.insert_child_after(widget, self._label.get_parent())

    def set_meta(self, text: str) -> None:
        """Show the model and response time under the answer."""
        self._meta_label.set_text(text)
        self._meta_label.set_visible(bool(text))

    def set_sources(self, paths: list[str]) -> None:
        """List the local files that were sent as context."""
        home = GLib.get_home_dir()
        shown = [p.replace(home, "~", 1) if home and p.startswith(home) else p for p in paths]
        self._sources_label.set_text(
            _("Sources: {files}").format(files=", ".join(shown)) if shown else ""
        )
        self._sources_label.set_visible(bool(shown))

    def set_found_files(self, paths: list[str]) -> None:
        """Show files the AI looked at as buttons that open them."""
        child = self._found_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._found_box.remove(child)
            child = nxt
        home = GLib.get_home_dir()
        for path in paths:
            btn = Gtk.Button(label=Path(path).name or path)
            btn.add_css_class("flat")
            btn.add_css_class("source-chip")
            shown = path.replace(home, "~", 1) if home and path.startswith(home) else path
            btn.set_tooltip_text(shown)
            btn.update_property(
                [Gtk.AccessibleProperty.LABEL], [_("Open {file}").format(file=shown)]
            )
            btn.connect("clicked", lambda _b, p=path: open_file(_b, p))
            self._found_box.append(btn)
        self._found_box.set_visible(bool(paths))

    def found_files(self) -> list[str]:
        """Tooltips of the file buttons, for tests."""
        out = []
        child = self._found_box.get_first_child()
        while child is not None:
            out.append(child.get_child().get_tooltip_text())
            child = child.get_next_sibling()
        return out

    def set_error(self, text: str) -> None:
        """Show *text* as an error: error colour plus an icon."""
        self._text = text
        self._label.set_text(text)
        self._error_icon.set_visible(True)
        self._inner.add_css_class("bubble-error")

    def add_action(self, label: str, callback: Any) -> Gtk.Button:
        """Add a recovery button (e.g. "Try again") before the copy button."""
        btn = Gtk.Button(label=label)
        btn.add_css_class("bubble-action")
        btn.connect("clicked", lambda _b: callback())
        self._btn_row.prepend(btn)
        return btn

    def append_text(self, chunk: str) -> None:
        """Append *chunk* to the bubble label (used during streaming)."""
        self._text += chunk
        if self._role == "assistant":
            # During streaming, use plain text to avoid partial markup crashes.
            # Final markup is applied in set_final_text() once streaming completes.
            self._label.set_text(self._text)
        else:
            self._label.set_text(self._text)

    def set_final_text(self, text: str) -> None:
        """Set final rendered text after streaming completes."""
        self._text = text
        if self._role == "assistant":
            try:
                self._label.set_markup(_apply_markup(text))
            except Exception:
                self._label.set_text(text)
        else:
            self._label.set_text(text)


# ---------------------------------------------------------------------------
# Markup rendering
# ---------------------------------------------------------------------------


def _apply_markup(text: str) -> str:
    """Convert a subset of Markdown-like markup to Pango markup."""
    # 1. Escape HTML special characters first (NUL is the code placeholder)
    escaped = html.escape(text.replace("\x00", ""))

    # Code (fenced blocks, then `inline`) is set in monospace and kept out of
    # the bold/italic rules below, so `**x**` inside code stays literal
    code: list[str] = []

    def keep(match: re.Match[str]) -> str:
        code.append(match.group(1))
        return f"\x00{len(code) - 1}\x00"

    escaped = re.sub(r"```[\w+-]*\n?(.*?)\n?```", keep, escaped, flags=re.DOTALL)
    escaped = re.sub(r"`([^`\n]+)`", keep, escaped)

    # 2. Bold: **text**
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)

    # 3. Italic: *text* (not preceded/followed by another *)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped)

    # 4. Bullet list items: lines starting with "- "
    lines = escaped.split("\n")
    result_lines: list[str] = []
    for line in lines:
        if re.match(r"^- ", line):
            result_lines.append("  • " + line[2:])
        else:
            result_lines.append(line)

    joined = "\n".join(result_lines)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<tt>{code[int(m.group(1))]}</tt>", joined)
