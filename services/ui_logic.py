"""Pure UI logic shared by the Axon GTK apps (Intent Bar, AI panel).

Nothing in this module imports GTK or D-Bus, so every function can be unit
tested without a display. The GNOME Shell extension keeps a JavaScript copy of
the parts it needs in ``shell/axon-shell/ui-logic.js``; keep the two in sync.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from i18n import _

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


# What Brain answers while game mode has the AI paused (services/axon-brain/game_gate.py).
# Clients recognise it to offer "Run anyway" instead of a plain error.
GAME_MODE_MESSAGE = "Game mode is on. AI is paused to keep your game smooth."


def is_game_mode_error(message: str) -> bool:
    """True if *message* is Brain saying the AI is paused for a game."""
    return GAME_MODE_MESSAGE in message


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
    return text[: match.start()].strip(), message or _("Something went wrong.")


def describe_action(action: dict[str, Any]) -> str:
    """Return a short, friendly line for an action instead of its JSON."""
    kind = action.get("action", "")
    if kind == "open_app":
        app = str(action.get("app", "")).strip()
        return _("Opening {app}…").format(app=app) if app else _("Opening app…")
    if kind == "run_command":
        command = str(action.get("command", "")).strip()
        return _("Run: {command}").format(command=command) if command else _("Run command")
    return _("Axon suggested an action it can't perform here.")


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
        ``min(preferred, available - margin)``, but never below *minimum*
        (or *preferred*, if that is smaller).
    """
    if available <= 0:
        return preferred
    return max(min(minimum, preferred), min(preferred, available - margin))


# Loading stages shown while a request runs. Only show a stage the code has
# actually reached. Keep in sync with STAGES in shell/axon-shell/ui-logic.js.
STAGE_CONNECTING = _("Connecting to Axon Brain…")
STAGE_WAITING = _("Waiting for model…")
STAGE_PLANNING = _("Planning action…")
STAGE_SEARCHING = _("Searching local files…")
STAGE_CONFIRM = _("Ready for confirmation")


def stage_for_stream(text: str) -> str | None:
    """Stage to show once tokens arrive: planning while JSON is held back.

    Returns None when the reply is plain text and is being shown already.
    """
    return STAGE_PLANNING if classify_stream_prefix(text) == HOLD else None


