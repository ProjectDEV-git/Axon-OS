"""Pure helpers for routing transcribed speech — unit-testable, no D-Bus/GTK."""

from __future__ import annotations

import json
from typing import Any

# Filler tokens whisper emits for silence / noise; a transcript that is only
# these is discarded instead of being sent to the Brain.
_NOISE_TRANSCRIPTS = {
    "",
    ".",
    "you",
    "thank you.",
    "thanks for watching!",
    "[blank_audio]",
    "[music]",
    "(music)",
    "uh",
    "um",
}


def clean_transcript(text: str) -> str:
    """Normalise a whisper transcript; empty string when it is just noise."""
    cleaned = " ".join(text.split()).strip()
    if cleaned.lower() in _NOISE_TRANSCRIPTS:
        return ""
    return cleaned


def parse_intent_response(raw: str) -> tuple[str, Any]:
    """Interpret a Brain.ClassifyIntent reply.

    Returns ("open_app", app) | ("run_command", cmd) | ("say", text).
    """
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return ("say", raw)
        if isinstance(data, dict):
            action = data.get("action", "")
            if action == "open_app" and data.get("app"):
                return ("open_app", str(data["app"]))
            if action == "run_command" and data.get("command"):
                return ("run_command", str(data["command"]))
            if action == "error":
                # the Brain's own failure report: say the message, not the JSON
                return ("say", str(data.get("message") or "The AI is not available right now."))
    return ("say", raw)
