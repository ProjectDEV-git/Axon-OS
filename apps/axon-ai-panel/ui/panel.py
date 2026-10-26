"""
panel.py — AIPanelWindow for Axon AI Panel.

A side-panel GTK4/libadwaita window that streams responses from a local
Ollama instance and is aware of the user's desktop context.
"""

from __future__ import annotations

import html
import json
import re
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

# ---------------------------------------------------------------------------
# Import OllamaClient from the intent-bar app, falling back to a built-in
# stub if the module is not yet populated.
# ---------------------------------------------------------------------------
_intent_bar_path = str(Path(__file__).resolve().parents[2] / "intent-bar")
if _intent_bar_path not in sys.path:
    sys.path.insert(0, _intent_bar_path)

# services/ sits next to apps/ in the source tree and the .deb, and inside
# the app directory for install.sh (~/.local/share/axon-os/services).
for _base in (Path(__file__).resolve().parents[3], Path(__file__).resolve().parents[2]):
    if (_base / "services").is_dir():
        sys.path.insert(0, str(_base / "services"))
        break
from ui_logic import (
    ModelOrigin,
    RequestTracker,
    answer_meta,
    escape_action,
    origin_tooltip,
    routing_origin,
    split_error,
    visible_conversations,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from axon_gtk_utils import fit_default_size, load_app_css
from context_settings import (
    ITEM_LABELS,
    SENSITIVE_ITEMS,
    load_settings,
    sends_sensitive,
    sent_files,
)
from diagnostics import FAIL, INFO, OK, WARN, run_diagnostics
from status_summary import StatusSummary, build_status

_STATUS_ICONS = {
    OK: "emblem-ok-symbolic",
    INFO: "dialog-information-symbolic",
    WARN: "dialog-warning-symbolic",
    FAIL: "dialog-error-symbolic",
}
# Text next to each icon, so status never relies on colour alone
_STATUS_WORDS = {OK: "OK", INFO: "Info", WARN: "Warning", FAIL: "Problem"}

try:
    from ollama_client import OllamaClient  # type: ignore[import]
except ImportError:
    import urllib.error
    import urllib.request

    class OllamaClient:  # type: ignore[no-redef]
        """Minimal Ollama client used when intent-bar's module is absent."""

        BASE_URL: str = "http://localhost:11434"

        def __init__(self, model: str = "llama3.2:3b") -> None:
            self.model = model

        def is_available(self) -> bool:
            try:
                urllib.request.urlopen(f"{self.BASE_URL}/api/tags", timeout=2)  # nosec B310 - fixed http URL
                return True
            except Exception:
                return False

        def list_models(self) -> list[str]:
            """Return list of locally available model names."""
            try:
                with urllib.request.urlopen(f"{self.BASE_URL}/api/tags", timeout=5) as resp:  # nosec B310
                    data = json.loads(resp.read().decode())
                    return [m["name"] for m in data.get("models", [])]
            except Exception:
                return []

        def chat_stream(
            self,
            messages: list[dict],
            system: str = "",
        ) -> Iterator[str]:
            """Yield text chunks from the Ollama /api/chat endpoint."""
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": True,
            }
            if system:
                payload["system"] = system

            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self.BASE_URL}/api/chat",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - BASE_URL
                    for raw_line in resp:
                        line = raw_line.decode().strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            chunk = obj.get("message", {}).get("content", "") or obj.get(
                                "response", ""
                            )
                            if chunk:
                                yield chunk
                            if obj.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                yield f"\n[Error: {exc}]"

        def list_conversations(self):
            return []

        def get_messages(self, conv_id):
            return []

        def create_conversation(self, title=None):
            return "fallback"

        def delete_conversation(self, conv_id):
            pass

        def send_message_stream(self, conv_id, message, system=""):
            return iter(["AI service unavailable."])


