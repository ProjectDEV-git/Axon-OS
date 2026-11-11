"""
panel.py — AIPanelWindow for Axon AI Panel.

A side-panel GTK4/libadwaita window that streams responses from a local
Ollama instance and is aware of the user's desktop context.

The window is assembled from focused modules next to this file:
``panel_compat`` (import paths, Ollama client fallback), ``panel_bubble``
(message widget, markup), and the ``panel_*`` mixins for conversations,
memories, status/context, streaming and action cards. Everything the old
single-file module exposed is still importable from here.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

# Sibling modules are imported by name: this file is loaded both as
# ``ui.panel`` (main.py) and by path (tests), so relative imports won't do.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# OllamaClient / tool_sources are re-exported: callers import them from here.
from panel_compat import OllamaClient, tool_sources

# isort: split
from axon_gtk_utils import fit_default_size, load_app_css
from diagnostics import run_diagnostics
from i18n import N_, _
from panel_actions import ActionsMixin
from panel_bubble import MessageBubble, _apply_markup, open_file
from panel_context import ContextMixin
from panel_conversations import ConversationsMixin
from panel_memory import MemoryMixin
from panel_stream import StreamMixin
from service_utils import confirm_and_exec
from status_summary import StatusSummary
from ui_logic import (
    ModelOrigin,
    RequestTracker,
    escape_action,
    format_command_output,
    origin_tooltip,
    routing_origin,
)

__all__ = [
    "AIPanelWindow",
    "MessageBubble",
    "OllamaClient",
    "_apply_markup",
    "open_file",
    "tool_sources",
]


def _label_factory(ellipsize: bool) -> Gtk.SignalListItemFactory:
    """List item factory showing a Gtk.StringObject as a label."""
    factory = Gtk.SignalListItemFactory()

    def setup(_f: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        label = Gtk.Label(xalign=0.0)
        if ellipsize:
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_max_width_chars(14)
        item.set_child(label)

    def bind(_f: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        label = item.get_child()
        string = item.get_item()
        text = string.get_string() if string is not None else ""
        label.set_text(text)
        label.set_tooltip_text(text)

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    return factory


class AIPanelWindow(
    ConversationsMixin, MemoryMixin, ContextMixin, StreamMixin, ActionsMixin, Adw.Window
):
    """Main side-panel window for the Axon AI assistant."""

    _QUICK_CHIPS: list[str] = [  # noqa: RUF012
        N_("Explain Error"),
        N_("Summarize"),
        N_("Write Code"),
        N_("Fix Bug"),
        N_("Translate"),
    ]

    _AUTO_LABEL = "Automatic"

    _DEFAULT_MODELS: list[str] = [  # noqa: RUF012
        "llama3.2:3b",
        "mistral:7b",
        "qwen2.5:7b",
    ]

    def __init__(
        self,
        ollama_client: OllamaClient,
        context_reader: Any,
    ) -> None:
        super().__init__()

        self._client = ollama_client
        self._ctx_reader = context_reader
        self._streaming = False
        self._stream_bubble: MessageBubble | None = None
        self._stream_cancelled = False
        self._requests = RequestTracker()
        self._stream_prompt = ""
        self._conv_id: str = ""

        load_app_css(Path(__file__).with_name("panel.css"), self.get_display())

        # 860 px doesn't fit 768 px-high screens on an undecorated window
        fit_default_size(self, 420, 860)
        self.set_resizable(True)
        self.set_decorated(False)

        # Root layout
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        # ---- Header --------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.add_css_class("panel-header")
        header.set_spacing(8)
        header.set_margin_start(16)
        header.set_margin_end(16)
        header.set_margin_top(12)
        header.set_margin_bottom(12)

        title_label = Gtk.Label()
        title_label.set_markup("<b>⬡ Axon AI</b>")
        title_label.set_hexpand(True)
        title_label.set_xalign(0.0)

        # Model dropdown
        self._model_store = Gtk.StringList()
        self._model_store.append(self._AUTO_LABEL)
        for m in self._DEFAULT_MODELS:
            self._model_store.append(m)
        self._providers: list[dict[str, Any]] | None = None
        self._tiers: dict[str, str] = {}

        self._model_dropdown = Gtk.DropDown(model=self._model_store)
        # Long names ("@openrouter/anthropic/…") are shortened on the button so
        # the panel still fits 320 px; the popup list shows them in full.
        self._model_dropdown.set_factory(_label_factory(ellipsize=True))
        self._model_dropdown.set_list_factory(_label_factory(ellipsize=False))
        self._model_dropdown.set_selected(self._model_index(self._client.model))
        self._model_dropdown.connect("notify::selected", self._on_model_changed)

        self._model_dropdown.set_tooltip_text(_("Model"))
        self._model_dropdown.update_property([Gtk.AccessibleProperty.LABEL], [_("Model")])

        # Clear history button
        self._clear_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self._clear_btn.add_css_class("clear-btn")
        self._clear_btn.add_css_class("flat")
        self._clear_btn.set_tooltip_text(_("Clear conversation"))
        self._clear_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Clear conversation")])
        self._clear_btn.connect("clicked", self._on_clear_clicked)

        # Conversation list: switch, rename, archive, delete, start new
        self._conv_button = Gtk.MenuButton(icon_name="view-list-symbolic")
        self._conv_button.add_css_class("flat")
        self._conv_button.set_tooltip_text(_("Conversations"))
        self._conv_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Conversations")])
        self._conv_popover = Gtk.Popover()
        self._conv_popover.connect("show", lambda _p: self._refresh_conversations())
        self._conv_button.set_popover(self._conv_popover)
        self._build_conversation_popover()

        # What the AI remembers about the user: view, add, forget
        self._mem_button = Gtk.MenuButton(icon_name="user-bookmarks-symbolic")
        self._mem_button.add_css_class("flat")
        self._mem_button.set_tooltip_text(_("Memories"))
        self._mem_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Memories")])
        self._mem_popover = Gtk.Popover()
        self._mem_popover.connect("show", lambda _p: self._refresh_memories())
        self._mem_button.set_popover(self._mem_popover)
        self._build_memory_popover()

        header.append(self._conv_button)
        header.append(self._mem_button)
        header.append(title_label)
        header.append(self._model_dropdown)
        header.append(self._clear_btn)
        root.append(header)

        # Where requests go. Never says "local" until the model is checked.
        self._origin_label = Gtk.Label(label=_("Model: checking…"), xalign=0.0)
        self._origin_label.add_css_class("origin-label")
        self._origin_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._origin_label.set_margin_start(16)
        self._origin_label.set_margin_end(16)
        root.append(self._origin_label)

        # ---- Axon status (Brain, Ollama, model, index, voice, last error) ----
        self._status_expander = Gtk.Expander()
        self._status_expander.set_margin_start(12)
        self._status_expander.set_margin_end(12)
        status_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._status_icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
        self._status_headline = Gtk.Label(label=_("Axon status: checking…"), xalign=0.0)
        self._status_headline.set_ellipsize(Pango.EllipsizeMode.END)
        status_title.append(self._status_icon)
        status_title.append(self._status_headline)
        self._status_expander.set_label_widget(status_title)
        status_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        status_body.set_margin_top(6)
        self._status_list = Gtk.ListBox()
        self._status_list.add_css_class("boxed-list")
        self._status_list.set_selection_mode(Gtk.SelectionMode.NONE)
        status_body.append(self._status_list)
        self._status_fix = Gtk.Label(xalign=0.0)
        self._status_fix.set_wrap(True)
        self._status_fix.set_selectable(True)
        self._status_fix.set_visible(False)
        status_body.append(self._status_fix)
        status_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_buttons.set_halign(Gtk.Align.END)
        refresh = Gtk.Button(label=_("Check Again"))
        refresh.connect("clicked", lambda _b: self._refresh_status())
        diagnostics = Gtk.Button(label=_("Full Diagnostics"))
        diagnostics.connect("clicked", lambda _b: self._open_diagnostics())
        status_buttons.append(refresh)
        status_buttons.append(diagnostics)
        status_body.append(status_buttons)
        self._status_expander.set_child(status_body)
        root.append(self._status_expander)
        self._status: StatusSummary | None = None
        self._status_loading = False

        # ---- Quick action chips --------------------------------------------
        # They wrap onto a second row rather than scrolling sideways, where
        # the last chip was cut off with nothing to say more were hidden.
        chips_row = Gtk.FlowBox()
        chips_row.set_selection_mode(Gtk.SelectionMode.NONE)
        chips_row.set_column_spacing(6)
        chips_row.set_row_spacing(6)
        chips_row.set_min_children_per_line(2)
        chips_row.set_max_children_per_line(5)
        chips_row.set_homogeneous(False)
        chips_row.set_margin_start(16)
        chips_row.set_margin_end(16)
        chips_row.set_margin_top(8)
        chips_row.set_margin_bottom(4)
        self._chips_row = chips_row

        for chip_label in (_(c) for c in self._QUICK_CHIPS):
            chip_btn = Gtk.Button(label=chip_label)
            chip_btn.add_css_class("quick-chip")
            chip_btn.add_css_class("flat")
            chip_btn.connect(
                "clicked",
                lambda _btn, lbl=chip_label: self._on_chip_clicked(lbl + ": "),
            )
            chips_row.append(chip_btn)

        root.append(chips_row)

        # ---- Chat area -----------------------------------------------------
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_vexpand(True)
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._messages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._messages_box.set_spacing(2)
        self._messages_box.set_margin_top(8)
        self._messages_box.set_margin_bottom(8)
        self._scroll.set_child(self._messages_box)
        root.append(self._scroll)

        # ---- Spinner -------------------------------------------------------
        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        self._spinner.set_margin_top(4)
        self._spinner.set_margin_bottom(4)
        root.append(self._spinner)

        # ---- Context row: what goes with the next message -----------------
        ctx_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ctx_row.add_css_class("context-row")
        ctx_row.set_margin_start(12)
        ctx_row.set_margin_end(12)
        self._ctx_title = Gtk.Label(label=_("Context: checking…"), xalign=0.0)
        self._ctx_title.add_css_class("context-title")
        self._ctx_title.set_valign(Gtk.Align.CENTER)
        ctx_row.append(self._ctx_title)
        self._ctx_flow = Gtk.FlowBox()
        self._ctx_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._ctx_flow.set_hexpand(True)
        self._ctx_flow.set_max_children_per_line(6)
        ctx_row.append(self._ctx_flow)
        ctx_settings_btn = Gtk.Button(label=_("Settings"))
        ctx_settings_btn.add_css_class("flat")
        ctx_settings_btn.set_valign(Gtk.Align.CENTER)
        ctx_settings_btn.set_tooltip_text(_("Choose what context Axon may send"))
        ctx_settings_btn.connect("clicked", lambda _b: self._open_settings_action("preferences"))
        ctx_row.append(ctx_settings_btn)
        root.append(ctx_row)
        self._ctx_snapshot: dict[str, Any] | None = None
        self._ctx_items: list[tuple[str, str]] = []
        self._ctx_excluded: set[str] = set()  # removed for the next message only
        self._ctx_enabled = True
        # Conversation in which the user accepted sending sensitive context to
        # a model that leaves this computer
        self._cloud_ack_conv = ""

        # ---- Input area ----------------------------------------------------
        input_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        input_area.add_css_class("panel-input-area")
        input_area.set_spacing(8)

        self._entry = Gtk.Entry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text(_("Ask anything…"))
        self._entry.connect("activate", self._on_entry_activate)

        self._send_btn = Gtk.Button(label=_("Send"))
        self._send_btn.add_css_class("suggested-action")
        self._send_btn.connect("clicked", self._on_send_clicked)

        # Replaces Send while an answer is streaming
        self._stop_btn = Gtk.Button(label=_("Stop"))
        self._stop_btn.set_tooltip_text(_("Stop generating (Esc)"))
        self._stop_btn.connect("clicked", lambda _b: self._cancel_stream())
        self._stop_btn.set_visible(False)

        input_area.append(self._entry)
        input_area.append(self._send_btn)
        input_area.append(self._stop_btn)
        root.append(input_area)

        # Escape key hides the window
        esc_ctrl = Gtk.EventControllerKey()
        esc_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(esc_ctrl)

        # Clean up streaming and resources on destroy
        self.connect("destroy", self._on_destroy)

        # Fetch real model list in background
        threading.Thread(target=self._fetch_models, daemon=True).start()

        # Initialize D-Bus conversation session
        self._init_conversation()
        self._refresh_context()
        self._refresh_status()

    def _fetch_models(self) -> None:
        """Worker: fetch models and provider info, then update the header."""
        routing_info = getattr(self._client, "routing_info", None)
        try:
            providers, tiers = routing_info() if routing_info is not None else (None, {})
        except Exception:
            providers, tiers = None, {}
        GLib.idle_add(self._set_routing_info, providers, tiers)
        try:
            models = self._client.list_models()
            if models:
                GLib.idle_add(self._populate_model_dropdown, models)
        except Exception:
            pass

    def _set_routing_info(
        self, providers: list[dict[str, Any]] | None, tiers: dict[str, str]
    ) -> bool:
        self._providers = providers
        self._tiers = tiers
        self._update_origin()
        return False

    def current_origin(self) -> ModelOrigin:
        """Where the next request goes, from the selected model."""
        return routing_origin(self._client.model, self._tiers, self._providers)

    def _update_origin(self) -> None:
        origin = self.current_origin()
        self._origin_label.set_text(origin.label)
        # The label may be shortened at 320 px; the tooltip has it in full
        self._origin_label.set_tooltip_text(f"{origin.label}\n{origin_tooltip(origin)}")

    def _model_index(self, model: str) -> int:
        if not model:
            return 0
        for i in range(self._model_store.get_n_items()):
            if self._model_store.get_string(i) == model:
                return i
        return 0

    def _populate_model_dropdown(self, models: list[str]) -> bool:
        # Build a combined deduplicated list
        existing = set(self._DEFAULT_MODELS)
        all_models: list[str] = list(self._DEFAULT_MODELS)
        for m in models:
            if m not in existing:
                all_models.append(m)
                existing.add(m)

        # Rebuild the StringList, keeping the current choice
        current = self._client.model
        new_store = Gtk.StringList()
        new_store.append(self._AUTO_LABEL)
        for m in all_models:
            new_store.append(m)
        self._model_store = new_store
        self._model_dropdown.set_model(new_store)
        self._model_dropdown.set_selected(self._model_index(current))
        return False

    def _on_model_changed(self, dropdown: Gtk.DropDown, _param: Any) -> None:
        index = dropdown.get_selected()
        selected_item = dropdown.get_selected_item()
        if selected_item is not None:
            self._client.model = "" if index == 0 else selected_item.get_string()
            self._update_origin()

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Any,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            # First Escape stops a streaming answer; the next one hides
            if escape_action(self._requests.busy) == "cancel":
                self._cancel_stream()
            else:
                self.set_visible(False)
            return True
        return False

    def _on_chip_clicked(self, prefix: str) -> None:
        """Put the chip prefix in the entry so the user can finish the prompt."""
        current = self._entry.get_text()
        if not current.startswith(prefix):
            self._entry.set_text(prefix + current)
        self._entry.grab_focus()
        self._entry.set_position(-1)

    def _on_entry_activate(self, entry: Gtk.Entry) -> None:
        self._send_message(entry.get_text())

    def _on_send_clicked(self, btn: Gtk.Button) -> None:
        self._send_message(self._entry.get_text())

    def _load_status_worker(self) -> None:
        try:
            results = run_diagnostics(only=["services", "ai"]).results
        except Exception:
            results = []
        search_stats = getattr(self._client, "search_stats", None)
        stats = search_stats() if search_stats is not None else None
        pull = getattr(self._client, "pull_progress", None)
        GLib.idle_add(self._show_status, results, stats, pull)

    _RISK_ICONS = {  # noqa: RUF012
        "read_only": "dialog-information-symbolic",
        "changes_files": "document-edit-symbolic",
        "deletes_files": "user-trash-symbolic",
        "needs_root": "dialog-password-symbolic",
    }

    def _run_card_command(self, bubble: MessageBubble, card: Gtk.Box, command: str) -> None:
        card.set_sensitive(False)

        def worker() -> None:
            proc = confirm_and_exec(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            if proc is None:
                GLib.idle_add(self._card_result, bubble, card, "Not run.")
                return
            try:
                out, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                out = "(stopped after 15 seconds)"
            GLib.idle_add(self._card_result, bubble, card, format_command_output(out or ""))

        threading.Thread(target=worker, daemon=True).start()

    def toggle(self) -> None:
        """Show the window if hidden, hide it if visible."""
        if self.get_visible():
            self.set_visible(False)
        else:
            self._refresh_context()
            self._refresh_status()
            self.present()

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        """Cancel streaming and clean up resources on window close."""
        self._stream_cancelled = True
