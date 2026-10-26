"""
context_reader.py — ContextReader for Axon AI Panel.

Retrieves consolidated session context on-demand from the org.axonos.Context
D-Bus service instead of scanning /proc and system log files directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import dbus

# services/ is beside apps/ (source tree, .deb) or inside the install dir.
for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        if str(_base / "services") not in sys.path:
            sys.path.insert(0, str(_base / "services"))
        break
from context_settings import ContextSettings, available_items, format_context, load_settings


class ContextReader:
    """Retrieves session context from the central Context Engine D-Bus service."""

    def __init__(self) -> None:
        try:
            self.bus = dbus.SessionBus()
        except Exception:
            self.bus = None
        self.context_obj: Any = None
        self._connect()

    def close(self) -> None:
        """Release the D-Bus connection."""
        self.context_obj = None

    def _connect(self) -> None:
        if self.context_obj is None and self.bus is not None:
            try:
                self.context_obj = self.bus.get_object("org.axonos.Context", "/org/axonos/Context")
            except Exception:
                self.context_obj = None

    def _get_context(self) -> Any:
        self._connect()
        return self.context_obj

    def get_active_window_title(self) -> str | None:
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

    def get_last_terminal_stderr(self) -> str | None:
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

    def get_space_context(self) -> dict[str, str | None]:
        """Read and return name and color properties of the current space."""
        ctx = self._get_context()
        result: dict[str, str | None] = {"space_name": None, "space_color": None}
        if ctx is not None:
            try:
                context_data = json.loads(ctx.GetActiveContext())
                result["space_name"] = context_data.get("active_space", "Default")
            except Exception:
                pass
        return result

    def snapshot(self) -> dict[str, Any]:
        """Raw GetActiveContext() data, or {} when the service is unavailable."""
        ctx = self._get_context()
        if ctx is None:
            return {}
        try:
            data = json.loads(ctx.GetActiveContext())
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def settings(self) -> ContextSettings:
        """The user's share settings (re-read each time; the file may change)."""
        return load_settings()

    def context_items(self, snapshot: dict[str, Any] | None = None) -> list[tuple[str, str]]:
        """What would be sent, as ``(key, description)`` pairs for the UI."""
        data = self.snapshot() if snapshot is None else snapshot
        return available_items(data, self.settings())

    def build_context_string(
        self,
        exclude: frozenset[str] | set[str] = frozenset(),
        snapshot: dict[str, Any] | None = None,
    ) -> str:
        """Prompt-ready context honouring the share settings.

        Args:
            exclude: Item keys the user removed for this one message.
            snapshot: Data from :meth:`snapshot`, to avoid a second D-Bus call.

        Returns:
            The context text, or "" when nothing may be sent.
        """
        settings = self.settings()
        if not settings.share_context:
            return ""
        data = self.snapshot() if snapshot is None else snapshot
        return format_context(data, settings, exclude)
