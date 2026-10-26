"""What desktop context the AI may receive, and how it is formatted.

Settings live in ``~/.config/axon-os/context.json`` next to the existing
``track_*`` keys (what the Context service collects). The ``share_*`` keys
here decide what is *sent* with a prompt:

- ``share_context``: master switch; off sends nothing.
- ``share_active_app``: focused window title/app and current Space.
- ``share_open_files``: files open in editors.
- ``share_terminal_history``: recent shell commands and the last error.
- ``share_clipboard``: recent clipboard entries.

Defaults keep today's behaviour (everything on). Nothing here imports D-Bus
or GTK, so it is shared by the Context service, the AI panel and Settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".config" / "axon-os" / "context.json"

# Context item keys, in the order they appear in prompts and in the UI.
ACTIVE_APP = "active_app"
OPEN_FILES = "open_files"
TERMINAL = "terminal_history"
CLIPBOARD = "clipboard"
ITEM_KEYS = (ACTIVE_APP, OPEN_FILES, TERMINAL, CLIPBOARD)

# Items that can carry file contents, commands or copied text. A cloud
# model gets a warning before these are first sent in a conversation.
SENSITIVE_ITEMS = frozenset({OPEN_FILES, TERMINAL, CLIPBOARD})

ITEM_LABELS = {
    ACTIVE_APP: "Active app",
    OPEN_FILES: "Open files",
    TERMINAL: "Terminal history",
    CLIPBOARD: "Clipboard",
}

_CLIP_PREVIEW = 120


@dataclass
class ContextSettings:
    """Which kinds of context may be sent with a prompt."""

    share_context: bool = True
    share_active_app: bool = True
    share_open_files: bool = True
    share_terminal_history: bool = True
    share_clipboard: bool = True

    def allows(self, item: str) -> bool:
        """True when *item* (one of ITEM_KEYS) may be sent."""
        return self.share_context and bool(getattr(self, f"share_{item}", False))


def load_settings(path: Path | None = None) -> ContextSettings:
    """Read the share settings; missing or bad values fall back to defaults."""
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ContextSettings()
    if not isinstance(data, dict):
        return ContextSettings()
    defaults = asdict(ContextSettings())
    values = {k: data[k] if isinstance(data.get(k), bool) else v for k, v in defaults.items()}
    return ContextSettings(**values)


def save_settings(settings: ContextSettings, path: Path | None = None) -> None:
    """Write the share settings, keeping other keys (e.g. ``track_*``)."""
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.update(asdict(settings))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # also tighten a file that already existed
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _items(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Pull each context item out of a GetActiveContext() snapshot."""
    window = snapshot.get("active_window") or {}
    title = str(window.get("title") or "")
    return {
        ACTIVE_APP: (
            title if title and title != "None" else "",
            str(window.get("app") or ""),
            str(snapshot.get("active_space") or ""),
        ),
        OPEN_FILES: [str(f) for f in snapshot.get("open_files") or []],
        TERMINAL: (
            [str(c) for c in snapshot.get("terminal_commands") or []],
            str(snapshot.get("last_stderr") or ""),
        ),
        CLIPBOARD: [str(c) for c in snapshot.get("clipboard_history") or []],
    }


def available_items(snapshot: dict[str, Any], settings: ContextSettings) -> list[tuple[str, str]]:
    """Items that would be sent, as ``(key, short description)`` for the UI.

    E.g. ``[("active_app", "Firefox"), ("open_files", "main.py"),
    ("terminal_history", "terminal history")]``.
    """
    items = _items(snapshot)
    out: list[tuple[str, str]] = []
    title, app, _space = items[ACTIVE_APP]
    app_name = app if app != "None" else ""
    if settings.allows(ACTIVE_APP) and (app_name or title):
        out.append((ACTIVE_APP, app_name or title))
    files = items[OPEN_FILES]
    if settings.allows(OPEN_FILES) and files:
        names = [Path(f).name or f for f in files]
        more = f" +{len(names) - 2}" if len(names) > 2 else ""
        out.append((OPEN_FILES, ", ".join(names[:2]) + more))
    commands, stderr = items[TERMINAL]
    if settings.allows(TERMINAL) and (commands or stderr):
        out.append((TERMINAL, "terminal history"))
    if settings.allows(CLIPBOARD) and items[CLIPBOARD]:
        out.append((CLIPBOARD, "clipboard"))
    return out


def format_context(
    snapshot: dict[str, Any],
    settings: ContextSettings,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> str:
    """Prompt-ready context text, honouring the settings and *exclude*.

    Returns "" when nothing may be sent, so callers send no context at all.
    """
    if not settings.share_context:
        return ""
    items = _items(snapshot)

    def allowed(key: str) -> bool:
        return settings.allows(key) and key not in exclude

    parts: list[str] = []
    if allowed(ACTIVE_APP):
        title, app, space = items[ACTIVE_APP]
        if title:
            parts.append(f"Active window: {title} (App: {app})")
        if space:
            parts.append(f"Current space: {space}")
    if allowed(OPEN_FILES) and items[OPEN_FILES]:
        parts.append("Open files in editors:")
        parts.extend(f"  - {f}" for f in items[OPEN_FILES])
    if allowed(TERMINAL):
        commands, stderr = items[TERMINAL]
        if commands:
            parts.append("Recent terminal commands:")
            parts.extend(f"  $ {c}" for c in commands)
        if stderr:
            parts.append(f"Last terminal error:\n{stderr}")
    if allowed(CLIPBOARD) and items[CLIPBOARD]:
        parts.append("Recent clipboard entries:")
        for i, entry in enumerate(reversed(items[CLIPBOARD]), 1):
            shown = entry[:_CLIP_PREVIEW] + "..." if len(entry) > _CLIP_PREVIEW else entry
            parts.append(f"  [{i}] {shown}")
    return "\n".join(parts)


def sends_sensitive(items: list[tuple[str, str]], exclude: set[str] | frozenset[str]) -> bool:
    """True when the items to send include clipboard, terminal or file data."""
    return any(key in SENSITIVE_ITEMS and key not in exclude for key, _ in items)


def sent_files(
    snapshot: dict[str, Any] | None,
    settings: ContextSettings,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    """Paths of open files that went out with a message, to list as sources."""
    if not snapshot or not settings.allows(OPEN_FILES) or OPEN_FILES in exclude:
        return []
    return [str(f) for f in snapshot.get("open_files") or []]
