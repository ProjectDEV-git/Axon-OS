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
from pathlib import Path
from typing import Optional, Iterator, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

# ---------------------------------------------------------------------------
# Import OllamaClient from the intent-bar app, falling back to a built-in
# stub if the module is not yet populated.
# ---------------------------------------------------------------------------
_intent_bar_path = str(Path(__file__).resolve().parents[2] / "intent-bar")
if _intent_bar_path not in sys.path:
    sys.path.insert(0, _intent_bar_path)

try:
    from ollama_client import OllamaClient  # type: ignore[import]
except ImportError:
    import urllib.request
    import urllib.error

    class OllamaClient:  # type: ignore[no-redef]
        """Minimal Ollama client used when intent-bar's module is absent."""

        BASE_URL: str = "http://localhost:11434"

        def __init__(self, model: str = "llama3.2:3b") -> None:
            self.model = model

        def is_available(self) -> bool:
            try:
                urllib.request.urlopen(f"{self.BASE_URL}/api/tags", timeout=2)
                return True
            except Exception:
                return False

        def list_models(self) -> list[str]:
            """Return list of locally available model names."""
            try:
                with urllib.request.urlopen(
                    f"{self.BASE_URL}/api/tags", timeout=5
                ) as resp:
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
            # Extract the latest user content as prompt fallback
            prompt = messages[-1].get("content", "") if messages else ""

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
                with urllib.request.urlopen(req, timeout=120) as resp:
                    for raw_line in resp:
                        line = raw_line.decode().strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            chunk = (
                                obj.get("message", {}).get("content", "")
                                or obj.get("response", "")
                            )
                            if chunk:
                                yield chunk
                            if obj.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                yield f"\n[Error: {exc}]"


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
        self._inner.append(self._label)

        # Copy button row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_row.set_halign(Gtk.Align.END)
        self._copy_btn = Gtk.Button(label="⎘")
        self._copy_btn.add_css_class("copy-btn")
        self._copy_btn.add_css_class("flat")
        self._copy_btn.set_tooltip_text("Copy to clipboard")
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

    def append_text(self, chunk: str) -> None:
        """Append *chunk* to the bubble label (used during streaming)."""
        self._text += chunk
        if self._role == "assistant":
            try:
                self._label.set_markup(_apply_markup(self._text))
            except Exception:
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

    _CSS: bytes = b"""
    .panel-window {
        background: linear-gradient(180deg, #0d0d1e 0%, #0a0a16 100%);
        border: 1px solid #2a2a42;
        border-radius: 12px;
        box-shadow:
            0 20px 60px rgba(0, 0, 0, 0.7),
            0 4px 16px rgba(0, 0, 0, 0.4),
            inset 0 1px 0 rgba(255, 255, 255, 0.05);
    }
    .panel-header {
        background: linear-gradient(180deg, #0a0a16 0%, #0d0d1e 100%);
        border-bottom: 1px solid #2a2a42;
        border-radius: 12px 12px 0 0;
    }
    .bubble-user {
        background-color: #16162a;
        border-radius: 12px 12px 4px 12px;
        border-left: 3px solid #8b5cf6;
        box-shadow:
            0 2px 8px rgba(0, 0, 0, 0.3),
            inset 0 1px 0 rgba(139, 92, 246, 0.08);
        color: #e8e8f4;
        padding: 11px 15px;
    }
    .bubble-assistant {
        background-color: #0f0f1c;
        border-radius: 4px 12px 12px 12px;
        border: 1px solid #2a2a42;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
        color: #e8e8f4;
        padding: 11px 15px;
    }
    .model-badge {
        background: linear-gradient(135deg, #9d6ff8 0%, #8b5cf6 100%);
        border-radius: 9999px;
        color: white;
        font-size: 11px;
        font-weight: 600;
        padding: 3px 11px;
        box-shadow: 0 2px 6px rgba(139, 92, 246, 0.35);
    }
    .thinking-label {
        color: #8b5cf6;
        font-size: 13px;
        font-style: italic;
    }
    .panel-input-area {
        background: linear-gradient(180deg, transparent 0%, #080810 30%, #080810 100%);
        border-top: 1px solid #2a2a42;
        padding: 14px;
        border-radius: 0 0 12px 12px;
    }
    .copy-btn {
        background-color: transparent;
        color: #50507a;
        font-size: 11px;
        border: none;
        padding: 2px 6px;
        border-radius: 6px;
        transition: all 150ms ease;
    }
    .copy-btn:hover {
        color: #8b5cf6;
        background-color: rgba(139, 92, 246, 0.12);
    }
    .quick-chip {
        background-color: rgba(139, 92, 246, 0.07);
        color: #8b5cf6;
        border: 1px solid rgba(139, 92, 246, 0.28);
        border-radius: 9999px;
        padding: 4px 13px;
        font-size: 12px;
        transition: all 150ms ease;
    }
    .quick-chip:hover {
        background: linear-gradient(135deg, #9d6ff8 0%, #8b5cf6 100%);
        color: white;
        border-color: #8b5cf6;
        box-shadow: 0 2px 8px rgba(139, 92, 246, 0.3);
    }
    .clear-btn {
        color: #50507a;
        background: transparent;
        border: none;
        font-size: 16px;
        border-radius: 6px;
        padding: 4px 6px;
        transition: all 150ms ease;
    }
    .clear-btn:hover {
        color: #ff5f57;
        background-color: rgba(255, 95, 87, 0.1);
    }
    """

    _QUICK_CHIPS: list[str] = [
        "Explain Error",
        "Summarize",
        "Write Code",
        "Fix Bug",
        "Translate",
    ]

    _DEFAULT_MODELS: list[str] = [
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
        self._stream_bubble: Optional[MessageBubble] = None
        self._conv_id: str = ""

        # Apply CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(self._CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.set_default_size(420, 860)
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

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)

        # Model dropdown
        self._model_store = Gtk.StringList()
        for m in self._DEFAULT_MODELS:
            self._model_store.append(m)

        self._model_dropdown = Gtk.DropDown(model=self._model_store)
        self._model_dropdown.set_selected(0)
        self._model_dropdown.connect("notify::selected", self._on_model_changed)

        # Clear history button
        self._clear_btn = Gtk.Button(label="🗑")
        self._clear_btn.add_css_class("clear-btn")
        self._clear_btn.add_css_class("flat")
        self._clear_btn.set_tooltip_text("Clear conversation")
        self._clear_btn.connect("clicked", self._on_clear_clicked)

        # Model badge
        self._model_badge = Gtk.Label(label=self._client.model)
        self._model_badge.add_css_class("model-badge")

        header.append(title_label)
        header.append(self._model_dropdown)
        header.append(self._clear_btn)
        header.append(self._model_badge)
        root.append(header)

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
                lambda _btn, lbl=chip_label: self._send_message(lbl + ": "),
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

        # ---- Input area ----------------------------------------------------
        input_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        input_area.add_css_class("panel-input-area")
        input_area.set_spacing(8)

        self._entry = Gtk.Entry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text("Ask anything…")
        self._entry.connect("activate", self._on_entry_activate)

        send_btn = Gtk.Button(label="Send")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self._on_send_clicked)

        input_area.append(self._entry)
        input_area.append(send_btn)
        root.append(input_area)

        # Escape key hides the window
        esc_ctrl = Gtk.EventControllerKey()
        esc_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(esc_ctrl)

        # Fetch real model list in background
        threading.Thread(target=self._fetch_models, daemon=True).start()

        # Initialize D-Bus conversation session
        self._init_conversation()

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def _fetch_models(self) -> None:
        """Fetch available models from Ollama and update the dropdown."""
        try:
            models = self._client.list_models()
            if models:
                GLib.idle_add(self._populate_model_dropdown, models)
        except Exception:
            pass

    def _populate_model_dropdown(self, models: list[str]) -> bool:
        # Build a combined deduplicated list
        existing = set(self._DEFAULT_MODELS)
        all_models: list[str] = list(self._DEFAULT_MODELS)
        for m in models:
            if m not in existing:
                all_models.append(m)
                existing.add(m)

        # Rebuild the StringList
        new_store = Gtk.StringList()
        for m in all_models:
            new_store.append(m)
        self._model_store = new_store
        self._model_dropdown.set_model(new_store)
        self._model_dropdown.set_selected(0)
        return False

    def _on_model_changed(self, dropdown: Gtk.DropDown, _param: Any) -> None:
        selected_item = dropdown.get_selected_item()
        if selected_item is not None:
            model_name: str = selected_item.get_string()
            self._client.model = model_name
            self._model_badge.set_label(model_name)

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
            self.set_visible(False)
            return True
        return False

    # ------------------------------------------------------------------
    # Input handlers
    # ------------------------------------------------------------------

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
            convs = self._client.list_conversations()
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

        self._entry.set_text("")
        self._add_bubble("user", text)

        if not self._client.is_available():
            err_text = "Axon Brain service is offline or Ollama is not running."
            self._add_bubble("assistant", err_text)
            return

        ctx_string = self._ctx_reader.build_context_string()
        self._set_streaming(True)
        self._stream_bubble = self._add_bubble("assistant", "")

        model = self._client.model
        thread = threading.Thread(
            target=self._stream_response,
            args=(text, ctx_string, model),
            daemon=True,
        )
        thread.start()

    def _set_streaming(self, active: bool) -> None:
        self._streaming = active
        self._spinner.set_visible(active)
        if active:
            self._spinner.start()
        else:
            self._spinner.stop()

    def _stream_response(self, text: str, ctx: str, model: str) -> None:
        accumulated = ""
        try:
            for chunk in self._client.send_message_stream(self._conv_id, text, model=model):
                accumulated += chunk
                GLib.idle_add(self._on_chunk, chunk)
        except Exception as exc:
            error_text = f"\n[Error: {exc}]"
            accumulated += error_text
            GLib.idle_add(self._on_chunk, error_text)
        finally:
            GLib.idle_add(self._on_stream_done, accumulated)

    def _on_chunk(self, chunk: str) -> bool:
        if self._stream_bubble is not None:
            self._stream_bubble.append_text(chunk)
            self._scroll_to_bottom()
        return False

    def _on_stream_done(self, full_text: str) -> bool:
        self._set_streaming(False)
        if self._stream_bubble is not None:
            self._stream_bubble.set_final_text(full_text)
            self._stream_bubble = None
        return False

    # ------------------------------------------------------------------
    # Visibility toggle
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        """Show the window if hidden, hide it if visible."""
        if self.get_visible():
            self.set_visible(False)
        else:
            self.present()
