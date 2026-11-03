"""Reason → call tool → observe loop for the Axon Brain.

Pure logic: the model is any ``complete(messages) -> str`` callable, so the
loop is the same for Ollama and cloud providers and is unit-testable.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tools import Tool, ToolRegistry, format_tool_result, parse_tool_call

MAX_STEPS = 6

FINAL_NUDGE = "Stop calling tools now and give the user your final answer."


@dataclass
class AgentResult:
    """Outcome of one agent run.

    Attributes:
        answer: Final text for the user.
        steps: Tool calls made, as dicts with name, arguments, result.
        cancelled: True when the cancel event stopped the run.
    """

    answer: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False


def run_agent(
    complete: Callable[[list[dict[str, str]]], str],
    messages: list[dict[str, str]],
    registry: ToolRegistry,
    max_steps: int = MAX_STEPS,
    cancel: threading.Event | None = None,
    on_tool: Callable[[dict[str, Any]], None] | None = None,
    approve: Callable[[Tool, dict[str, Any]], bool] | None = None,
) -> AgentResult:
    """Run the tool loop until the model answers without a tool call.

    Args:
        complete: Sends a message list to the model, returns its reply.
        messages: Starting conversation, system prompt first. Not modified.
        registry: Tools the model may call.
        max_steps: Most tool calls allowed before forcing an answer.
        cancel: Set to stop between steps.
        on_tool: Called after each tool call with its step dict.
        approve: Decides whether a ``needs_approval`` tool may run.

    Returns:
        The final answer and the tool calls made.
    """
    msgs = list(messages)
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _ in range(max_steps):
        if cancel is not None and cancel.is_set():
            return AgentResult("", steps, cancelled=True)
        reply = complete(msgs)
        call = parse_tool_call(reply)
        if call is None:
            return AgentResult(reply.strip(), steps)
        name, args = call
        key = f"{name}:{sorted(args.items())!r}"
        if key in seen:
            result = "error: you already made this exact call; use the earlier result"
        else:
            seen.add(key)
            result = registry.run(name, args, approve)
        step = {"name": name, "arguments": args, "result": result}
        steps.append(step)
        if on_tool is not None:
            on_tool(step)
        msgs.append({"role": "assistant", "content": reply})
        msgs.append({"role": "user", "content": format_tool_result(name, result)})
    if cancel is not None and cancel.is_set():
        return AgentResult("", steps, cancelled=True)
    msgs.append({"role": "user", "content": FINAL_NUDGE})
    reply = complete(msgs)
    # A model that still insists on a tool call gets its text with the call removed.
    if parse_tool_call(reply) is not None:
        reply = reply.split("<tool_call>", 1)[0].strip() or "I couldn't finish that request."
    return AgentResult(reply.strip(), steps)
