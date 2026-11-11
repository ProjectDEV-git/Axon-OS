"""panel_memory.py — The memories popover: list, add, forget and clear the facts the AI remembers.

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
from gi.repository import Adw, GLib, Gtk, Pango

# isort: split
from i18n import _


class MemoryMixin:
    """The memories popover: list, add, forget and clear the facts the AI remembers."""

    def _build_memory_popover(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(6)
        intro = Gtk.Label(
            label=_("Facts the AI remembers and uses in your chats."), xalign=0.0, wrap=True
        )
        intro.add_css_class("dim-label")
        intro.set_max_width_chars(36)
        box.append(intro)
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._mem_entry = Gtk.Entry(hexpand=True)
        self._mem_entry.set_placeholder_text(_("e.g. I prefer metric units"))
        self._mem_entry.update_property([Gtk.AccessibleProperty.LABEL], [_("New memory")])
        self._mem_entry.connect("activate", lambda _e: self._add_memory())
        add_btn = Gtk.Button(label=_("Remember"))
        add_btn.connect("clicked", lambda _b: self._add_memory())
        add_row.append(self._mem_entry)
        add_row.append(add_btn)
        box.append(add_row)
        self._mem_list = Gtk.ListBox()
        self._mem_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._mem_list.update_property([Gtk.AccessibleProperty.LABEL], [_("Memories")])
        self._mem_empty = Gtk.Label(label=_("Nothing remembered yet."), xalign=0.0)
        self._mem_empty.add_css_class("dim-label")
        self._mem_list.set_placeholder(self._mem_empty)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(320)
        scroll.set_propagate_natural_height(True)
        scroll.set_min_content_width(280)
        scroll.set_child(self._mem_list)
        box.append(scroll)
        self._mem_clear_btn = Gtk.Button(label=_("Forget All"))
        self._mem_clear_btn.add_css_class("destructive-action")
        self._mem_clear_btn.set_halign(Gtk.Align.END)
        self._mem_clear_btn.connect("clicked", lambda _b: self._confirm_clear_memories())
        box.append(self._mem_clear_btn)
        self._mem_popover.set_child(box)
        self._memories: list[dict[str, Any]] = []

    def _refresh_memories(self) -> None:
        def worker() -> None:
            facts = self._client.list_memories() if hasattr(self._client, "list_memories") else []
            GLib.idle_add(self._show_memories, facts)

        threading.Thread(target=worker, daemon=True).start()

    def _show_memories(self, facts: list[dict[str, Any]]) -> bool:
        self._memories = facts
        self._mem_list.remove_all()
        for fact in facts:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=str(fact.get("text", "")), xalign=0.0, hexpand=True)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(32)
            label.set_selectable(True)
            forget = Gtk.Button.new_from_icon_name("edit-delete-symbolic")
            forget.add_css_class("flat")
            forget.set_valign(Gtk.Align.CENTER)
            forget.set_tooltip_text(_("Forget"))
            forget.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [_("Forget “{text}”").format(text=fact.get("text", ""))],
            )
            fid = fact.get("id")
            forget.connect("clicked", lambda _b, fid=fid: self._forget_memory(fid))
            row.append(label)
            row.append(forget)
            self._mem_list.append(row)
        self._mem_clear_btn.set_sensitive(bool(facts))
        return False

    def _run_memory_job(self, job: Any) -> None:
        def worker() -> None:
            job()
            facts = self._client.list_memories()
            GLib.idle_add(self._show_memories, facts)

        threading.Thread(target=worker, daemon=True).start()

    def _add_memory(self) -> None:
        text = self._mem_entry.get_text().strip()
        if not text:
            return
        self._mem_entry.set_text("")
        self._run_memory_job(lambda: self._client.add_memory(text))

    def _forget_memory(self, fact_id: Any) -> None:
        if fact_id is None:
            return
        self._run_memory_job(lambda: self._client.forget_memory(int(fact_id)))

    def _confirm_clear_memories(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Forget Everything?"),
            body=_("The AI will forget all {n} saved facts. This can't be undone.").format(
                n=len(self._memories)
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("forget", _("Forget All"))
        dialog.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_memories_response)
        dialog.present()

    def _on_clear_memories_response(self, _dialog: Any, response: str) -> None:
        if response == "forget":
            self._run_memory_job(self._client.clear_memories)
