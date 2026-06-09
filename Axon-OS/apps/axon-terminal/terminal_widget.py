"""
terminal_widget.py — Core terminal widget for Axon Terminal.

Provides:
  • VTE-based terminal emulator with tab support
  • Command history and exit-code tracking
  • Failure detection with inline AI diagnosis overlay
  • Natural language command bar (Ctrl+Shift+A)
  • AI-powered suggestion chips after errors
"""

from __future__ import annotations

import os
import shlex
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "2.91")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango, Vte  # noqa: E402

from ai_helper import AIHelper  # noqa: E402


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

_BG = Gdk.RGBA()
_BG.parse("#0f0f14")

_FG = Gdk.RGBA()
_FG.parse("#e4e4e8")

_ACCENT = Gdk.RGBA()
_ACCENT.parse("#c4b5fd")

# Terminal 16-colour palette (dark theme matching Axon branding)
_PALETTE: list[Gdk.RGBA] = []
_PALETTE_HEX = [
    "#1a1a24", "#f87171", "#86efac", "#fde68a",
    "#93c5fd", "#c4b5fd", "#67e8f9", "#e4e4e8",
    "#3a3a4e", "#fca5a5", "#a7f3d0", "#fef08a",
    "#bfdbfe", "#ddd6fe", "#a5f3fc", "#ffffff",
]
for _hex in _PALETTE_HEX:
    _c = Gdk.RGBA()
    _c.parse(_hex)
    _PALETTE.append(_c)


# ---------------------------------------------------------------------------
# Single terminal tab
# ---------------------------------------------------------------------------

class _TerminalTab:
    """Data associated with a single terminal tab."""

    __slots__ = ("terminal", "label", "pid", "last_command", "last_exit_code",
                 "stderr_capture", "page")

    def __init__(self, terminal: Vte.Terminal, label: str, pid: int) -> None:
        self.terminal = terminal
        self.label = label
        self.pid = pid
        self.last_command: str = ""
        self.last_exit_code: int = 0
        self.stderr_capture: str = ""
        self.page: Optional[Adw.TabPage] = None


# ---------------------------------------------------------------------------
# TerminalWidget
# ---------------------------------------------------------------------------

