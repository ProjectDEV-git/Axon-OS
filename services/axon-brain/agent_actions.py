"""Built-in tools the Brain agent can call.

Each tool talks to an existing Axon service over D-Bus or to a pure helper,
so the agent can only do what those services already allow. Desktop changes
go through the GuiAgent, whose allowlist (``plan.py``) is the safety boundary.
"""

from __future__ import annotations

import json
from typing import Any

from tools import Tool, ToolRegistry

_SEARCH = ("org.axonos.Search", "/org/axonos/Search")
_CONTEXT = ("org.axonos.Context", "/org/axonos/Context")
_GUI = ("org.axonos.GuiAgent", "/org/axonos/GuiAgent")
_DBUS_TIMEOUT = 20


def _call(target: tuple[str, str], method: str, *args: Any) -> Any:
    import dbus

    name, path = target
    obj = dbus.SessionBus().get_object(name, path)
    return getattr(dbus.Interface(obj, name), method)(*args, timeout=_DBUS_TIMEOUT)


def _arg(args: dict[str, Any], key: str, limit: int = 500) -> str:
    value = str(args.get(key, "")).strip()
    if not value:
        raise ValueError(f"missing argument '{key}'")
    return value[:limit]


def _search_files(args: dict[str, Any]) -> str:
    import dbus

    raw = _call(_SEARCH, "Query", _arg(args, "query"), dbus.Int32(8))
    hits = json.loads(str(raw))
    if isinstance(hits, dict):
        hits = hits.get("results", [])
    if not hits:
        return "no matching files"
    lines = []
    for h in hits[:8]:
        snippet = " ".join(str(h.get("snippet", h.get("text", ""))).split())[:200]
        lines.append(f"{h.get('path', '?')}: {snippet}")
    return "\n".join(lines)


def _ask_files(args: dict[str, Any]) -> str:
    import dbus

    raw = _call(_SEARCH, "Retrieve", _arg(args, "question"), dbus.Int32(6))
    chunks = json.loads(str(raw))
    if not chunks:
        return "no relevant passages in the user's files"
    return "\n\n".join(f"[{c['path']}]\n{c['text']}" for c in chunks)


def _get_context(_args: dict[str, Any]) -> str:
    import dbus

    return str(_call(_CONTEXT, "GetContextForPrompt", dbus.Int32(2000))) or "no context"


def _calculate(args: dict[str, Any]) -> str:
    from calculator import calculate

    result = calculate(_arg(args, "expression", 200))
    return result if result is not None else "error: not a calculation I can evaluate"


def _desktop_action(args: dict[str, Any]) -> str:
    return str(_call(_GUI, "Execute", _arg(args, "instruction")))


def build_registry(brain: Any) -> ToolRegistry:
    """Tools bound to a running BrainService (for memory access)."""
    reg = ToolRegistry()
    reg.register(
        Tool(
            "search_files",
            "find the user's files by name or content",
            _search_files,
            {"query": "words to search for"},
        )
    )
    reg.register(
        Tool(
            "ask_files",
            "get passages from the user's files that answer a question; cite the file paths",
            _ask_files,
            {"question": "what to look up"},
        )
    )
    reg.register(
        Tool(
            "get_context",
            "see the active window, clipboard, recent terminal commands and errors",
            _get_context,
        )
    )
    reg.register(
        Tool(
            "calculate",
            "evaluate arithmetic exactly",
            _calculate,
            {"expression": "e.g. 17.5% of 2400"},
        )
    )
    reg.register(
        Tool(
            "desktop_action",
            "change desktop settings or open apps (dark mode, wallpaper, night light, launch app)",
            _desktop_action,
            {"instruction": "what to do, in plain words"},
            needs_approval=True,
        )
    )

    def remember(args: dict[str, Any]) -> str:
        fid = brain.memory.add(_arg(args, "fact", 300))
        return f"remembered (id {fid})"

    def recall(args: dict[str, Any]) -> str:
        facts = brain.memory.recall(_arg(args, "query"), k=8)
        return "\n".join(f"- {f}" for f in facts) or "nothing remembered about that"

    reg.register(
        Tool(
            "remember",
            "save a lasting fact or preference about the user",
            remember,
            {"fact": "one short sentence"},
        )
    )
    reg.register(Tool("recall", "look up saved facts about the user", recall, {"query": "topic"}))
    return reg


def approve(tool: Tool, args: dict[str, Any]) -> bool:
    """Approval policy for tools marked ``needs_approval``.

    Desktop actions are limited by the GuiAgent allowlist (preferences, app
    launch, notifications), which can show its own confirmation, so they are
    allowed. Anything else that asks for approval is refused until a UI
    prompt exists for it.
    """
    return tool.name == "desktop_action"
