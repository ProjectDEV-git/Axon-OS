"""Categorised Intent Bar suggestions while the user types.

Pure functions: callers pass in the installed apps and Space names, and
run the chosen action themselves. Categories, in order: Applications,
Files, Settings, Spaces, Commands, Ask AI. The Shell keeps a JavaScript
twin in shell/axon-shell/ui-logic.js (same data, same ranking); a test
checks the two lists match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from calculator import calculate
from i18n import N_, _

# Category ids stay English because code compares them; show them with _().
CALCULATOR = N_("Calculator")
APPS = N_("Applications")
FILES = N_("Files")
SETTINGS = N_("Settings")
SPACES = N_("Spaces")
COMMANDS = N_("Commands")
ASK = N_("Ask AI")
CATEGORIES = (CALCULATOR, APPS, FILES, SETTINGS, SPACES, COMMANDS, ASK)

# (title, GNOME Settings panel, extra search words)
SETTINGS_PANELS: list[tuple[str, str, str]] = [
    (N_("Wi-Fi"), "wifi", "network wireless internet"),
    (N_("Bluetooth"), "bluetooth", "devices headphones"),
    (N_("Displays"), "display", "screen monitor resolution scale"),
    (N_("Sound"), "sound", "volume audio speaker microphone"),
    (N_("Appearance"), "background", "dark light theme wallpaper"),
    (N_("Power"), "power", "battery sleep"),
    (N_("Keyboard"), "keyboard", "shortcuts layout"),
    (N_("Mouse & Touchpad"), "mouse", "pointer touchpad"),
    (N_("Privacy"), "privacy", "location camera"),
    (N_("Accessibility"), "universal-access", "a11y large text contrast zoom"),
]

# (exact allowlisted command, what it shows, extra search words). Running one
# still goes through the allowlist and the confirmation card.
SUGGESTED_COMMANDS: list[tuple[str, str, str]] = [
    ("df -h", N_("Free disk space"), "disk storage space"),
    ("free -h", N_("Memory in use"), "ram memory"),
    ("uptime", N_("How long the computer has been on"), "uptime load"),
    ("uname -a", N_("System and kernel version"), "kernel version system"),
    ("whoami", N_("Your user name"), "user account"),
    ("date", N_("Date and time"), "time clock"),
]

_MAX_PER_CATEGORY = 3


@dataclass(frozen=True)
class Suggestion:
    """One row in the suggestion list."""

    category: str
    title: str
    subtitle: str
    action: dict[str, Any] = field(default_factory=dict)

    @property
    def accessible_label(self) -> str:
        return f"{_(self.category)}: {self.title}. {self.subtitle}"


def match_score(query: str, *texts: str) -> int:
    """How well *query* matches any of *texts*: 3 prefix, 2 word prefix, 1 inside, 0 none.

    Multi-word queries only match from the start of a word, so a question
    such as "what is firefox" doesn't turn into "open Firefox".
    """
    q = query.strip().lower()
    if not q:
        return 0
    best = 0
    for text in texts:
        t = text.lower()
        if t.startswith(q):
            return 3
        if any(word.startswith(q) for word in t.replace("-", " ").split()):
            best = max(best, 2)
        elif " " not in q and len(q) >= 3 and q in t:
            best = max(best, 1)
    return best


def _top(scored: list[tuple[int, Suggestion]], limit: int) -> list[Suggestion]:
    scored.sort(key=lambda item: (-item[0], item[1].title.lower()))
    return [s for _score, s in scored[:limit]]


def build_suggestions(
    query: str,
    apps: list[tuple[str, str, str]] | None = None,
    spaces: list[str] | None = None,
    limit: int = _MAX_PER_CATEGORY,
) -> list[Suggestion]:
    """Suggestions for *query*, grouped in CATEGORIES order.

    Args:
        query: What the user typed.
        apps: Installed apps as ``(app_id, name, keywords)``.
        spaces: Space names, in Super+1…9 order.
        limit: Most rows per category (Ask AI is always one row).

    Returns:
        A flat list; consecutive rows share a category.
    """
    q = query.strip()
    if not q:
        return []
    out: list[Suggestion] = []

    if q.lower().startswith("find ") and len(q) > 5:
        target = q[5:].strip()
        return [
            Suggestion(
                FILES,
                _("Search files for “{query}”").format(query=target),
                _("Semantic search in your files"),
                {"type": "files", "query": target, "explicit": True},
            ),
            Suggestion(ASK, _("Ask Axon AI"), q, {"type": "ask", "query": q}),
        ]

    # Typed arithmetic answers at once; Enter copies the result
    result = calculate(q)
    if result is not None:
        out.append(
            Suggestion(
                CALCULATOR,
                f"= {result}",
                _("Enter copies the result"),
                {"type": "copy", "text": result},
            )
        )

    out += _top(
        [
            (
                score,
                Suggestion(APPS, name, _("Application"), {"type": "open_app", "app_id": app_id}),
            )
            for app_id, name, keywords in apps or []
            if (score := match_score(q, name, keywords)) >= 2
        ],
        limit,
    )
    out.append(
        Suggestion(
            FILES,
            _("Search files for “{query}”").format(query=q),
            _("Semantic search in your files"),
            {"type": "files", "query": q},
        )
    )
    out += _top(
        [
            (
                score,
                Suggestion(SETTINGS, _(title), _("Settings"), {"type": "settings", "panel": panel}),
            )
            for title, panel, words in SETTINGS_PANELS
            if (score := match_score(q, title, _(title), words)) >= 2
        ],
        limit,
    )
    out += _top(
        [
            (
                score,
                Suggestion(
                    SPACES,
                    _("{name} Space").format(name=name),
                    f"Super+{i + 1}",
                    {"type": "space", "index": i, "name": name},
                ),
            )
            for i, name in enumerate(spaces or [])
            if (score := match_score(q, name, f"{name} space")) >= 2
        ],
        limit,
    )
    out += _top(
        [
            (
                score,
                Suggestion(
                    COMMANDS, command, _(description), {"type": "command", "command": command}
                ),
            )
            for command, description, words in SUGGESTED_COMMANDS
            if (score := match_score(q, command, description, _(description), words)) >= 2
        ],
        limit,
    )
    out.append(Suggestion(ASK, _("Ask Axon AI"), q, {"type": "ask", "query": q}))
    return out


def describe(suggestion: Suggestion) -> str:
    """Action preview, e.g. "Open Firefox" or "Run: df -h"."""
    action = suggestion.action
    kind = action.get("type")
    if kind == "copy":
        return _("Copy {text}").format(text=action.get("text", ""))
    if kind == "open_app":
        return _("Open {app}").format(app=suggestion.title)
    if kind == "files":
        return _("Search your files for “{query}”").format(query=action.get("query", ""))
    if kind == "settings":
        return _("Open Settings → {panel}").format(panel=suggestion.title)
    if kind == "space":
        return _("Switch to {name} Space").format(name=action.get("name", ""))
    if kind == "command":
        return _("Run: {command} (asks first)").format(command=action.get("command", ""))
    return _("Ask Axon AI")


_STRONG = frozenset({CALCULATOR, APPS, SETTINGS, SPACES, COMMANDS})


def default_index(rows: list[Suggestion]) -> int:
    """Row Enter runs when the user hasn't moved the selection.

    The best app, setting, Space or command match; the file search only
    after "find …"; otherwise "Ask Axon AI", so plain questions go to the AI.
    """
    for i, row in enumerate(rows):
        if row.category in _STRONG or row.action.get("explicit"):
            return i
    return len(rows) - 1