# ---------------------------------------------------------------------------
# MessageBubble
# ---------------------------------------------------------------------------


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
        self._copy_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self._copy_btn.add_css_class("copy-btn")
        self._copy_btn.add_css_class("flat")
        self._copy_btn.set_tooltip_text("Copy to clipboard")
        self._copy_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Copy message"])
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

    def set_meta(self, text: str) -> None:
        """Show the model and response time under the answer."""
        self._meta_label.set_text(text)
        self._meta_label.set_visible(bool(text))

    def set_sources(self, paths: list[str]) -> None:
        """List the local files that were sent as context."""
        home = GLib.get_home_dir()
        shown = [p.replace(home, "~", 1) if home and p.startswith(home) else p for p in paths]
        self._sources_label.set_text("Sources: " + ", ".join(shown) if shown else "")
        self._sources_label.set_visible(bool(shown))

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
    # 1. Escape HTML special characters first
    escaped = html.escape(text)

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

    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# AIPanelWindow
# ---------------------------------------------------------------------------


class AIPanelWindow(Adw.Window):
    """Main side-panel window for the Axon AI assistant."""

    _QUICK_CHIPS: list[str] = [  # noqa: RUF012
        "Explain Error",
        "Summarize",
        "Write Code",
        "Fix Bug",
        "Translate",
    ]

    # First dropdown entry: let the Brain pick a model per request (model "")
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
        self._model_dropdown.set_selected(self._model_index(self._client.model))
        self._model_dropdown.connect("notify::selected", self._on_model_changed)

        self._model_dropdown.set_tooltip_text("Model")
        self._model_dropdown.update_property([Gtk.AccessibleProperty.LABEL], ["Model"])

        # Clear history button
        self._clear_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self._clear_btn.add_css_class("clear-btn")
        self._clear_btn.add_css_class("flat")
        self._clear_btn.set_tooltip_text("Clear conversation")
        self._clear_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Clear conversation"])
        self._clear_btn.connect("clicked", self._on_clear_clicked)

        # Conversation list: switch, rename, archive, delete, start new
        self._conv_button = Gtk.MenuButton(icon_name="view-list-symbolic")
        self._conv_button.add_css_class("flat")
        self._conv_button.set_tooltip_text("Conversations")
        self._conv_button.update_property([Gtk.AccessibleProperty.LABEL], ["Conversations"])
        self._conv_popover = Gtk.Popover()
        self._conv_popover.connect("show", lambda _p: self._refresh_conversations())
        self._conv_button.set_popover(self._conv_popover)
        self._build_conversation_popover()

        header.append(self._conv_button)
        header.append(title_label)
        header.append(self._model_dropdown)
        header.append(self._clear_btn)
        root.append(header)

        # Where requests go. Never says "local" until the model is checked.
        self._origin_label = Gtk.Label(label="Model: checking…", xalign=0.0)
        self._origin_label.add_css_class("origin-label")
        self._origin_label.set_margin_start(16)
        self._origin_label.set_margin_end(16)
        root.append(self._origin_label)

        # ---- Axon status (Brain, Ollama, model, index, voice, last error) ----
        self._status_expander = Gtk.Expander()
        self._status_expander.set_margin_start(12)
        self._status_expander.set_margin_end(12)
        status_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._status_icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
        self._status_headline = Gtk.Label(label="Axon status: checking…", xalign=0.0)
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
        refresh = Gtk.Button(label="Check Again")
        refresh.connect("clicked", lambda _b: self._refresh_status())
        diagnostics = Gtk.Button(label="Full Diagnostics")
        diagnostics.connect("clicked", lambda _b: self._open_diagnostics())
        status_buttons.append(refresh)
        status_buttons.append(diagnostics)
        status_body.append(status_buttons)
        self._status_expander.set_child(status_body)
        root.append(self._status_expander)
        self._status: StatusSummary | None = None
        self._status_loading = False

        # ---- Quick action chips --------------------------------------------
        chips_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        chips_row.set_spacing(6)
        chips_row.set_margin_start(8)
        chips_row.set_margin_end(8)
        chips_row.set_margin_top(8)
        chips_row.set_margin_bottom(4)

        # Wrap chips in a scrolled window so they don't overflow
        chips_scroll = Gtk.ScrolledWindow()
        chips_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        chips_scroll.set_child(chips_row)
        chips_scroll.set_margin_start(8)
        chips_scroll.set_margin_end(8)
        chips_scroll.set_margin_top(8)
        chips_scroll.set_margin_bottom(4)

        for chip_label in self._QUICK_CHIPS:
            chip_btn = Gtk.Button(label=chip_label)
            chip_btn.add_css_class("quick-chip")
            chip_btn.add_css_class("flat")
            chip_btn.connect(
                "clicked",
                lambda _btn, lbl=chip_label: self._on_chip_clicked(lbl + ": "),
            )
            chips_row.append(chip_btn)

        root.append(chips_scroll)

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
        self._ctx_title = Gtk.Label(label="Context: checking…", xalign=0.0)
        self._ctx_title.add_css_class("context-title")
        self._ctx_title.set_valign(Gtk.Align.CENTER)
        ctx_row.append(self._ctx_title)
        self._ctx_flow = Gtk.FlowBox()
        self._ctx_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._ctx_flow.set_hexpand(True)
        self._ctx_flow.set_max_children_per_line(6)
        ctx_row.append(self._ctx_flow)
        ctx_settings_btn = Gtk.Button(label="Settings")
        ctx_settings_btn.add_css_class("flat")
        ctx_settings_btn.set_valign(Gtk.Align.CENTER)
        ctx_settings_btn.set_tooltip_text("Choose what context Axon may send")
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
        self._entry.set_placeholder_text("Ask anything…")
        self._entry.connect("activate", self._on_entry_activate)

        self._send_btn = Gtk.Button(label="Send")
        self._send_btn.add_css_class("suggested-action")
        self._send_btn.connect("clicked", self._on_send_clicked)

        # Replaces Send while an answer is streaming
        self._stop_btn = Gtk.Button(label="Stop")
        self._stop_btn.set_tooltip_text("Stop generating (Esc)")
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

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

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
        self._origin_label.set_tooltip_text(origin_tooltip(origin))

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

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Input handlers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Clear history
    # ------------------------------------------------------------------

    def _on_clear_clicked(self, btn: Gtk.Button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Clear Conversation",
            body="This will remove all messages in the current conversation. This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
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
                self._conv_id = self._client.create_conversation(title="Active Chat")
        except Exception:
            self._conv_id = self._client.create_conversation(title="Active Chat")

    def _on_clear_confirmed(self, dialog: Adw.MessageDialog, response: str) -> None:
        if response == "clear":
            try:
                self._client.delete_conversation(self._conv_id)
            except Exception:
                pass
            self._conv_id = self._client.create_conversation(title="Active Chat")
            child = self._messages_box.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                self._messages_box.remove(child)
                child = next_child

    # ------------------------------------------------------------------
    # Conversation list
    # ------------------------------------------------------------------

    def _build_conversation_popover(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        new_btn = Gtk.Button(label="New Conversation")
        new_btn.connect("clicked", lambda _b: self._new_conversation())
        top.append(new_btn)
        self._show_archived = Gtk.CheckButton(label="Archived")
        self._show_archived.set_tooltip_text("Show archived conversations")
        self._show_archived.connect("toggled", lambda _c: self._refresh_conversations())
        top.append(self._show_archived)
        box.append(top)
        self._conv_list = Gtk.ListBox()
        self._conv_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._conv_list.update_property([Gtk.AccessibleProperty.LABEL], ["Conversations"])
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
            empty = Gtk.Label(label="No conversations yet")
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
        self._conv_id = self._client.create_conversation(title="New Chat")
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
            heading="Delete Conversation?",
            body=f"“{conv.get('title') or 'Untitled'}” will be removed. This can't be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
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

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def _add_bubble(self, role: str, text: str = "") -> MessageBubble:
        bubble = MessageBubble(role, text)
        self._messages_box.append(bubble)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self) -> None:
        def _do_scroll() -> bool:
            adj = self._scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False

        GLib.idle_add(_do_scroll)

    def _send_message(self, text: str) -> None:
        text = text.strip()
        if not text or self._streaming:
            return
        if self._needs_cloud_warning():
            self._confirm_cloud_send(text)
            return
        self._submit(text)

    def _submit(self, text: str) -> None:
        self._entry.set_text("")
        self._add_bubble("user", text)
        self._start_request(text)

    def _start_request(self, text: str) -> None:
        """Stream an answer to *text* into a new assistant bubble."""
        request_id = self._requests.start()
        self._set_streaming(True)
        self._stream_prompt = text
        self._stream_started = time.monotonic()
        self._first_token_at: float | None = None
        self._stream_bubble = self._add_bubble("assistant", "")

        exclude = set(self._ctx_excluded)
        if self.current_origin().leaves_computer and self._cloud_ack_conv != self._conv_id:
            # Not warned in this conversation: send no sensitive context
            exclude |= SENSITIVE_ITEMS
        snapshot = self._ctx_snapshot
        self._ctx_excluded = set()  # removals apply to one message

        # The availability check and context lookup are D-Bus/HTTP calls:
        # they run in the worker so the panel never freezes.
        model = self._client.model
        thread = threading.Thread(
            target=self._stream_response,
            args=(text, model, request_id, frozenset(exclude), snapshot),
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # Axon status
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        """Re-run the services/AI diagnostics checks in a worker."""
        if self._status_loading:
            return
        self._status_loading = True
        threading.Thread(target=self._load_status_worker, daemon=True).start()

    def _load_status_worker(self) -> None:
        try:
            results = run_diagnostics(only=["services", "ai"]).results
        except Exception:
            results = []
        search_stats = getattr(self._client, "search_stats", None)
        stats = search_stats() if search_stats is not None else None
        pull = getattr(self._client, "pull_progress", None)
        GLib.idle_add(self._show_status, results, stats, pull)

    def _show_status(self, results: list[Any], stats: Any, pull: Any) -> bool:
        self._status_loading = False
        summary = build_status(results, self.current_origin().label, stats, pull)
        self._status = summary
        self._status_icon.set_from_icon_name(_STATUS_ICONS[summary.status])
        self._status_headline.set_text(f"Axon status: {summary.headline}")
        self._status_list.remove_all()
        for row in summary.rows:
            line = Adw.ActionRow(title=row.name, subtitle=row.text)
            line.set_use_markup(False)
            icon = Gtk.Image.new_from_icon_name(_STATUS_ICONS[row.status])
            icon.set_tooltip_text(_STATUS_WORDS[row.status])
            icon.update_property([Gtk.AccessibleProperty.LABEL], [_STATUS_WORDS[row.status]])
            line.add_prefix(icon)
            self._status_list.append(line)
        problem = summary.problem
        if problem is not None and problem.hint:
            self._status_fix.set_text(f"Fix: {problem.hint}")
            self._status_fix.set_visible(True)
        else:
            self._status_fix.set_visible(False)
        # Open the section by itself when something is wrong
        if summary.status == FAIL:
            self._status_expander.set_expanded(True)
        return False

    # ------------------------------------------------------------------
    # Context row and cloud warning
    # ------------------------------------------------------------------

    def _refresh_context(self) -> None:
        """Reload what context would be sent (D-Bus, so in a worker)."""
        if not hasattr(self._ctx_reader, "snapshot"):
            self._show_context(None, [], True)
            return
        threading.Thread(target=self._load_context_worker, daemon=True).start()

    def _load_context_worker(self) -> None:
        try:
            snapshot = self._ctx_reader.snapshot()
            items = self._ctx_reader.context_items(snapshot)
            enabled = self._ctx_reader.settings().share_context
        except Exception:
            snapshot, items, enabled = None, [], True
        GLib.idle_add(self._show_context, snapshot, items, enabled)

    def _show_context(
        self, snapshot: dict[str, Any] | None, items: list[tuple[str, str]], enabled: bool
    ) -> bool:
        self._ctx_snapshot = snapshot
        self._ctx_items = items
        self._ctx_enabled = enabled
        self._render_context()
        return False

    def _render_context(self) -> None:
        self._ctx_flow.remove_all()
        shown = [(k, d) for k, d in self._ctx_items if k not in self._ctx_excluded]
        if not self._ctx_enabled:
            self._ctx_title.set_text("Context: off")
        elif not shown:
            self._ctx_title.set_text("Context: none")
        else:
            self._ctx_title.set_text("Context:")
        for key, desc in shown:
            chip = Gtk.Button()
            chip.add_css_class("context-chip")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            box.append(Gtk.Label(label=desc))
            box.append(Gtk.Image.new_from_icon_name("window-close-symbolic"))
            chip.set_child(box)
            what = ITEM_LABELS.get(key, key).lower()
            chip.set_tooltip_text(f"Don't send {what} with this message")
            chip.update_property([Gtk.AccessibleProperty.LABEL], [f"Don't send {desc}"])
            chip.connect("clicked", lambda _b, k=key: self._exclude_context(k))
            self._ctx_flow.append(chip)

    def _exclude_context(self, key: str) -> None:
        self._ctx_excluded.add(key)
        self._render_context()

    def _needs_cloud_warning(self) -> bool:
        """Warn once per conversation before sensitive context leaves the computer."""
        return (
            self.current_origin().leaves_computer
            and self._cloud_ack_conv != self._conv_id
            and sends_sensitive(self._ctx_items, self._ctx_excluded)
        )

    def _confirm_cloud_send(self, text: str) -> None:
        origin = self.current_origin()
        labels = [
            ITEM_LABELS[k].lower()
            for k, _d in self._ctx_items
            if k in SENSITIVE_ITEMS and k not in self._ctx_excluded
        ]
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Send desktop context to the cloud?",
            body=(
                f"{origin_tooltip(origin)} This message would include your "
                f"{', '.join(labels)}. You won't be asked again in this conversation."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("without", "Send Without It")
        dialog.add_response("send", "Send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.DESTRUCTIVE)
        # Closing the dialog sends nothing
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_cloud_response, text)
        dialog.present()

    def _on_cloud_response(self, _dialog: Adw.MessageDialog, response: str, text: str) -> None:
        if response == "send":
            self._cloud_ack_conv = self._conv_id
            self._submit(text)
        elif response == "without":
            self._ctx_excluded |= SENSITIVE_ITEMS
            self._submit(text)

    def _set_streaming(self, active: bool) -> None:
        self._streaming = active
        self._spinner.set_visible(active)
        self._send_btn.set_visible(not active)
        self._stop_btn.set_visible(active)
        if active:
            self._spinner.start()
        else:
            self._spinner.stop()

    def _cancel_stream(self) -> None:
        """Stop the streaming answer, keep what arrived and mark it cancelled."""
        if not self._requests.cancel():
            return
        self._set_streaming(False)
        if self._stream_bubble is not None:
            partial = self._stream_bubble.get_text()
            self._stream_bubble.set_final_text(
                f"{partial}\n\n(Cancelled)" if partial else "Cancelled"
            )
            self._stream_bubble = None
        self._entry.grab_focus()
        cancel = getattr(self._client, "cancel", None)
        if cancel is not None:
            threading.Thread(target=cancel, daemon=True).start()

    def _stream_response(
        self,
        text: str,
        model: str,
        request_id: int,
        exclude: frozenset[str] = frozenset(),
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        if not self._client.is_available():
            GLib.idle_add(self._on_ai_unavailable, request_id)
            return
        sources: list[str] = []
        if hasattr(self._ctx_reader, "snapshot"):
            if snapshot is None:
                snapshot = self._ctx_reader.snapshot()
            ctx = self._ctx_reader.build_context_string(exclude=exclude, snapshot=snapshot)
            if ctx:
                settings = getattr(self._ctx_reader, "settings", load_settings)()
                sources = sent_files(snapshot, settings, exclude)
        else:
            ctx = self._ctx_reader.build_context_string()
        accumulated = ""
        try:
            for chunk in self._client.send_message_stream(
                self._conv_id, text, model=model, ctx=ctx
            ):
                if self._stream_cancelled or not self._requests.is_current(request_id):
                    return
                accumulated += chunk
                GLib.idle_add(self._on_chunk, chunk, request_id)
        except Exception as exc:
            error_text = f"\n[Error: {exc}]"
            accumulated += error_text
            GLib.idle_add(self._on_chunk, error_text, request_id)
        if not self._stream_cancelled:
            GLib.idle_add(self._on_stream_done, accumulated, request_id, sources)

    def _on_ai_unavailable(self, request_id: int) -> bool:
        if not self._requests.finish(request_id):
            return False
        self._set_streaming(False)
        if self._stream_bubble is not None:
            bubble = self._stream_bubble
            self._stream_bubble = None
            self._show_bubble_error(
                bubble, "Axon Brain service is offline or Ollama is not running.", offline=True
            )
        return False

    def _show_bubble_error(self, bubble: MessageBubble, message: str, offline: bool) -> None:
        """Turn *bubble* into an error with recovery buttons."""
        bubble.set_error(message)
        prompt = self._stream_prompt
        bubble.add_action("Try again", lambda: self._retry(bubble, prompt))
        if offline:
            bubble.add_action("Diagnostics", self._open_diagnostics)

    def _offer_regenerate(self, bubble: MessageBubble, prompt: str) -> None:
        """Put "Regenerate" on the newest answer only."""
        old = getattr(self, "_regenerate_btn", None)
        if old is not None:
            old.set_visible(False)
        self._regenerate_btn = bubble.add_action("Regenerate", lambda: self._retry(bubble, prompt))
        self._regenerate_btn.set_tooltip_text("Ask again for a new answer")

    def _retry(self, bubble: MessageBubble, prompt: str) -> None:
        if self._streaming or not prompt:
            return
        self._messages_box.remove(bubble)
        self._start_request(prompt)

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

    def _on_chunk(self, chunk: str, request_id: int) -> bool:
        if self._requests.is_current(request_id) and self._first_token_at is None:
            self._first_token_at = time.monotonic()
        if self._requests.is_current(request_id) and self._stream_bubble is not None:
            self._stream_bubble.append_text(chunk)
            self._scroll_to_bottom()
        return False

    def _on_stream_done(
        self, full_text: str, request_id: int, sources: list[str] | None = None
    ) -> bool:
        if not self._requests.finish(request_id):
            return False  # cancelled; the bubble already says so
        self._set_streaming(False)
        self._refresh_context()
        if self._stream_bubble is not None:
            bubble = self._stream_bubble
            self._stream_bubble = None
            body, error = split_error(full_text)
            if error is None:
                bubble.set_final_text(full_text)
                now = time.monotonic()
                first = self._first_token_at
                bubble.set_meta(
                    answer_meta(
                        self._client.model,
                        now - self._stream_started,
                        None if first is None else first - self._stream_started,
                    )
                )
                bubble.set_sources(sources or [])
                self._offer_regenerate(bubble, self._stream_prompt)
            else:
                self._show_bubble_error(
                    bubble, f"{body}\n\n{error}" if body else error, offline=False
                )
        return False

    # ------------------------------------------------------------------
    # Visibility toggle
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        """Show the window if hidden, hide it if visible."""
        if self.get_visible():
            self.set_visible(False)
        else:
            self._refresh_context()
            self._refresh_status()
            self.present()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        """Cancel streaming and clean up resources on window close."""
        self._stream_cancelled = True
