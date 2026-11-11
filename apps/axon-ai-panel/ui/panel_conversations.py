"""panel_conversations.py — Conversation lifecycle and the conversation-list popover (switch, rename, archive, delete).

Mixed into ``AIPanelWindow`` (see ``panel.py``); methods rely on its widgets/state.
"""

from __future__ import annotations

import threading
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Importing panel_compat first puts services/ and apps/shared on sys.path.
import panel_compat  # noqa: F401
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

# isort: split
from i18n import _
from ui_logic import (
    visible_conversations,
)


class ConversationsMixin:
    """Conversation lifecycle and the conversation-list popover (switch, rename, archive, delete)."""

    def _on_clear_clicked(self, btn: Gtk.Button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Clear Conversation"),
            body=_(
                "This will remove all messages in the current conversation. This cannot be undone."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_confirmed)
        dialog.present()

    def _init_conversation(self) -> None:
        """Initialize the persistent conversation ID and load past messages."""
        try:
            convs = visible_conversations(self._client.list_conversations())
            if convs:
                self._conv_id = convs[0]["id"]
                messages = self._client.get_messages(self._conv_id)
                for msg in messages:
                    role = msg.get("role", "assistant")
                    content = msg.get("content", "")
                    bubble = self._add_bubble(role, content)
                    bubble.set_final_text(content)
            else:
                self._conv_id = self._client.create_conversation(title=_("Active Chat"))
        except Exception:
            self._conv_id = self._client.create_conversation(title=_("Active Chat"))

    def _on_clear_confirmed(self, dialog: Adw.MessageDialog, response: str) -> None:
        if response == "clear":
            try:
                self._client.delete_conversation(self._conv_id)
            except Exception:
                pass
            self._conv_id = self._client.create_conversation(title=_("Active Chat"))
            child = self._messages_box.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                self._messages_box.remove(child)
                child = next_child

    def _build_conversation_popover(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        new_btn = Gtk.Button(label=_("New Conversation"))
        new_btn.connect("clicked", lambda _b: self._new_conversation())
        top.append(new_btn)
        self._show_archived = Gtk.CheckButton(label=_("Archived"))
        self._show_archived.set_tooltip_text(_("Show archived conversations"))
        self._show_archived.connect("toggled", lambda _c: self._refresh_conversations())
        top.append(self._show_archived)
        box.append(top)
        self._conv_list = Gtk.ListBox()
        self._conv_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._conv_list.update_property([Gtk.AccessibleProperty.LABEL], [_("Conversations")])
        self._conv_list.connect("row-activated", self._on_conversation_activated)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(360)
        scroll.set_propagate_natural_height(True)
        scroll.set_min_content_width(280)
        scroll.set_child(self._conv_list)
        box.append(scroll)
        self._conv_popover.set_child(box)
        self._conv_rows: list[dict[str, Any]] = []

    def _refresh_conversations(self) -> None:
        """Reload the list from the Brain (D-Bus, so in a worker)."""

        def worker() -> None:
            try:
                convs = self._client.list_conversations()
            except Exception:
                convs = []
            GLib.idle_add(self._show_conversations, convs)

        threading.Thread(target=worker, daemon=True).start()

    def _show_conversations(self, convs: list[dict[str, Any]]) -> bool:
        self._conv_rows = visible_conversations(convs, self._show_archived.get_active())
        self._conv_list.remove_all()
        if not self._conv_rows:
            empty = Gtk.Label(label=_("No conversations yet"))
            empty.add_css_class("dim-label")
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            self._conv_list.append(empty)
        for conv in self._conv_rows:
            self._conv_list.append(self._conversation_row(conv))
        return False

    def _conversation_row(self, conv: dict[str, Any]) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.conv_id = conv["id"]  # type: ignore[attr-defined]
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title = str(conv.get("title") or "Untitled")
        current = conv["id"] == self._conv_id
        label = Gtk.Label(label=title, xalign=0.0)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        if current:
            label.add_css_class("heading")
        box.append(label)
        archived = bool(conv.get("archived"))
        for icon, tip, handler in (
            ("document-edit-symbolic", "Rename", lambda _b: self._start_rename(row, label, conv)),
            (
                "package-x-generic-symbolic",
                "Restore" if archived else "Archive",
                lambda _b: self._archive_conversation(conv["id"], not archived),
            ),
            ("user-trash-symbolic", "Delete", lambda _b: self._confirm_delete(conv)),
        ):
            btn = Gtk.Button.new_from_icon_name(icon)
            btn.add_css_class("flat")
            btn.set_tooltip_text(tip)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [f"{tip} “{title}”"])
            btn.connect("clicked", handler)
            box.append(btn)
        row.set_child(box)
        state = " (current)" if current else " (archived)" if archived else ""
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}{state}"])
        return row

    def _on_conversation_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        conv_id = getattr(row, "conv_id", None)
        if conv_id and not self._streaming:
            self._conv_popover.popdown()
            self._open_conversation(conv_id)

    def _clear_bubbles(self) -> None:
        child = self._messages_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._messages_box.remove(child)
            child = next_child

    def _open_conversation(self, conv_id: str) -> None:
        """Show *conv_id*; messages are fetched in a worker."""
        self._conv_id = conv_id
        self._clear_bubbles()

        def worker() -> None:
            try:
                messages = self._client.get_messages(conv_id)
            except Exception:
                messages = []
            GLib.idle_add(self._show_messages, conv_id, messages)

        threading.Thread(target=worker, daemon=True).start()

    def _show_messages(self, conv_id: str, messages: list[dict[str, Any]]) -> bool:
        if conv_id != self._conv_id:
            return False  # the user switched again
        for msg in messages:
            content = str(msg.get("content", ""))
            bubble = self._add_bubble(str(msg.get("role", "assistant")), content)
            bubble.set_final_text(content)
        return False

    def _new_conversation(self) -> None:
        if self._streaming:
            return
        self._conv_popover.popdown()
        self._conv_id = self._client.create_conversation(title=_("New Chat"))
        self._clear_bubbles()
        self._entry.grab_focus()

    def _start_rename(self, row: Gtk.ListBoxRow, label: Gtk.Label, conv: dict[str, Any]) -> None:
        entry = Gtk.Entry(text=str(conv.get("title") or ""))
        entry.set_hexpand(True)
        box = label.get_parent()
        box.insert_child_after(entry, label)
        label.set_visible(False)
        entry.grab_focus()

        def save(_e: Gtk.Entry) -> None:
            title = entry.get_text().strip()
            if title:
                threading.Thread(
                    target=lambda: (
                        self._client.rename_conversation(conv["id"], title),
                        GLib.idle_add(self._refresh_conversations),
                    ),
                    daemon=True,
                ).start()

        keys = Gtk.EventControllerKey()
        keys.connect(
            "key-pressed",
            lambda _c, keyval, _k, _s: (
                self._refresh_conversations() or True if keyval == Gdk.KEY_Escape else False
            ),
        )
        entry.add_controller(keys)
        entry.connect("activate", save)

    def _archive_conversation(self, conv_id: str, archived: bool) -> None:
        def worker() -> None:
            self._client.archive_conversation(conv_id, archived)
            GLib.idle_add(self._refresh_conversations)

        threading.Thread(target=worker, daemon=True).start()
        if archived and conv_id == self._conv_id:
            self._new_conversation()

    def _confirm_delete(self, conv: dict[str, Any]) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Conversation?"),
            body=_("“{title}” will be removed. This can't be undone.").format(
                title=conv.get("title") or _("Untitled")
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_response, conv["id"])
        dialog.present()

    def _on_delete_response(self, _dialog: Adw.MessageDialog, response: str, conv_id: str) -> None:
        if response != "delete":
            return

        def worker() -> None:
            self._client.delete_conversation(conv_id)
            GLib.idle_add(self._refresh_conversations)

        threading.Thread(target=worker, daemon=True).start()
        if conv_id == self._conv_id:
            self._new_conversation()
