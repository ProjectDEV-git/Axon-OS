"""Intent Bar main window — streaming AI responses with Axon OS design system."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, ClassVar

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from ..ollama_client import OllamaClient
from ..spaces_manager import SpacesManager

# services/ sits next to apps/ in the source tree and in the .deb
# (/usr/lib/axon), and inside the app directory for install.sh
# (~/.local/share/axon-os/services).
for _services in (Path(__file__).resolve().parents[3], Path(__file__).resolve().parents[2]):
    if (_services / "services").is_dir():
        sys.path.insert(0, str(_services / "services"))
        break
from constants import AXON_DIR
from service_utils import confirm_and_exec
from suggestions import SETTINGS_PANELS, Suggestion, build_suggestions, default_index
from suggestions import describe as describe_suggestion

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from axon_gtk_utils import announce, load_app_css, monitor_size
from i18n import _
from ui_logic import (
    EXAMPLE_PROMPTS,
    SHOW,
    STAGE_CONFIRM,
    STAGE_CONNECTING,
    STAGE_SEARCHING,
    STAGE_WAITING,
    ModelOrigin,
    RequestTracker,
    announcement,
    classify_stream_prefix,
    describe_action,
    escape_action,
    format_command_output,
    intent_bar_width,
    load_history,
    origin_tooltip,
    parse_action,
    push_history,
    recent_for_empty_state,
    routing_origin,
    save_history,
    split_error,
    stage_for_stream,
)

try:
    import dbus
except ImportError:  # pragma: no cover - dbus ships on Axon images
    dbus = None

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
    ("Search Web", "search "),
    ("Summarize", "summarize "),
]

_MAX_HISTORY = 20
# Queries survive closing the bar so the empty state can offer them again
_HISTORY_FILE = AXON_DIR / "intent_history.json"
_RESPONSE_MAX_HEIGHT = 320  # px; longer answers scroll

# Allowlist of safe characters for AI-generated app names (no paths, no metacharacters)
_SAFE_APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _wrapping_row(spacing: int) -> Gtk.FlowBox:
    """A row of small widgets that wraps onto more lines when space runs out."""
    row = Gtk.FlowBox()
    row.set_selection_mode(Gtk.SelectionMode.NONE)
    row.set_column_spacing(spacing)
    row.set_row_spacing(4)
    row.set_max_children_per_line(12)
    # At least three per line: otherwise GTK sizes the window for one item
    # per line and the bar keeps a tall blank band
    row.set_min_children_per_line(3)
    row.set_homogeneous(False)
    return row


def _installed_apps() -> list[tuple[str, str, str]]:
    """Visible installed apps as ``(desktop id, name, keywords)``."""
    apps = []
    for info in Gio.AppInfo.get_all():
        if not info.should_show() or not info.get_id():
            continue
        keywords = ""
        if isinstance(info, Gio.DesktopAppInfo):
            keywords = " ".join(info.get_keywords() or [])
        apps.append((info.get_id(), info.get_display_name() or info.get_name(), keywords))
    return apps


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
        self._history: list[str] = load_history(_HISTORY_FILE, _MAX_HISTORY)
        self._history_idx: int = -1

        # Accumulator for streaming tokens
        self._stream_parts: list[str] = []
        self._stream_cancelled: bool = False
        # The running AI/search request; Escape or Stop cancels it
        self._requests = RequestTracker()
        # Suggestions for what is typed, and an AI action waiting for Enter
        self._apps = _installed_apps()
        self._suggestions: list[Suggestion] = []
        self._pending_action: dict[str, Any] | None = None

        self.set_decorated(False)
        # clamp(480 px, 45% of the monitor, 820 px)
        self.set_default_size(intent_bar_width(monitor_size(self.get_display())[0]), -1)
        self.set_size_request(480, -1)  # minimum width so it still fits small screens
        self.add_css_class("intent-bar-window")

        self._apply_css()
        self._build_ui()
        self._connect_signals()

        # Clean up streaming and resources on destroy
        self.connect("destroy", self._on_destroy)
        self.connect("map", lambda *_a: self._queue_fit())

    # ------------------------------------------------------------------
    # CSS
    # ------------------------------------------------------------------

    def _apply_css(self) -> None:
        load_app_css(Path(__file__).with_name("intent-bar.css"), self.get_display())

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

        # Where requests go: "Local · llama3.2:3b" or "Cloud · OpenAI · gpt-4.1".
        # Filled in by _check_available_async; never assumed local.
        self._model_badge = Gtk.Label(label=_("Model: checking…"))
        self._model_badge.add_css_class("model-badge")
        self._model_badge.set_valign(Gtk.Align.CENTER)
        top_row.append(self._model_badge)

        # Recovery button, shown next to the "not running" error. The check
        # is a D-Bus call, so it runs in _check_available_async, not here.
        self._start_ollama_btn = Gtk.Button(label=_("Start Ollama"))
        self._start_ollama_btn.add_css_class("start-ollama-btn")
        self._start_ollama_btn.set_valign(Gtk.Align.START)
        self._start_ollama_btn.set_visible(False)

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
        self._entry.set_placeholder_text(_("Ask anything or type a command..."))
        self._entry.set_hexpand(True)
        self._entry.add_css_class("intent-entry")
        entry_row.append(self._entry)

        self._spinner = Gtk.Spinner()
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_size_request(22, 22)
        entry_row.append(self._spinner)

        # What the running request is doing ("Waiting for model…")
        self._stage_label = Gtk.Label()
        self._stage_label.add_css_class("stage-label")
        self._stage_label.set_valign(Gtk.Align.CENTER)
        self._stage_label.set_visible(False)
        entry_row.append(self._stage_label)

        self._stop_btn = Gtk.Button.new_from_icon_name("process-stop-symbolic")
        self._stop_btn.add_css_class("flat")
        self._stop_btn.set_valign(Gtk.Align.CENTER)
        self._stop_btn.set_tooltip_text(_("Stop (Esc)"))
        self._stop_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Stop request")])
        self._stop_btn.set_visible(False)
        entry_row.append(self._stop_btn)

        # ---- Row 4: separator -------------------------------------------
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep2.add_css_class("entry-sep")
        sep2.set_margin_top(4)
        sep2.set_margin_bottom(8)
        root.append(sep2)

        # ---- Row 5: quick-action chips ----------------------------------
        # FlowBox so the chips wrap with Large Text instead of widening the bar
        chips_row = _wrapping_row(spacing=8)
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

        # Long answers scroll instead of pushing the bar off-screen
        self._response_scroll = Gtk.ScrolledWindow()
        self._response_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._response_scroll.set_max_content_height(_RESPONSE_MAX_HEIGHT)
        self._response_scroll.set_propagate_natural_height(True)
        self._error_icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        self._error_icon.add_css_class("response-error-icon")
        self._error_icon.set_valign(Gtk.Align.START)
        self._error_icon.set_visible(False)
        self._response_label.set_hexpand(True)
        response_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        response_row.append(self._error_icon)
        response_row.append(self._response_label)
        response_row.append(self._start_ollama_btn)
        self._response_scroll.set_child(response_row)
        self._response_label.bind_property(
            "visible", self._response_scroll, "visible", GObject.BindingFlags.SYNC_CREATE
        )
        root.append(self._response_scroll)
        threading.Thread(target=self._check_available_async, daemon=True).start()

        # ---- Row 6b: semantic search results ----------------------------
        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._results_box.set_margin_bottom(8)
        self._results_box.set_visible(False)
        root.append(self._results_box)

        # ---- Row 6c: suggestions while typing ----------------------------
        self._suggest_list = Gtk.ListBox()
        self._suggest_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._suggest_list.set_header_func(self._suggestion_header)
        self._suggest_list.update_property([Gtk.AccessibleProperty.LABEL], [_("Suggestions")])
        self._suggest_list.connect("row-activated", self._on_suggestion_activated)
        self._suggest_list.connect("row-selected", lambda *_a: self._update_preview())
        self._suggest_list.set_visible(False)
        root.append(self._suggest_list)
        # What Enter will do ("↵ Open Firefox")
        self._preview_label = Gtk.Label(xalign=0.0)
        self._preview_label.add_css_class("hint-label")
        self._preview_label.set_visible(False)
        root.append(self._preview_label)

        # ---- Row 6d: empty state (before the user types) -----------------
        self._empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._empty_box.set_margin_bottom(8)
        root.append(self._empty_box)
        self._build_empty_state()

        # ---- Row 7: keyboard hint row -----------------------------------
        hint_row = _wrapping_row(spacing=16)
        hint_row.set_margin_top(4)
        root.append(hint_row)

        for hint_text in ("Return  Submit", "Esc  Stop / Close", "Up/Down  History"):
            lbl = Gtk.Label(label=hint_text)
            lbl.add_css_class("hint-label")
            lbl.set_valign(Gtk.Align.CENTER)
            hint_row.append(lbl)

    def _build_empty_state(self) -> None:
        """Example prompts and recent queries; clicking one fills the entry."""
        child = self._empty_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._empty_box.remove(child)
            child = nxt
        sections = (
            ("Try", list(EXAMPLE_PROMPTS)),
            ("Recent", recent_for_empty_state(self._history)),
        )
        for title, items in sections:
            if not items:
                continue
            heading = Gtk.Label(label=title, xalign=0.0)
            heading.add_css_class("hint-label")
            self._empty_box.append(heading)
            for text in items:
                btn = Gtk.Button(label=text)
                btn.add_css_class("flat")
                btn.add_css_class("search-result-row")
                btn.set_halign(Gtk.Align.START)
                btn.connect("clicked", lambda _b, t=text: self._fill_entry(t))
                self._empty_box.append(btn)

    def _fill_entry(self, text: str) -> None:
        self._entry.set_text(text)
        self._entry.grab_focus()
        self._entry.set_position(-1)

    def _update_empty_state(self, *_args: Any) -> None:
        """Show the empty state only while nothing is typed or shown."""
        idle = (
            not self._entry.get_text()
            and not self._requests.busy
            and not self._response_label.get_visible()
            and not self._results_box.get_visible()
        )
        self._empty_box.set_visible(idle)
        self._queue_fit()

    def _queue_fit(self) -> None:
        """Resize to the content after this change has been laid out."""
        if not getattr(self, "_fit_pending", False):
            self._fit_pending = True
            GLib.idle_add(self._fit_height)

    def _fit_height(self) -> bool:
        """Make the window as tall as its content, so it never keeps a blank band.

        GTK 4 windows grow with their content but never shrink by themselves.
        """
        self._fit_pending = False
        width = self.get_default_size()[0]
        if width <= 0:
            width = self.get_width()
        _minimum, natural, _b1, _b2 = self.measure(Gtk.Orientation.VERTICAL, width)
        if natural > 0 and natural != self.get_default_size()[1]:
            self.set_default_size(width, natural)
        return False

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._entry.connect("activate", self._on_activate)
        self._entry.connect("changed", self._update_empty_state)
        self._entry.connect("changed", self._on_entry_changed)
        self._response_label.connect("notify::visible", self._update_empty_state)
        self._results_box.connect("notify::visible", self._update_empty_state)
        self._start_ollama_btn.connect("clicked", self._on_start_ollama)
        self._stop_btn.connect("clicked", lambda _b: self._cancel_request())

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
            # Escape drops a waiting action, then stops a running request;
            # only after that does it close the bar
            if self._pending_action is not None:
                self._pending_action = None
                self._set_response(_("Cancelled"))
            elif escape_action(self._requests.busy) == "cancel":
                self._cancel_request()
            else:
                self.close()
            return True

        # Up/Down move through suggestions while typing, history otherwise
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and self._suggest_list.get_visible():
            self._move_selection(-1 if keyval == Gdk.KEY_Up else 1)
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
        """Put a query at the top of the history and save it."""
        self._history = push_history(self._history, query, _MAX_HISTORY)
        save_history(_HISTORY_FILE, self._history)
        self._build_empty_state()

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
        # Enter confirms an AI action shown as a preview
        if self._pending_action is not None:
            action, self._pending_action = self._pending_action, None
            self._execute_action(action)
            return
        query = entry.get_text().strip()
        if not query:
            return
        selected = self._selected_suggestion()
        if selected is not None and selected.action.get("type") != "ask":
            self._push_history(query)
            self._history_idx = -1
            self._run_suggestion(selected)
            return
        self._hide_suggestions()

        # "find <something>" routes to the local semantic index instead of
        # the LLM — works even when Ollama is offline (keyword fallback).
        if query.lower().startswith("find ") and len(query) > 5:
            self._push_history(query)
            self._history_idx = -1
            self._start_semantic_search(query[5:].strip())
            return

        # Prepare UI for streaming (the availability check runs in the worker)
        request_id = self._requests.start()
        self._set_busy(True)
        self._set_stage(STAGE_CONNECTING)
        self._set_response("")
        self._push_history(query)
        self._history_idx = -1

        thread = threading.Thread(
            target=self._do_query,
            args=(query, request_id),
            daemon=True,
        )
        thread.start()

    def _set_busy(self, busy: bool) -> None:
        """Show or hide the running-request UI (spinner, Stop, locked entry)."""
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()
        self._stop_btn.set_visible(busy)
        self._entry.set_sensitive(not busy)
        if not busy:
            self._set_stage(None)
            self._entry.grab_focus()
        self._update_empty_state()

    def _set_stage(self, stage: str | None, request_id: int | None = None) -> bool:
        """Show the loading stage; ignored for a stale *request_id*."""
        if request_id is not None and not self._requests.is_current(request_id):
            return False
        self._stage_label.set_text(stage or "")
        self._stage_label.set_visible(bool(stage))
        return False

    def _cancel_request(self) -> None:
        """Stop the running request; late results from it are dropped."""
        if not self._requests.cancel():
            return
        self._set_busy(False)
        self._clear_results()
        self._set_response(_("Cancelled"))
        announce(self, announcement("cancelled"))
        cancel = getattr(self._ollama, "cancel", None)
        if cancel is not None:
            threading.Thread(target=cancel, daemon=True).start()

    # ------------------------------------------------------------------
    # Streaming implementation
    # ------------------------------------------------------------------

    def _do_query(self, query: str, request_id: int) -> None:
        """Run the Ollama streaming query in a background thread.

        A reply that may be an action (JSON) is held back until the stream
        ends, so the user sees "Opening Firefox…" instead of raw JSON.
        """
        if not self._ollama.is_available():
            GLib.idle_add(self._on_ai_unavailable, request_id)
            return
        GLib.idle_add(self._set_stage, STAGE_WAITING, request_id)
        system = self._build_system_prompt()
        parts: list[str] = []
        self._stream_parts = parts
        showing = False
        try:
            for token in self._ollama.generate_stream(query, system=system):
                if self._stream_cancelled or not self._requests.is_current(request_id):
                    return
                parts.append(token)
                if showing:
                    GLib.idle_add(self._append_token, token, request_id)
                    continue
                so_far = "".join(parts)
                GLib.idle_add(self._set_stage, stage_for_stream(so_far), request_id)
                if classify_stream_prefix(so_far) == SHOW:
                    showing = True
                    GLib.idle_add(self._append_token, so_far, request_id)
        except Exception as exc:
            parts.append(f"\n[error] {exc}")
        if not self._stream_cancelled:
            GLib.idle_add(self._finish_stream, "".join(parts), request_id)

    def _check_available_async(self) -> None:
        """Worker thread: fill the origin badge; show "Start Ollama" if offline."""
        routing_info = getattr(self._ollama, "routing_info", None)
        providers, tiers = routing_info() if routing_info is not None else (None, {})
        GLib.idle_add(self._set_origin, routing_origin(self._ollama.model, tiers, providers))
        if not self._ollama.is_available():
            GLib.idle_add(self._show_ai_offline)

    def _set_origin(self, origin: ModelOrigin) -> bool:
        self._model_badge.set_text(origin.label)
        self._model_badge.set_tooltip_text(origin_tooltip(origin))
        return False

    def _on_ai_unavailable(self, request_id: int) -> bool:
        """GTK thread: the query found the AI offline."""
        if not self._requests.finish(request_id):
            return False
        self._set_busy(False)
        self._show_ai_offline()
        return False

    def _show_ai_offline(self) -> bool:
        """Show the offline error with its recovery button."""
        if self._requests.busy:
            return False  # don't overwrite a request that is running
        self._show_error(_("Ollama is not running."))
        announce(self, announcement("error", "Ollama is not running."), important=True)
        self._start_ollama_btn.set_sensitive(True)
        self._start_ollama_btn.set_visible(True)
        return False

    def _append_token(self, token: str, request_id: int) -> bool:
        """Append a streaming token to the response label (GTK main thread)."""
        if not self._requests.is_current(request_id):
            return False
        self._set_response(self._response_label.get_text() + token)
        return False

    def _finish_stream(self, full: str, request_id: int) -> bool:
        """Called when streaming is complete (GTK main thread)."""
        if not self._requests.finish(request_id):
            return False  # cancelled: the user already saw "Cancelled"
        self._set_busy(False)

        action = parse_action(full)
        if action is not None:
            if action.get("action") == "run_command":
                # The confirmation card opens next; nothing runs without it
                self._set_response(f"{STAGE_CONFIRM}: {action.get('command', '')}")
                self._execute_action(action)
            else:
                # Show what will happen and wait for Enter (Esc cancels)
                self._pending_action = action
                self._set_response(
                    _("{action} Press Enter to confirm, Esc to cancel.").format(
                        action=describe_action(action)
                    )
                )
            return False
        body, error = split_error(full)
        if error is not None:
            self._show_error(f"{body}\n\n{error}" if body else error)
            announce(self, announcement("error", error), important=True)
        elif body:
            # Covers replies that were held back as possible JSON
            self._set_response(body)
            announce(self, announcement("answer", body))

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
            # Desktop IDs only: never run an arbitrary binary named by the model
            if safe_name and shutil.which("gtk-launch"):
                proc = subprocess.Popen(
                    ["gtk-launch", safe_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                threading.Thread(target=proc.wait, daemon=True).start()

        elif action_type == "run_command":
            command: str = action.get("command", "")
            if command:
                self._run_command(command)

    def _run_command(self, command: str) -> None:
        """Allowlist + confirmation card, then show the output here."""
        self._set_response(f"{STAGE_CONFIRM}: {command}")

        def worker() -> None:
            # The confirmation card blocks, so keep it off the GTK thread
            proc = confirm_and_exec(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            if proc is None:
                GLib.idle_add(self._command_done, "Not run.")
                return
            try:
                out, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                out = "(stopped after 15 seconds)"
            GLib.idle_add(self._command_done, format_command_output(out or ""))

        threading.Thread(target=worker, daemon=True).start()

    def _command_done(self, text: str) -> bool:
        self._set_response(text)
        announce(self, announcement("command", text))
        return False

    # ------------------------------------------------------------------
    # Suggestions while typing
    # ------------------------------------------------------------------

    def _on_entry_changed(self, _entry: Gtk.Entry) -> None:
        self._pending_action = None  # typing changes the request
        self._update_suggestions()

    def _update_suggestions(self) -> None:
        query = self._entry.get_text()
        spaces = (
            [s.name for s in self._spaces.get_spaces()]
            if hasattr(self._spaces, "get_spaces")
            else []
        )
        self._suggestions = build_suggestions(query, self._apps, spaces)
        self._suggest_list.remove_all()
        for suggestion in self._suggestions:
            self._suggest_list.append(self._suggestion_row(suggestion))
        visible = bool(self._suggestions) and not self._requests.busy
        self._suggest_list.set_visible(visible)
        default = self._suggest_list.get_row_at_index(default_index(self._suggestions))
        if visible and default is not None:
            self._suggest_list.select_row(default)
        self._update_preview()
        self._queue_fit()

    def _hide_suggestions(self) -> None:
        self._suggest_list.set_visible(False)
        self._preview_label.set_visible(False)
        self._queue_fit()

    @staticmethod
    def _suggestion_row(suggestion: Suggestion) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(6)
        box.set_margin_end(6)
        title = Gtk.Label(label=suggestion.title, xalign=0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        subtitle = Gtk.Label(label=suggestion.subtitle, xalign=1.0)
        subtitle.add_css_class("hint-label")
        subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(title)
        box.append(subtitle)
        row.set_child(box)
        row.update_property([Gtk.AccessibleProperty.LABEL], [suggestion.accessible_label])
        return row

    def _suggestion_header(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        index = row.get_index()
        category = self._suggestions[index].category if index < len(self._suggestions) else ""
        previous = self._suggestions[before.get_index()].category if before is not None else None
        if category and category != previous:
            header = Gtk.Label(label=category, xalign=0.0)
            header.add_css_class("hint-label")
            header.set_margin_top(4)
            row.set_header(header)
        else:
            row.set_header(None)

    def _selected_suggestion(self) -> Suggestion | None:
        if not self._suggest_list.get_visible():
            return None
        row = self._suggest_list.get_selected_row()
        if row is None or row.get_index() >= len(self._suggestions):
            return None
        return self._suggestions[row.get_index()]

    def _move_selection(self, step: int) -> None:
        row = self._suggest_list.get_selected_row()
        index = (row.get_index() if row is not None else -1) + step
        index = max(0, min(index, len(self._suggestions) - 1))
        target = self._suggest_list.get_row_at_index(index)
        if target is not None:
            self._suggest_list.select_row(target)

    def _update_preview(self) -> None:
        selected = self._selected_suggestion()
        self._preview_label.set_visible(selected is not None)
        if selected is not None:
            self._preview_label.set_text(
                _("Enter: {action}").format(action=describe_suggestion(selected))
            )

    def _on_suggestion_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._suggest_list.select_row(row)
        self._on_activate(self._entry)

    def _run_suggestion(self, suggestion: Suggestion) -> None:
        action = suggestion.action
        kind = action.get("type")
        self._hide_suggestions()
        if kind == "copy":
            text = str(action.get("text", ""))
            self.get_clipboard().set(text)
            message = _("Copied {text}").format(text=text)
            self._set_response(message)
            announce(self, message)
        elif kind == "files":
            self._start_semantic_search(str(action.get("query", "")))
        elif kind == "open_app":
            info = Gio.DesktopAppInfo.new(str(action.get("app_id", "")))
            if info is not None and info.launch([], None):
                self.close()
            else:
                self._show_error(_("Couldn't open {name}.").format(name=suggestion.title))
        elif kind == "settings":
            panel = str(action.get("panel", ""))
            # Only the fixed panel names, never text from the query
            if panel in {p for _t, p, _w in SETTINGS_PANELS} and shutil.which(
                "gnome-control-center"
            ):
                subprocess.Popen(  # nosec B603 B607 - fixed program and panel name
                    ["gnome-control-center", panel],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.close()
            else:
                self._show_error(_("GNOME Settings isn't available."))
        elif kind == "space":
            self._switch_space(int(action.get("index", 0)), str(action.get("name", "")))
        elif kind == "game_mode":
            self._run_game_mode(str(action.get("op", "")))
        elif kind == "command":
            self._run_command(str(action.get("command", "")))

    # Fixed argument lists: query text never reaches the command line. "Off" is a
    # reset, so it works however game mode was started.
    _GAME_MODE_ARGV: ClassVar[dict[str, list[str]]] = {
        "on": ["axon-game-mode", "on", "--app-id", "manual"],
        "off": ["axon-game-mode", "reset"],
        "status": ["axon-game-mode", "status"],
    }

    def _run_game_mode(self, op: str) -> None:
        argv = self._GAME_MODE_ARGV.get(op)
        if argv is None:
            return

        def worker() -> None:
            try:
                result = subprocess.run(  # nosec B603 - fixed argv
                    argv, capture_output=True, text=True, timeout=10, check=False
                )
            except (OSError, subprocess.SubprocessError):
                GLib.idle_add(self._show_error, _("Couldn't change game mode."))
                return
            output = (result.stdout or result.stderr).strip()
            if result.returncode != 0:
                GLib.idle_add(self._show_error, output or _("Couldn't change game mode."))
            elif op == "status":
                GLib.idle_add(self._set_response, output)
            else:
                GLib.idle_add(
                    self._set_response,
                    _("Game mode is on.") if op == "on" else _("Game mode is off."),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _switch_space(self, index: int, name: str) -> None:
        """Ask the Axon Shell extension (org.axonos.Shell) to switch Space."""

        def worker() -> None:
            try:
                if dbus is None:
                    raise RuntimeError("python3-dbus is not available")
                obj = dbus.SessionBus().get_object("org.axonos.Shell", "/org/axonos/Shell")
                obj.SwitchToSpace(index, dbus_interface="org.axonos.Shell", timeout=5)
            except Exception:
                GLib.idle_add(
                    self._show_error,
                    _("Couldn't switch to {name}: Axon Shell isn't running.").format(name=name),
                )
                return
            GLib.idle_add(self.close)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Semantic file search (org.axonos.Search)
    # ------------------------------------------------------------------

    def _start_semantic_search(self, query: str) -> None:
        request_id = self._requests.start()
        self._set_busy(True)
        self._set_stage(STAGE_SEARCHING)
        self._clear_results()
        self._set_response(_("Searching your files for “{query}”").format(query=query))
        threading.Thread(
            target=self._do_semantic_search, args=(query, request_id), daemon=True
        ).start()

    def _do_semantic_search(self, query: str, request_id: int) -> None:
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
        GLib.idle_add(self._show_search_results, query, results, error, request_id)

    def _show_search_results(
        self, query: str, results: list[dict[str, Any]], error: str, request_id: int
    ) -> bool:
        if not self._requests.finish(request_id):
            return False
        self._set_busy(False)
        self._clear_results()
        if error:
            self._show_error(_("Semantic search unavailable: {error}").format(error=error))
            return False
        if not results:
            self._set_response(_("No matching files for “{query}”.").format(query=query))
            return False

        backend = results[0].get("backend", "vector")
        label = "semantic" if backend == "vector" else "keyword"
        self._set_response(
            _("Top {count} {kind} matches. Click one to open it:").format(
                count=len(results), kind=label
            )
        )

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
        self._queue_fit()

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

    def _set_response(self, text: str, error: bool = False) -> None:
        """Show *text* below the entry; errors get an icon and the error colour."""
        self._response_label.set_text(text)
        self._response_label.set_visible(bool(text))
        self._error_icon.set_visible(error)
        if error:
            self._response_label.add_css_class("response-error")
        else:
            self._response_label.remove_css_class("response-error")
            self._start_ollama_btn.set_visible(False)
        self._queue_fit()

    def _show_error(self, text: str) -> None:
        self._set_response(text, error=True)

    def _on_start_ollama(self, _btn: Gtk.Button) -> None:
        """Attempt to launch the Ollama daemon."""
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._start_ollama_btn.set_sensitive(False)
        self._set_response(_("Starting Ollama… try again in a moment."))

    # ------------------------------------------------------------------
    # Semantic File Search Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        """Cancel streaming and clean up resources on window close."""
        self._stream_cancelled = True
