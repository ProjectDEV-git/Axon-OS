"""Intent Bar main window — streaming AI responses with Axon OS design system."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..ollama_client import OllamaClient
from ..spaces_manager import SpacesManager

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "services"))
from service_utils import safe_exec

try:
    import dbus
except ImportError:  # pragma: no cover - dbus ships on Axon images
    dbus = None

# ---------------------------------------------------------------------------
# Axon OS Design System — embedded CSS
# ---------------------------------------------------------------------------

_CSS = b"""
/* ---- Window shell ---- */
.intent-bar-window {
    background-color: #09090f;
    border-radius: 20px;
    border: 1px solid #3a3a58;
    box-shadow: 0 8px 40px rgba(0, 0, 0, 0.8);
}

/* ---- Entry field ---- */
.intent-entry {
    background-color: transparent;
    color: #e8e8f4;
    font-size: 20px;
    caret-color: #8b5cf6;
    border: none;
    padding: 8px 4px;
    box-shadow: none;
    outline: none;
}

.intent-entry:focus {
    outline: none;
    box-shadow: none;
}

/* ---- Separators ---- */
.entry-sep {
    background-color: #2a2a42;
    min-height: 1px;
}

/* ---- Quick-action chips ---- */
.intent-chip {
    background-color: rgba(139, 92, 246, 0.08);
    color: #8b5cf6;
    border: 1px solid rgba(139, 92, 246, 0.35);
    border-radius: 9999px;
    padding: 4px 14px;
    font-size: 12px;
}

.intent-chip:hover {
    background-color: #8b5cf6;
    color: #ffffff;
}

/* ---- Response text ---- */
.response-label {
    color: #9090b8;
    font-size: 13px;
}

/* ---- Space badge ---- */
.space-badge {
    background-color: rgba(139, 92, 246, 0.15);
    color: #8b5cf6;
    border-radius: 9999px;
    padding: 2px 12px;
    font-size: 11px;
    font-weight: bold;
}

/* ---- Model badge ---- */
.model-badge {
    background-color: rgba(34, 211, 238, 0.12);
    color: #22d3ee;
    border-radius: 9999px;
    padding: 2px 12px;
    font-size: 11px;
}

/* ---- Semantic search result rows ---- */
.search-result-row {
    border-radius: 10px;
    padding: 6px 10px;
}

.search-result-row:hover {
    background-color: rgba(139, 92, 246, 0.12);
}

.search-result-path {
    color: #c4b5fd;
    font-size: 13px;
    font-weight: bold;
}

.search-result-snippet {
    color: #70709a;
    font-size: 11px;
}

/* ---- Keyboard hint labels ---- */
.hint-label {
    color: #50507a;
    font-size: 11px;
}

/* ---- Start Ollama button ---- */
.start-ollama-btn {
    color: #8b5cf6;
    border: 1px solid rgba(139, 92, 246, 0.4);
    border-radius: 8px;
    background-color: transparent;
    font-size: 12px;
    padding: 4px 10px;
}

.start-ollama-btn:hover {
    background-color: rgba(139, 92, 246, 0.15);
}
"""

# ---------------------------------------------------------------------------
# System-prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are an intelligent assistant embedded in Axon OS, a Linux desktop environment.
The user is currently in workspace: "{space_name}".

You can perform actions or answer questions.

When the user wants to open an application, respond ONLY with a JSON object (no markdown, no extra text):
  {{"action": "open_app", "app": "<application name>"}}

When the user wants to run a shell command, respond ONLY with:
  {{"action": "run_command", "command": "<shell command>"}}

For all other queries — information, explanations, conversation — respond with plain natural language text.
Keep responses concise and helpful.
"""

# Quick-action chip definitions: (label, prefix inserted into entry)
_CHIPS: list[tuple[str, str]] = [
    ("Find File", "find "),
    ("Open App", "open "),
    ("Run Command", "run "),
    ("Find Files", "find "),
    ("Search Web", "search "),
    ("Summarize", "summarize "),
]

_MAX_HISTORY = 20

# Allowlist of safe characters for AI-generated app names (no paths, no metacharacters)
_SAFE_APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_app_name(name: str) -> str | None:
    """Return sanitized app name, or None if the name is unsafe."""
    name = name.strip()
    if not name or len(name) > 128:
        return None
    if not _SAFE_APP_RE.match(name):
        return None
    return name


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