class TerminalWidget(Gtk.Box):
    """Multi-tab VTE terminal with AI-powered diagnostics and NL command bar."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._ai = AIHelper()
        self._tabs: list[_TerminalTab] = []
        self._command_history: list[str] = []
        self._nl_bar_visible: bool = False

        # ---- Tab view (Adw.TabView + Adw.TabBar) --------------------------
        self._tab_bar = Adw.TabBar()
        self._tab_view = Adw.TabView()
        self._tab_bar.set_view(self._tab_view)
        self.append(self._tab_bar)

        # Terminal area
        self._tab_view.set_vexpand(True)
        self._tab_view.set_hexpand(True)
        self.append(self._tab_view)

        # ---- AI Diagnosis overlay (inline below terminal) -----------------
        self._diagnosis_revealer = Gtk.Revealer()
        self._diagnosis_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP
        )
        self._diagnosis_revealer.set_reveal_child(False)

        diag_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        diag_frame.add_css_class("diagnosis-box")
        diag_frame.set_spacing(6)
        diag_frame.set_margin_start(8)
        diag_frame.set_margin_end(8)
        diag_frame.set_margin_top(6)
        diag_frame.set_margin_bottom(6)

        # Header row
        diag_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        diag_header.set_spacing(6)
        diag_title = Gtk.Label(label="⬡ AI Diagnosis")
        diag_title.add_css_class("diagnosis-title")
        diag_title.set_xalign(0.0)
        diag_title.set_hexpand(True)
        diag_header.append(diag_title)

        dismiss_btn = Gtk.Button(label="✕")
        dismiss_btn.add_css_class("flat")
        dismiss_btn.add_css_class("diagnosis-dismiss")
        dismiss_btn.set_tooltip_text("Dismiss")
        dismiss_btn.connect("clicked", lambda _b: self._hide_diagnosis())
        diag_header.append(dismiss_btn)
        diag_frame.append(diag_header)

        # Diagnosis text
        self._diagnosis_label = Gtk.Label()
        self._diagnosis_label.set_wrap(True)
        self._diagnosis_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._diagnosis_label.set_xalign(0.0)
        self._diagnosis_label.set_selectable(True)
        self._diagnosis_label.add_css_class("diagnosis-text")
        diag_frame.append(self._diagnosis_label)

        # Suggestion chips row
        self._suggestion_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._suggestion_box.set_spacing(6)
        self._suggestion_box.set_margin_top(4)
        diag_frame.append(self._suggestion_box)

        # Spinner shown while waiting for AI
        self._diag_spinner = Gtk.Spinner()
        self._diag_spinner.set_visible(False)
        diag_frame.append(self._diag_spinner)

        self._diagnosis_revealer.set_child(diag_frame)
        self.append(self._diagnosis_revealer)

        # ---- NL command bar (toggled with Ctrl+Shift+A) -------------------
        self._nl_revealer = Gtk.Revealer()
        self._nl_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP
        )
        self._nl_revealer.set_reveal_child(False)

        nl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nl_box.add_css_class("nl-bar")
        nl_box.set_spacing(8)
        nl_box.set_margin_start(8)
        nl_box.set_margin_end(8)
        nl_box.set_margin_top(4)
        nl_box.set_margin_bottom(4)

        nl_icon = Gtk.Label(label="⬡")
        nl_icon.add_css_class("nl-icon")
        nl_box.append(nl_icon)

        self._nl_entry = Gtk.Entry()
        self._nl_entry.set_hexpand(True)
        self._nl_entry.set_placeholder_text("Describe what you want to do…")
        self._nl_entry.connect("activate", self._on_nl_activate)
        nl_box.append(self._nl_entry)

        self._nl_spinner = Gtk.Spinner()
        self._nl_spinner.set_visible(False)
        nl_box.append(self._nl_spinner)

        # Preview label shows the generated command for confirmation
        self._nl_preview = Gtk.Label()
        self._nl_preview.set_xalign(0.0)
        self._nl_preview.set_selectable(True)
        self._nl_preview.add_css_class("nl-preview")
        self._nl_preview.set_visible(False)

        nl_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        nl_outer.set_spacing(4)
        nl_outer.append(nl_box)
        nl_outer.append(self._nl_preview)

        # Confirm / Cancel row for previewed command
        self._nl_confirm_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._nl_confirm_box.set_spacing(6)
        self._nl_confirm_box.set_margin_start(8)
        self._nl_confirm_box.set_margin_bottom(4)
        self._nl_confirm_box.set_visible(False)

        confirm_btn = Gtk.Button(label="Run")
        confirm_btn.add_css_class("suggested-action")
        confirm_btn.connect("clicked", self._on_nl_confirm)
        self._nl_confirm_box.append(confirm_btn)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", self._on_nl_cancel)
        self._nl_confirm_box.append(cancel_btn)

        nl_outer.append(self._nl_confirm_box)
        self._nl_revealer.set_child(nl_outer)
        self.append(self._nl_revealer)

        # Pending command from NL translation
        self._pending_nl_command: str = ""

        # Listen for tab close
        self._tab_view.connect("close-page", self._on_close_page)

        # Open initial tab
        self.new_tab()

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def new_tab(self, title: str = "Terminal") -> None:
        """Spawn a new terminal tab running the user's default shell."""
        term = Vte.Terminal()
        term.set_scrollback_lines(10000)
        term.set_scroll_on_output(True)
        term.set_scroll_on_keystroke(True)
        term.set_audible_bell(False)
        term.set_font(Pango.FontDescription("Monospace 11"))

        # Colours
        term.set_color_background(_BG)
        term.set_color_foreground(_FG)
        term.set_color_cursor(_ACCENT)
        term.set_color_cursor_foreground(_BG)
        term.set_colors(_FG, _BG, _PALETTE)

        # Spawn user shell
        shell = os.environ.get("SHELL", "/bin/bash")
        term.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            [shell],
            None,  # envv
            GLib.SpawnFlags.DEFAULT,
            None,  # child_setup
            None,  # child_setup_data
            -1,    # timeout
            None,  # cancellable
            self._on_spawn_ready,
        )

        # Track the tab
        tab = _TerminalTab(terminal=term, label=title, pid=-1)

        # Add to tab view
        page = self._tab_view.append(term)
        page.set_title(title)
        tab.page = page
        self._tabs.append(tab)

        # Connect signals
        term.connect("child-exited", self._on_child_exited, tab)
        term.connect("contents-changed", self._on_contents_changed, tab)

        # Focus the new tab
        self._tab_view.set_selected_page(page)

    def _on_spawn_ready(
        self,
        terminal: Vte.Terminal,
        pid: GLib.Pid,
        error: Optional[GLib.Error],
        user_data: object = None,
    ) -> None:
        """Callback after async shell spawn completes."""
        if error is not None:
            print(f"[axon-terminal] Spawn error: {error.message}")
            return
        # Find and update the tab
        for tab in self._tabs:
            if tab.terminal is terminal:
                tab.pid = pid
                break

    def _on_close_page(
        self, tab_view: Adw.TabView, page: Adw.TabPage
    ) -> bool:
        """Handle tab close request."""
        # Remove from our tracking list
        self._tabs = [t for t in self._tabs if t.page is not page]
        tab_view.close_page_finish(page, True)
        # If no tabs left, open a new one
        if not self._tabs:
            self.new_tab()
        return True

    def _get_active_tab(self) -> Optional[_TerminalTab]:
        """Return the currently selected terminal tab."""
        page = self._tab_view.get_selected_page()
        if page is None:
            return None
        for tab in self._tabs:
            if tab.page is page:
                return tab
        return None

    # ------------------------------------------------------------------
    # Command tracking
    # ------------------------------------------------------------------

    def _on_contents_changed(
        self, terminal: Vte.Terminal, tab: _TerminalTab
    ) -> None:
        """Track terminal content changes for command history."""
        # VTE doesn't give us structured command data; we rely on
        # child-exited for failure detection. This signal is available
        # for future enhancements (e.g. shell integration via OSC).
        pass

    def _on_child_exited(
        self, terminal: Vte.Terminal, exit_status: int, tab: _TerminalTab
    ) -> None:
        """Called when the shell or a command exits.

        For interactive shells the exit_status is from the shell itself.
        We use VTE's text extraction to capture the most recent output
        and trigger AI diagnosis for failures.
        """
        # In interactive shells, non-zero exit from the shell typically means
        # the user typed 'exit N' or the shell crashed. Individual command
        # failures are harder to detect without shell integration.
        # For now we record the exit status.
        tab.last_exit_code = exit_status

        if exit_status != 0:
            # Try to extract recent terminal output for diagnosis
            try:
                text_tuple = terminal.get_text()
                # get_text() may return (text, attributes) or just text
                if isinstance(text_tuple, tuple):
                    recent_text = str(text_tuple[0]) if text_tuple[0] else ""
                else:
                    recent_text = str(text_tuple) if text_tuple else ""
                # Take the last ~40 lines for context
                lines = recent_text.strip().split("\n")
                stderr_snippet = "\n".join(lines[-40:])
                tab.stderr_capture = stderr_snippet
            except Exception:
                tab.stderr_capture = ""

            self._show_diagnosis_for(tab)

    # ------------------------------------------------------------------
    # AI diagnosis overlay
    # ------------------------------------------------------------------

    def _show_diagnosis_for(self, tab: _TerminalTab) -> None:
        """Trigger AI diagnosis for the given tab's last failure."""
        if not self._ai.is_available:
            return

        # Show overlay with spinner
        self._diagnosis_label.set_text("Analyzing error…")
        self._diag_spinner.set_visible(True)
        self._diag_spinner.start()
        self._clear_suggestions()
        self._diagnosis_revealer.set_reveal_child(True)

        command = tab.last_command or "(shell session)"
        self._ai.diagnose_failure(
            command=command,
            exit_code=tab.last_exit_code,
            stderr_output=tab.stderr_capture,
            callback=self._on_diagnosis_received,
        )

        # Also request suggestions
        self._ai.get_suggestions(
            command=command,
            stderr_output=tab.stderr_capture,
            callback=self._on_suggestions_received,
        )

    def _on_diagnosis_received(self, text: str) -> None:
        """Callback with the AI diagnosis text."""
        self._diag_spinner.set_visible(False)
        self._diag_spinner.stop()
        self._diagnosis_label.set_text(text)

    def _on_suggestions_received(self, suggestions: list[str]) -> None:
        """Populate suggestion chips."""
        self._clear_suggestions()
        for cmd in suggestions[:3]:
            chip = Gtk.Button(label=cmd)
            chip.add_css_class("suggestion-chip")
            chip.add_css_class("flat")
            chip.set_tooltip_text(f"Run: {cmd}")
            chip.connect("clicked", self._on_suggestion_clicked, cmd)
            self._suggestion_box.append(chip)

    def _on_suggestion_clicked(
        self, button: Gtk.Button, command: str
    ) -> None:
        """Execute a suggested command in the active terminal."""
        tab = self._get_active_tab()
        if tab is not None:
            tab.terminal.feed_child((command + "\n").encode())
            tab.last_command = command
        self._hide_diagnosis()

    def _clear_suggestions(self) -> None:
        """Remove all suggestion chip buttons."""
        child = self._suggestion_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._suggestion_box.remove(child)
            child = next_child

    def _hide_diagnosis(self) -> None:
        """Dismiss the diagnosis overlay."""
        self._diagnosis_revealer.set_reveal_child(False)
        self._diag_spinner.set_visible(False)
        self._diag_spinner.stop()

    # ------------------------------------------------------------------
    # Natural language command bar
    # ------------------------------------------------------------------

    def toggle_nl_bar(self) -> None:
        """Show or hide the natural language command bar."""
        self._nl_bar_visible = not self._nl_bar_visible
        self._nl_revealer.set_reveal_child(self._nl_bar_visible)
        if self._nl_bar_visible:
            self._nl_entry.grab_focus()
        else:
            self._reset_nl_bar()

    def _reset_nl_bar(self) -> None:
        """Clear and hide NL bar state."""
        self._nl_entry.set_text("")
        self._nl_preview.set_visible(False)
        self._nl_preview.set_text("")
        self._nl_confirm_box.set_visible(False)
        self._nl_spinner.set_visible(False)
        self._nl_spinner.stop()
        self._pending_nl_command = ""

    def _on_nl_activate(self, entry: Gtk.Entry) -> None:
        """User pressed Enter in the NL bar — translate to command."""
        text = entry.get_text().strip()
        if not text:
            return

        if not self._ai.is_available:
            self._nl_preview.set_text("⚠ AI unavailable")
            self._nl_preview.set_visible(True)
            return

        self._nl_spinner.set_visible(True)
        self._nl_spinner.start()
        self._nl_preview.set_visible(False)
        self._nl_confirm_box.set_visible(False)

        self._ai.translate_to_command(text, self._on_nl_translated)

    def _on_nl_translated(self, command: str) -> None:
        """Callback with the translated shell command."""
        self._nl_spinner.set_visible(False)
        self._nl_spinner.stop()

        if not command:
            self._nl_preview.set_text("⚠ Could not translate to a command.")
            self._nl_preview.set_visible(True)
            return

        self._pending_nl_command = command
        self._nl_preview.set_text(f"$ {command}")
        self._nl_preview.set_visible(True)
        self._nl_confirm_box.set_visible(True)

    def _on_nl_confirm(self, button: Gtk.Button) -> None:
        """User confirmed the generated command — execute it."""
        if self._pending_nl_command:
            tab = self._get_active_tab()
            if tab is not None:
                cmd = self._pending_nl_command
                tab.terminal.feed_child((cmd + "\n").encode())
                tab.last_command = cmd
                self._command_history.append(cmd)
        self._reset_nl_bar()
        self._nl_bar_visible = False
        self._nl_revealer.set_reveal_child(False)

    def _on_nl_cancel(self, button: Gtk.Button) -> None:
        """User cancelled the generated command."""
        self._reset_nl_bar()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def feed_command(self, command: str) -> None:
        """Send a command string to the active terminal."""
        tab = self._get_active_tab()
        if tab is not None:
            tab.terminal.feed_child((command + "\n").encode())
            tab.last_command = command
            self._command_history.append(command)

    def get_active_terminal(self) -> Optional[Vte.Terminal]:
        """Return the active VTE terminal widget, or None."""
        tab = self._get_active_tab()
        return tab.terminal if tab is not None else None
