"""Pure UI logic shared by the Axon GTK apps (Intent Bar, AI panel).

Nothing in this module imports GTK or D-Bus, so every function can be unit
tested without a display. The GNOME Shell extension keeps a JavaScript copy of
the parts it needs in ``shell/axon-shell/ui-logic.js``; keep the two in sync.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

HOLD = "hold"
SHOW = "show"

_FENCE = "```"
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)
# Error markers the clients put into a reply: "[error] msg" or "[Error: msg]",
# at the start of the text or of a line.
_ERROR_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:\[error\][ \t]*(?P<tag>.*)|\[error:[ \t]*(?P<wrapped>.*?)\])",
    re.IGNORECASE | re.DOTALL,
)


def classify_stream_prefix(text: str) -> str:
    """Decide whether a partial model reply can be shown yet.

    A reply that may turn out to be an action (``{"action": ...}``, optionally
    inside a ```` ```json ```` fence) is held back until the stream ends so the
    user never sees raw JSON. Anything else is shown as it arrives.

    Args:
        text: Everything received so far.

    Returns:
        ``"hold"`` while the reply could still be an action, else ``"show"``.
    """
    s = text.lstrip()
    if not s or s.startswith("{"):
        return HOLD
    if _FENCE.startswith(s):
        return HOLD  # a fence that is still arriving: "`" or "``"
    if not s.startswith(_FENCE):
        return SHOW
    rest = s[len(_FENCE) :]
    newline = rest.find("\n")
    if newline < 0:
        # Language tag still arriving: hold while it could become "json".
        return HOLD if "json".startswith(rest.strip().lower()) else SHOW
    lang = rest[:newline].strip().lower()
    if lang not in ("", "json"):
        return SHOW
    body = rest[newline + 1 :].lstrip()
    if not body or body.startswith("{"):
        return HOLD
    return SHOW


def strip_code_fence(text: str) -> str:
    """Return the inside of a single fenced code block, or *text* unchanged."""
    s = text.strip()
    match = _FENCE_RE.match(s)
    return match.group(1).strip() if match else s


def parse_action(text: str) -> dict[str, Any] | None:
    """Parse a model reply as an action object.

    Args:
        text: The complete reply.

    Returns:
        The action dict when the reply is a JSON object with a string
        ``action`` key, otherwise None.
    """
    body = strip_code_fence(text)
    if not body.startswith("{"):
        return None
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(obj, dict) and isinstance(obj.get("action"), str):
        return obj
    return None


def split_error(text: str) -> tuple[str, str | None]:
    """Split a reply into the normal text and an error message, if any.

    Args:
        text: A complete reply, possibly ending in an ``[error] ...`` marker.

    Returns:
        ``(body, error)`` where *error* is None when there is no marker.
    """
    match = _ERROR_RE.search(text)
    if match is None:
        return text.strip(), None
    message = (match.group("tag") or match.group("wrapped") or "").strip()
    return text[: match.start()].strip(), message or "Something went wrong."


def describe_action(action: dict[str, Any]) -> str:
    """Return a short, friendly line for an action instead of its JSON."""
    kind = action.get("action", "")
    if kind == "open_app":
        app = str(action.get("app", "")).strip()
        return f"Opening {app}…" if app else "Opening app…"
    if kind == "run_command":
        command = str(action.get("command", "")).strip()
        return f"Run: {command}" if command else "Run command"
    return "Axon suggested an action it can't perform here."


class RequestTracker:
    """Track the one AI request a window may have running.

    Each request gets an id. Cancelling (or starting a newer request) makes
    older ids stale, so a worker thread can drop late results with
    ``is_current``. Thread safe enough for one writer (the GTK thread) and
    readers in workers: ids only ever grow.
    """

    def __init__(self) -> None:
        self._id = 0
        self._busy = False

    @property
    def busy(self) -> bool:
        """True while the latest request is running."""
        return self._busy

    def start(self) -> int:
        """Begin a request and return its id."""
        self._id += 1
        self._busy = True
        return self._id

    def finish(self, request_id: int) -> bool:
        """Mark *request_id* done. Returns False if it was already stale."""
        if request_id != self._id:
            return False
        self._busy = False
        return True

    def cancel(self) -> bool:
        """Cancel the running request. Returns False if nothing was running."""
        if not self._busy:
            return False
        self._id += 1
        self._busy = False
        return True

    def is_current(self, request_id: int) -> bool:
        """True while *request_id* is the latest, uncancelled request."""
        return request_id == self._id and self._busy


def escape_action(busy: bool) -> str:
    """What Escape does: cancel a running request first, otherwise close."""
    return "cancel" if busy else "close"


def fit_to_screen(preferred: int, available: int, margin: int = 48, minimum: int = 320) -> int:
    """Clamp a window dimension so it fits the screen.

    Args:
        preferred: The size the design asks for, in px.
        available: Monitor size (or work area) in the same direction, in px.
            0 or less means unknown, and *preferred* is returned.
        margin: Space to leave for panels/docks, in px.
        minimum: Never go below this, even on tiny screens.

    Returns:
        ``max(minimum, min(preferred, available - margin))``.
    """
    if available <= 0:
        return preferred
    return max(minimum, min(preferred, available - margin))


# Loading stages shown while a request runs. Only show a stage the code has
# actually reached. Keep in sync with STAGES in shell/axon-shell/ui-logic.js.
STAGE_CONNECTING = "Connecting to Axon Brain…"
STAGE_WAITING = "Waiting for model…"
STAGE_PLANNING = "Planning action…"
STAGE_SEARCHING = "Searching local files…"
STAGE_CONFIRM = "Ready for confirmation"


def stage_for_stream(text: str) -> str | None:
    """Stage to show once tokens arrive: planning while JSON is held back.

    Returns None when the reply is plain text and is being shown already.
    """
    return STAGE_PLANNING if classify_stream_prefix(text) == HOLD else None


# Example prompts for the Intent Bar's empty state. Each one works with the
# current actions: semantic file search, open_app, an allowlisted command,
# or a plain answer.
EXAMPLE_PROMPTS: tuple[str, ...] = (
    "find the PDF I edited yesterday",
    "Open Firefox",
    "How much disk space is free?",
    "Explain what a symlink is",
)


def push_history(history: list[str], query: str, limit: int = 20) -> list[str]:
    """Return *history* (newest first) with *query* moved to the front."""
    query = query.strip()
    if not query:
        return list(history)
    return ([query] + [h for h in history if h != query])[:limit]


def recent_for_empty_state(history: list[str], limit: int = 3) -> list[str]:
    """Most recent distinct queries to offer before the user types."""
    seen: list[str] = []
    for item in history:
        if item and item not in seen and item not in EXAMPLE_PROMPTS:
            seen.append(item)
        if len(seen) >= limit:
            break
    return seen


def load_history(path: Path, limit: int = 20) -> list[str]:
    """Read saved queries (newest first); missing or corrupt files give []."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data if isinstance(x, str) and x.strip()][:limit]


def save_history(path: Path, history: list[str]) -> None:
    """Write queries readable by the user only (they can be personal)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(history, fh)
    except OSError:
        pass  # history is a convenience; never break the bar over it
