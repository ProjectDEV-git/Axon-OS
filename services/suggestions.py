"""Categorised Intent Bar suggestions while the user types.

Pure functions: callers pass in the installed apps and Space names, and
run the chosen action themselves. Categories, in order: Applications,
Files, Settings, Spaces, Commands, Ask AI. The Shell keeps a JavaScript
twin in shell/axon-shell/ui-logic.js (same data, same ranking); a test
checks the two lists match.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from calculator import calculate
from i18n import N_, _

# Category ids stay English because code compares them; show them with _().
CALCULATOR = N_("Calculator")
APPS = N_("Applications")
FILES = N_("Files")
SETTINGS = N_("Settings")
SPACES = N_("Spaces")
GAME_MODE = N_("Game mode")
GAME_CENTER = N_("Game Center")
COMMANDS = N_("Commands")
ASK = N_("Ask AI")
CATEGORIES = (CALCULATOR, APPS, FILES, SETTINGS, SPACES, GAME_MODE, GAME_CENTER, COMMANDS, ASK)

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

# (operation, title, subtitle) for the "game mode" intents
GAME_MODE_ROWS: dict[str, tuple[str, str]] = {
    "on": (N_("Turn game mode on"), N_("Pause AI while you play")),
    "off": (N_("Turn game mode off"), N_("Bring AI back")),
    "status": (N_("Is game mode on?"), N_("See what is running")),
}

_CATALOG_PATHS = (
    Path("/usr/share/axon/gaming/catalog.json"),
    Path(__file__).resolve().parent.parent / "data" / "gaming" / "catalog.json",
)

# What the Intent Bar can install from the gaming catalog:
# items are (id, name, aliases) and presets are (id, name), both from catalog.json
CatalogItems = list[tuple[str, str, list[str]]]
CatalogPresets = list[tuple[str, str]]

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


def game_mode_ops(query: str) -> list[str]:
    """Which game mode intents *query* asks for: [] if it is not about game mode.

    "game mode on" turns it on, "game mode off" turns it off, "is game mode on"
    asks; a bare "game mode" offers all three.
    """
    words = set(re.findall(r"[a-z]+", query.lower()))
    if not ("game" in words and "mode" in words) and "gamemode" not in words:
        return []
    if "is" in words or "status" in words or query.rstrip().endswith("?"):
        return ["status"]
    if words & {"off", "stop", "disable", "exit", "end"}:
        return ["off"]
    if words & {"on", "start", "enable"}:
        return ["on"]
    return ["status", "on", "off"]


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def find_catalog_item(text: str, items: CatalogItems) -> tuple[str, str] | None:
    """(id, name) of the catalog item ``text`` names: an exact id, name or alias, else a prefix.

    "epic games" finds Heroic and "proton ge" finds ProtonPlus. Twin of findCatalogItem().
    """
    q = _norm(text)
    if not q:
        return None
    names = [(i, n, [_norm(i), _norm(n), *(_norm(a) for a in aliases)]) for i, n, aliases in items]
    for item_id, name, options in names:
        if q in options:
            return item_id, name
    for item_id, name, options in names:
        if any(o.startswith(q + " ") or (len(q) >= 3 and o.startswith(q)) for o in options):
            return item_id, name
    return None


def game_center_rows(
    query: str, items: CatalogItems, presets: CatalogPresets
) -> list[tuple[str, str, dict[str, Any]]]:
    """(title, subtitle, action) for "install heroic", "open game center", "update proton".

    Twin of gameCenterRows() in the Shell.
    """
    q = _norm(query)
    words = q.split()
    if not words:
        return []
    if q in ("game center", "open game center", "gaming apps", "open gaming apps"):
        return [
            (
                _("Open Game Center"),
                _("Install launchers, Proton and tools"),
                {"type": "game_center", "op": "open", "page": ""},
            )
        ]
    if words[0] == "update" and "proton" in words[1:]:
        return [
            (
                _("Update Proton"),
                _("Open Game Center to pick a version"),
                {"type": "game_center", "op": "open", "page": "compatibility"},
            )
        ]
    if words[0] != "install" or len(words) < 2:
        return []
    rest = " ".join(words[1:])
    rest_words = set(words[1:])
    # "install the gaming starter pack", "install streamer pack"; "install retro" alone
    # means RetroArch, so a preset needs the word "pack" or "preset" ("starter" is unique)
    for preset_id, name in presets:
        if preset_id in rest_words and (preset_id == "starter" or rest_words & {"pack", "preset"}):
            return [
                (
                    _("Install the {name} pack").format(name=name),
                    _("Opens Game Center"),
                    {"type": "game_center", "op": "preset", "preset": preset_id},
                )
            ]
    found = find_catalog_item(rest, items)
    if found is None:
        return []
    item_id, name = found
    return [
        (
            _("Install {name}").format(name=name),
            _("Opens Game Center"),
            {"type": "game_center", "op": "install", "ids": [item_id]},
        )
    ]


def load_catalog_entries(path: Path | None = None) -> tuple[CatalogItems, CatalogPresets] | None:
    """(items, presets) from the gaming catalog for ``build_suggestions``; None if unreadable."""
    for candidate in [path] if path else _CATALOG_PATHS:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            items = [(i["id"], i["name"], list(i.get("aliases", []))) for i in data["items"]]
            presets = [(p["id"], p["name"]) for p in data["presets"]]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        return items, presets
    return None


def game_center_argv(action: dict[str, Any]) -> list[str] | None:
    """The command for a ``game_center`` suggestion, or None if the action looks wrong.

    Ids come from the catalog, but they are checked again here: they end up in argv.
    """
    ident = re.compile(r"[a-z0-9][a-z0-9-]*")
    op = action.get("op")
    if op == "open":
        page = str(action.get("page", ""))
        if page and page not in (
            "discover",
            "installed",
            "compatibility",
            "performance",
            "drivers",
        ):
            return None
        return ["axon-game-center", *(["--page", page] if page else [])]
    if op == "install":
        ids = [str(i) for i in action.get("ids", [])]
        if ids and all(ident.fullmatch(i) for i in ids):
            return ["axon-game-center", "--install", *ids]
    if op == "preset" and ident.fullmatch(str(action.get("preset", ""))):
        return ["axon-game-center", "--preset", str(action["preset"])]
    return None


def _top(scored: list[tuple[int, Suggestion]], limit: int) -> list[Suggestion]:
    scored.sort(key=lambda item: (-item[0], item[1].title.lower()))
    return [s for _score, s in scored[:limit]]


def build_suggestions(
    query: str,
    apps: list[tuple[str, str, str]] | None = None,
    spaces: list[str] | None = None,
    limit: int = _MAX_PER_CATEGORY,
    catalog: tuple[CatalogItems, CatalogPresets] | None = None,
) -> list[Suggestion]:
    """Suggestions for *query*, grouped in CATEGORIES order.

    Args:
        query: What the user typed.
        apps: Installed apps as ``(app_id, name, keywords)``.
        spaces: Space names, in Super+1…9 order.
        limit: Most rows per category (Ask AI is always one row).
        catalog: Gaming catalog as ``(items, presets)`` for the install intents.

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
    out += [
        Suggestion(
            GAME_MODE,
            _(GAME_MODE_ROWS[op][0]),
            _(GAME_MODE_ROWS[op][1]),
            {"type": "game_mode", "op": op},
        )
        for op in game_mode_ops(q)
    ]
    if catalog is not None:
        out += [
            Suggestion(GAME_CENTER, title, subtitle, action)
            for title, subtitle, action in game_center_rows(q, *catalog)
        ]
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
    if kind == "game_center":
        return suggestion.title
    if kind == "game_mode":
        return _(GAME_MODE_ROWS.get(str(action.get("op")), ("", ""))[0])
    if kind == "command":
        return _("Run: {command} (asks first)").format(command=action.get("command", ""))
    return _("Ask Axon AI")


_STRONG = frozenset({CALCULATOR, APPS, SETTINGS, SPACES, GAME_MODE, GAME_CENTER, COMMANDS})


def default_index(rows: list[Suggestion]) -> int:
    """Row Enter runs when the user hasn't moved the selection.

    The best app, setting, Space or command match; the file search only
    after "find …"; otherwise "Ask Axon AI", so plain questions go to the AI.
    """
    for i, row in enumerate(rows):
        if row.category in _STRONG or row.action.get("explicit"):
            return i
    return len(rows) - 1