_css_loaded = False


class IntentBarWindow(Adw.Window):
    """Floating intent bar with streaming AI responses."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        spaces_manager: SpacesManager,
    ) -> None:
        super().__init__()
        self._ollama = ollama_client
        self._spaces = spaces_manager

        # Command history
        self._history: list[str] = []
        self._history_idx: int = -1

        # Accumulator for streaming tokens
        self._stream_parts: list[str] = []
        self._stream_cancelled: bool = False

        self.set_decorated(False)
        self.set_default_size(660, -1)
        self.set_size_request(480, -1)  # minimum width so it still fits small screens
        self.add_css_class("intent-bar-window")

        self._apply_css()
        self._build_ui()
        self._connect_signals()

        # Clean up streaming and resources on destroy
        self.connect("destroy", self._on_destroy)

    # ------------------------------------------------------------------
    # CSS
    # ------------------------------------------------------------------

    def _apply_css(self) -> None:
        global _css_loaded
        if not _css_loaded:
            provider = Gtk.CssProvider()
            provider.load_from_data(_CSS)
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            _css_loaded = True

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Root vertical box
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(16)
        root.set_margin_end(16)
        self.set_content(root)

        # ---- Row 1: top meta row ----------------------------------------
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_row.set_margin_bottom(10)
        root.append(top_row)

        current_space = self._spaces.get_current_space()
        space_name = current_space.name if current_space else "No Space"

        self._space_badge = Gtk.Label(label=space_name)
        self._space_badge.add_css_class("space-badge")
        self._space_badge.set_halign(Gtk.Align.START)
        self._space_badge.set_valign(Gtk.Align.CENTER)
        top_row.append(self._space_badge)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_row.append(spacer)

        self._model_badge = Gtk.Label(label=self._ollama.model)
        self._model_badge.add_css_class("model-badge")
        self._model_badge.set_valign(Gtk.Align.CENTER)
        top_row.append(self._model_badge)

        self._start_ollama_btn = Gtk.Button(label="Start Ollama")
        self._start_ollama_btn.add_css_class("start-ollama-btn")
        self._start_ollama_btn.set_valign(Gtk.Align.CENTER)
        self._start_ollama_btn.set_visible(not self._ollama.is_available())
        top_row.append(self._start_ollama_btn)

        # ---- Row 2: separator -------------------------------------------
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep1.add_css_class("entry-sep")
        sep1.set_margin_bottom(4)
        root.append(sep1)

        # ---- Row 3: entry + spinner -------------------------------------
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        entry_row.set_margin_top(4)
        entry_row.set_margin_bottom(4)
        root.append(entry_row)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Ask anything or type a command...")
        self._entry.set_hexpand(True)
        self._entry.add_css_class("intent-entry")
        entry_row.append(self._entry)

        self._spinner = Gtk.Spinner()
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_size_request(22, 22)
        entry_row.append(self._spinner)

        # ---- Row 4: separator -------------------------------------------
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep2.add_css_class("entry-sep")
        sep2.set_margin_top(4)
        sep2.set_margin_bottom(8)
        root.append(sep2)

        # ---- Row 5: quick-action chips ----------------------------------
        chips_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chips_row.set_margin_bottom(8)
        root.append(chips_row)

        for label, prefix in _CHIPS:
            btn = Gtk.Button(label=label)
            btn.add_css_class("intent-chip")
            btn.set_valign(Gtk.Align.CENTER)
            # Capture `prefix` by default arg
            btn.connect("clicked", lambda _b, p=prefix: self._on_chip_clicked(p))
            chips_row.append(btn)

        # ---- Row 6: response label --------------------------------------
        self._response_label = Gtk.Label()
        self._response_label.set_wrap(True)
        self._response_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._response_label.set_xalign(0.0)
        self._response_label.set_selectable(True)
        self._response_label.add_css_class("response-label")
        self._response_label.set_margin_top(2)
        self._response_label.set_margin_bottom(8)
        self._response_label.set_visible(False)
        root.append(self._response_label)

        # ---- Row 6b: semantic search results ----------------------------
        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._results_box.set_margin_bottom(8)
        self._results_box.set_visible(False)
        root.append(self._results_box)

        # ---- Row 7: keyboard hint row -----------------------------------
        hint_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        hint_row.set_margin_top(4)
        root.append(hint_row)

        for hint_text in ("Return  Submit", "Esc  Close", "Up/Down  History"):
            lbl = Gtk.Label(label=hint_text)
            lbl.add_css_class("hint-label")
            lbl.set_valign(Gtk.Align.CENTER)
            hint_row.append(lbl)

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._entry.connect("activate", self._on_activate)
        self._start_ollama_btn.connect("clicked", self._on_start_ollama)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    # ------------------------------------------------------------------
    # Key handler
    # ------------------------------------------------------------------

    def _on_key_pressed(
        self,
        _ctrl: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Any,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True

        if keyval == Gdk.KEY_Up:
            self._history_prev()
            return True

        if keyval == Gdk.KEY_Down:
            self._history_next()
            return True

        return False

    # ------------------------------------------------------------------
    # History navigation
    # ------------------------------------------------------------------

    def _history_prev(self) -> None:
        """Navigate to the previous history entry."""
        if not self._history:
            return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
        self._entry.set_text(self._history[self._history_idx])
        self._entry.set_position(-1)

    def _history_next(self) -> None:
        """Navigate to the next history entry (or clear if at newest)."""
        if self._history_idx <= 0:
            self._history_idx = -1
            self._entry.set_text("")
            return
        self._history_idx -= 1
        self._entry.set_text(self._history[self._history_idx])
        self._entry.set_position(-1)

    def _push_history(self, query: str) -> None:
        """Push a query onto the history stack (dedup head, cap at max)."""
        if self._history and self._history[0] == query:
            return
        self._history.insert(0, query)
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[:_MAX_HISTORY]

    # ------------------------------------------------------------------
    # Quick-action chips
    # ------------------------------------------------------------------

    def _on_chip_clicked(self, prefix: str) -> None:
        """Prepend the chip prefix to the current entry text."""
        current = self._entry.get_text()
        if current.startswith(prefix):
            return
        self._entry.set_text(prefix + current)
        self._entry.set_position(-1)
        self._entry.grab_focus()

    # ------------------------------------------------------------------
    # Query submission
    # ------------------------------------------------------------------

    def _on_activate(self, entry: Gtk.Entry) -> None:
        query = entry.get_text().strip()
        if not query:
            return

        # "find <something>" routes to the local semantic index instead of
        # the LLM — works even when Ollama is offline (keyword fallback).
        if query.lower().startswith("find ") and len(query) > 5:
            self._push_history(query)
            self._history_idx = -1
            self._start_semantic_search(query[5:].strip())
            return

        if not self._ollama.is_available():
            self._show_error("[error] Ollama is not running. Click 'Start Ollama'.")
            self._start_ollama_btn.set_visible(True)
            return

        # Prepare UI for streaming
        self._spinner.start()
        self._response_label.set_text("")
        self._response_label.set_visible(False)
        entry.set_sensitive(False)
        self._push_history(query)
        self._history_idx = -1

        thread = threading.Thread(
            target=self._do_query,
            args=(query,),
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # Streaming implementation
    # ------------------------------------------------------------------

    def _do_query(self, query: str) -> None:
        """Run the Ollama streaming query in a background thread."""
        system = self._build_system_prompt()
        self._stream_parts = []
        try:
            for token in self._ollama.generate_stream(query, system=system):
                if self._stream_cancelled:
                    break
                self._stream_parts.append(token)
                GLib.idle_add(self._append_token, token)
        except Exception as exc:
            if not self._stream_cancelled:
                GLib.idle_add(self._append_token, f"\n[error] {exc}")
        if not self._stream_cancelled:
            GLib.idle_add(self._finish_stream, "".join(self._stream_parts))

    def _append_token(self, token: str) -> bool:
        """Append a streaming token to the response label (GTK main thread)."""
        self._response_label.set_text(self._response_label.get_text() + token)
        self._response_label.set_visible(True)
        return False

    def _finish_stream(self, full: str) -> bool:
        """Called when streaming is complete (GTK main thread)."""
        self._spinner.stop()
        self._entry.set_sensitive(True)
        self._entry.grab_focus()

        # Attempt action JSON parsing
        stripped = full.strip()
        try:
            action: Any = json.loads(stripped)
            if isinstance(action, dict) and "action" in action:
                self._execute_action(action)
        except (json.JSONDecodeError, ValueError):
            pass

        return False

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def _execute_action(self, action: dict[str, Any]) -> None:
        """Dispatch an action returned by the model."""
        action_type: str = action.get("action", "")

        if action_type == "open_app":
            app_name: str = action.get("app", "")
            safe_name = _validate_app_name(app_name)
            if safe_name:
                proc = subprocess.Popen(
                    [safe_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                threading.Thread(target=proc.wait, daemon=True).start()

        elif action_type == "run_command":
            command: str = action.get("command", "")
            if command:
                safe_exec(command)

    # ------------------------------------------------------------------
    # Semantic file search (org.axonos.Search)
    # ------------------------------------------------------------------

    def _start_semantic_search(self, query: str) -> None:
        self._spinner.start()
        self._clear_results()
        self._response_label.set_text(f'Searching your files for "{query}"...')
        self._response_label.set_visible(True)
        threading.Thread(target=self._do_semantic_search, args=(query,), daemon=True).start()

    def _do_semantic_search(self, query: str) -> None:
        results: list[dict[str, Any]] = []
        error = ""
        if dbus is None:
            error = "python3-dbus is not available"
        else:
            try:
                bus = dbus.SessionBus()
                obj = bus.get_object("org.axonos.Search", "/org/axonos/Search")
                iface = dbus.Interface(obj, "org.axonos.Search")
                results = json.loads(iface.Query(query, 6, timeout=30))
            except Exception as exc:
                error = str(exc)
        GLib.idle_add(self._show_search_results, query, results, error)

    def _show_search_results(self, query: str, results: list[dict[str, Any]], error: str) -> bool:
        self._spinner.stop()
        self._clear_results()
        if error:
            self._show_error(f"[error] Semantic search unavailable: {error}")
            return False
        if not results:
            self._show_error(f'No matching files for "{query}".')
            return False

        backend = results[0].get("backend", "vector")
        label = "semantic" if backend == "vector" else "keyword"
        self._response_label.set_text(f"Top {len(results)} {label} matches — click to open:")
        self._response_label.set_visible(True)

        home = GLib.get_home_dir()
        for item in results:
            path = str(item.get("path", ""))
            display = path.replace(home, "~", 1) if home else path
            row_btn = Gtk.Button()
            row_btn.add_css_class("search-result-row")
            row_btn.set_has_frame(False)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            path_lbl = Gtk.Label(label=display, xalign=0.0)
            path_lbl.add_css_class("search-result-path")
            path_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            inner.append(path_lbl)
            snippet = " ".join(str(item.get("snippet", "")).split())[:140]
            if snippet:
                snip_lbl = Gtk.Label(label=snippet, xalign=0.0)
                snip_lbl.add_css_class("search-result-snippet")
                snip_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                inner.append(snip_lbl)
            row_btn.set_child(inner)
            row_btn.connect("clicked", lambda _b, p=path: self._open_search_result(p))
            self._results_box.append(row_btn)
        self._results_box.set_visible(True)
        return False

    def _open_search_result(self, path: str) -> None:
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path, None), None)
        self.close()

    def _clear_results(self) -> None:
        child = self._results_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._results_box.remove(child)
            child = nxt
        self._results_box.set_visible(False)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        current_space = self._spaces.get_current_space()
        space_name = current_space.name if current_space else "Default"
        return _SYSTEM_PROMPT_TEMPLATE.format(space_name=space_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _show_error(self, text: str) -> None:
        self._response_label.set_text(text)
        self._response_label.set_visible(True)

    def _on_start_ollama(self, _btn: Gtk.Button) -> None:
        """Attempt to launch the Ollama daemon."""
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._start_ollama_btn.set_sensitive(False)
        self._show_error("Starting Ollama... please wait a moment and try again.")

    # ------------------------------------------------------------------
    # Semantic File Search Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        """Cancel streaming and clean up resources on window close."""
        self._stream_cancelled = True