# Example prompts for the Intent Bar's empty state. Each one works with the
# current actions: semantic file search, open_app, an allowlisted command,
# or a plain answer.
EXAMPLE_PROMPTS: tuple[str, ...] = (
    _("find the PDF I edited yesterday"),
    _("Open Firefox"),
    _("How much disk space is free?"),
    _("Explain what a symlink is"),
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
        os.fchmod(fd, 0o600)  # also tighten a file that already existed
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(history, fh)
    except OSError:
        pass  # history is a convenience; never break the bar over it


def intent_bar_width(monitor_width: int) -> int:
    """Intent Bar width: 45% of the monitor, clamped to 480–820 px."""
    if monitor_width <= 0:
        return 660
    return max(480, min(820, round(monitor_width * 0.45)))


# ── Where the model runs ────────────────────────────────────────────────────

LOCAL = "local"  # on this computer
REMOTE = "remote"  # another machine on the local network
CLOUD = "cloud"  # an internet service
UNKNOWN = "unknown"  # the Brain couldn't tell us; assume it may leave the computer

_PROVIDER_LABELS = {
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
}
_ORIGIN_RANK = {LOCAL: 0, REMOTE: 1, CLOUD: 2}


@dataclass(frozen=True)
class ModelOrigin:
    """Where a model runs, for the local/cloud indicator."""

    kind: str
    provider: str
    model: str

    @property
    def label(self) -> str:
        """e.g. "Local · llama3.2:3b" or "Cloud · OpenAI · gpt-4.1"."""
        if self.kind == UNKNOWN:
            return _("Model: unknown")
        if self.kind == LOCAL and self.provider == "Ollama":
            return _("Local · {model}").format(model=self.model)
        prefix = {LOCAL: _("Local"), REMOTE: _("Remote"), CLOUD: _("Cloud")}[self.kind]
        return f"{prefix} · {self.provider} · {self.model}"

    @property
    def leaves_computer(self) -> bool:
        """True when prompts (and any context) are sent to another machine."""
        return self.kind != LOCAL


def host_kind(base_url: str) -> str:
    """Classify a provider URL as local, remote (LAN) or cloud."""
    host = (urlparse(base_url).hostname or "").lower()
    if host in ("localhost", "") or host.endswith(".localhost"):
        return LOCAL
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host.endswith((".local", ".lan", ".internal", ".home.arpa")) or "." not in host:
            return REMOTE
        return CLOUD
    if ip.is_loopback:
        return LOCAL
    if ip.is_private or ip.is_link_local:
        return REMOTE
    return CLOUD


def model_origin(model_id: str, providers: list[dict[str, Any]] | None = None) -> ModelOrigin:
    """Where *model_id* runs.

    Bare names are Ollama models; ``@provider/model`` refs belong to that
    provider. When the provider list is unknown, a ``@`` ref counts as cloud:
    never claim a model is local without checking.

    Args:
        model_id: A model id as passed to the Brain.
        providers: Brain ``ListProviders`` output (``id``, ``label``, ``base_url``).
    """
    by_id = {str(p.get("id")): p for p in providers or []}
    if model_id.startswith("@") and "/" in model_id:
        provider_id, model = model_id[1:].split("/", 1)
    else:
        provider_id, model = "ollama", model_id
    cfg = by_id.get(provider_id)
    label = str((cfg or {}).get("label") or _PROVIDER_LABELS.get(provider_id, provider_id))
    if cfg is not None and cfg.get("base_url"):
        kind = host_kind(str(cfg["base_url"]))
    else:
        kind = LOCAL if provider_id == "ollama" else CLOUD
    return ModelOrigin(kind, label, model or "default model")


def routing_origin(
    model_id: str,
    tiers: dict[str, str] | None = None,
    providers: list[dict[str, Any]] | None = None,
) -> ModelOrigin:
    """Origin for a request, including the Brain's automatic model choice.

    An empty *model_id* lets the Brain pick a tier (speed, general or deep)
    per request, so the least private tier decides the indicator.
    """
    if model_id:
        return model_origin(model_id, providers)
    names = [m for m in (tiers or {}).values() if m]
    if not names:
        # Unknown tiers could include a cloud model: don't say "local"
        return ModelOrigin(UNKNOWN, "", "")
    general = (tiers or {}).get("general_model") or names[0]
    origins = [model_origin(m, providers) for m in names]
    worst = max(origins, key=lambda o: _ORIGIN_RANK[o.kind])
    if worst.kind == LOCAL:
        return model_origin(general, providers)
    return worst


def origin_tooltip(origin: ModelOrigin) -> str:
    """One sentence explaining where requests go."""
    if origin.kind == LOCAL:
        return _("Runs on this computer. Nothing is sent over the internet.")
    if origin.kind == REMOTE:
        return _("Requests go to {provider} on another computer on your network.").format(
            provider=origin.provider
        )
    if origin.kind == CLOUD:
        return _("Requests are sent to {provider} over the internet.").format(
            provider=origin.provider
        )
    return _("Couldn't check where the model runs, so assume requests may leave this computer.")


def format_command_output(text: str, max_lines: int = 40, max_chars: int = 4000) -> str:
    """Trim command output for the Intent Bar, saying what was cut."""
    text = text.rstrip()
    if not text:
        return _("(no output)")
    lines = text.splitlines()
    cut = len(lines) > max_lines
    shown = "\n".join(lines[:max_lines])
    if len(shown) > max_chars:
        shown, cut = shown[:max_chars], True
    return shown + "\n" + _("… (output shortened)") if cut else shown


def visible_conversations(
    conversations: list[dict[str, Any]], show_archived: bool = False
) -> list[dict[str, Any]]:
    """Conversations for the list: newest first, archived ones only on request."""
    rows = [c for c in conversations if show_archived or not c.get("archived")]
    return sorted(rows, key=lambda c: str(c.get("updated_at") or ""), reverse=True)


def format_latency(seconds: float) -> str:
    """Response time for an answer, e.g. "850 ms" or "2.4 s"."""
    if seconds < 1:
        return f"{max(0, round(seconds * 1000))} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s"


def answer_meta(model: str, seconds: float, first_token: float | None = None) -> str:
    """Footer under an answer: model and response time."""
    parts = [model or _("Automatic model"), format_latency(seconds)]
    if first_token is not None and seconds >= 1:
        parts.append(_("first words after {time}").format(time=format_latency(first_token)))
    return " · ".join(parts)


def announcement(kind: str, text: str = "") -> str:
    """Short screen-reader message when an AI request finishes or fails.

    Args:
        kind: "answer", "error", "command", "cancelled" or "action".
        text: The answer, error or command output.
    """
    first = " ".join(text.split())[:120]
    if kind == "answer":
        return _("Axon answered: {text}").format(text=first) if first else _("Axon answered.")
    if kind == "error":
        return (
            _("Axon couldn't finish: {text}").format(text=first)
            if first
            else _("Axon couldn't finish.")
        )
    if kind == "command":
        return _("Command finished.") if text.strip() != _("Not run.") else _("Command not run.")
    if kind == "cancelled":
        return _("Cancelled.")
    return first or _("Done.")
