"""Pure UI logic shared by the Axon GTK apps (Intent Bar, AI panel).

Nothing in this module imports GTK or D-Bus, so every function can be unit
tested without a display. The GNOME Shell extension keeps a JavaScript copy of
the parts it needs in ``shell/axon-shell/ui-logic.js``; keep the two in sync.
"""

from __future__ import annotations

import json
import re
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
