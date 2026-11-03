"""Fit a chat history into a model's context window.

Keeps the system prompt and the newest turns, drops the oldest, and leaves a
short extractive note of what was dropped so the model keeps the thread.
"""

from __future__ import annotations

DEFAULT_LOCAL_TOKENS = 8192
DEFAULT_CLOUD_TOKENS = 128_000
RESERVE_TOKENS = 1024  # room for the reply
_NOTE_CHARS = 160
_MAX_NOTE_ITEMS = 12

# Substring of the model name -> context window in tokens.
_KNOWN = (
    ("claude", 200_000),
    ("gemini", 1_000_000),
    ("gpt-4o", 128_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-5", 400_000),
    ("llama3.1", 128_000),
    ("llama3.2", 128_000),
    ("llama3.3", 128_000),
    ("qwen2.5", 32_768),
    ("qwen3", 40_960),
    ("mistral", 32_768),
    ("phi3", 4096),
    ("gemma", 8192),
)


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token)."""
    return len(text) // 4 + 1


def context_limit(model: str, cloud: bool = False) -> int:
    """Best-known context window for *model*."""
    name = (model or "").lower()
    for key, size in _KNOWN:
        if key in name:
            return size
    return DEFAULT_CLOUD_TOKENS if cloud else DEFAULT_LOCAL_TOKENS


def fit_messages(
    messages: list[dict[str, str]], budget: int, reserve: int = RESERVE_TOKENS
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Drop the oldest non-system turns until the history fits.

    The system message (if first) and the newest message are always kept.

    Returns:
        ``(kept, dropped)``; dropped is in original order.
    """
    if not messages:
        return [], []
    system = [messages[0]] if messages[0].get("role") == "system" else []
    turns = messages[len(system) :]
    room = budget - reserve - sum(estimate_tokens(m["content"]) for m in system)
    kept: list[dict[str, str]] = []
    used = 0
    for m in reversed(turns):
        cost = estimate_tokens(m["content"])
        if kept and used + cost > room:
            break
        kept.append(m)
        used += cost
    kept.reverse()
    dropped = turns[: len(turns) - len(kept)]
    # A history should not start with an assistant turn.
    while len(kept) > 1 and kept[0].get("role") == "assistant":
        dropped.append(kept.pop(0))
    return system + kept, dropped


def summarize_dropped(dropped: list[dict[str, str]]) -> str:
    """Short extractive note listing what the user asked earlier."""
    items = [m["content"] for m in dropped if m.get("role") == "user"][-_MAX_NOTE_ITEMS:]
    if not items:
        return ""
    lines = []
    for text in items:
        one = " ".join(text.split())
        if len(one) > _NOTE_CHARS:
            one = one[: _NOTE_CHARS - 1] + "…"
        lines.append(f"- {one}")
    return "Earlier in this conversation (older turns omitted), the user asked:\n" + "\n".join(
        lines
    )
