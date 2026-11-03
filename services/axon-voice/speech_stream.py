"""Turn a stream of model tokens into speakable sentences — pure logic.

The voice service speaks each sentence as soon as it is complete instead of
waiting for the whole reply, which cuts the time to first audio.
"""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?。！？])[\"')\]]*\s+|\n{2,}|\n(?=[-*•]|\d+\.)")
_MARKDOWN = re.compile(r"(\*\*|__|`{1,3}|^#{1,6}\s*|^\s*[-*•]\s+|\[(\d+)\])", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
MIN_CHARS = 20  # avoid speaking tiny fragments like "Dr." on their own
MAX_CHARS = 400  # speak long run-on text in pieces


def speakable(text: str) -> str:
    """Strip markdown, citation marks and extra whitespace."""
    text = _LINK.sub(r"\1", text)
    text = _MARKDOWN.sub("", text)
    return " ".join(text.split())


class SentenceBuffer:
    """Collects tokens and releases whole sentences."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, token: str) -> list[str]:
        """Add a token; return any sentences that are now complete."""
        self._buf += token
        out: list[str] = []
        start = 0
        for m in _SENTENCE_END.finditer(self._buf):
            piece = self._buf[start : m.end()]
            if len(piece.strip()) < MIN_CHARS:
                continue
            out.append(piece)
            start = m.end()
        self._buf = self._buf[start:]
        while len(self._buf) > MAX_CHARS:
            cut = self._buf.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            out.append(self._buf[:cut])
            self._buf = self._buf[cut:]
        return [s for s in (speakable(p) for p in out) if s]

    def flush(self) -> list[str]:
        """Return whatever is left as a final sentence."""
        rest, self._buf = speakable(self._buf), ""
        return [rest] if rest else []
