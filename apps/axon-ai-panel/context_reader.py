"""
context_reader.py — ContextReader for Axon AI Panel.

Retrieves consolidated session context on-demand from the org.axonos.Context 
D-Bus service instead of scanning /proc and system log files directly.
"""

from __future__ import annotations

import json
import dbus
from typing import Optional, Any


class ContextReader:
    """Retrieves session context from the central Context Engine D-Bus service."""

    def __init__(self) -> None:
        self.bus = dbus.SessionBus()
        self.context_obj: Any = None
        self._connect()

    def _connect(self) -> None:
        if self.context_obj is None:
            try:
                self.context_obj = self.bus.get_object('org.axonos.Context', '/org/axonos/Context')
            except Exception:
                self.context_obj = None

    def _get_context(self) -> Any:
        self._connect()
        return self.context_obj

    def get_active_window_title(self) -> Optional[str]:
        """Return the title of the currently focused window, or None."""
        ctx = self._get_context()
        if ctx is not None:
            try:
                context_data = json.loads(ctx.GetActiveContext())
                title = context_data.get("active_window", {}).get("title")
                return str(title) if title and title != "None" else None
            except Exception:
                pass
        return None

    def get_open_files_in_editors(self) -> list[str]:
        """Scan known editor processes open file descriptors."""
        ctx = self._get_context()
        if ctx is not None:
            try:
                context_data = json.loads(ctx.GetActiveContext())
                return list(context_data.get("open_files", []))
            except Exception:
                pass
        return []

    def get_recent_terminal_commands(self, n: int = 10) -> list[str]:
        """Return the last shell commands from history."""
        ctx = self._get_context()
        if ctx is not None:
            try:
                context_data = json.loads(ctx.GetActiveContext())
                commands = context_data.get("terminal_commands", [])
                return list(commands[-n:])
            except Exception:
                pass
        return []

    def get_last_terminal_stderr(self) -> Optional[str]:
        """Return the contents of last terminal error output."""
        ctx = self._get_context()
        if ctx is not None:
            try:
                context_data = json.loads(ctx.GetActiveContext())
                err = context_data.get("last_stderr")
                return str(err) if err else None
            except Exception:
                pass
        return None

    def get_space_context(self) -> dict[str, Optional[str]]:
        """Read and return name and color properties of the current space."""
        ctx = self._get_context()
        result: dict[str, Optional[str]] = {"space_name": None, "space_color": None}
        if ctx is not None:
            try:
                context_data = json.loads(ctx.GetActiveContext())
                result["space_name"] = context_data.get("active_space", "Default")
            except Exception:
                pass
        return result

    def build_context_string(self) -> str:
        """Fetch the fully formatted prompt-ready context string from D-Bus."""
        ctx = self._get_context()
        if ctx is not None:
            try:
                return str(ctx.GetContextString())
            except Exception as e:
                return f"Error retrieving context: {e}"
        return "No desktop context available."
