"""Game mode policy for local Ollama requests.

While a game runs, Brain must not load a model onto the GPU. ``GameGate`` holds
that state and rewrites or refuses each request Brain is about to send to Ollama:

  * Not suspended: requests pass through unchanged.
  * Suspended: a request that only *unloads* a model passes (that is how Brain
    frees the GPU); every other inference request raises ``ProviderError`` with
    ``GAME_MODE_MESSAGE``.
  * Suspended, after the user pressed "Run anyway": requests run on the Speed
    model, on CPU only (``num_gpu: 0``), and are unloaded right after, for a
    limited time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from providers import ProviderError

GAME_MODE_MESSAGE = "Game mode is on. AI is paused to keep your game smooth."

# Ollama endpoints that load a model into memory
INFERENCE_PATHS = ("/api/generate", "/api/chat", "/api/embed", "/api/embeddings")

# How long "Run anyway" stays valid, and the most a caller may ask for (seconds)
DEFAULT_CPU_WINDOW = 120.0
MAX_CPU_WINDOW = 600.0


def is_unload_request(payload: dict[str, Any]) -> bool:
    """True for ``{"model": ..., "keep_alive": 0}`` with nothing to generate."""
    return (
        payload.get("keep_alive") == 0 and not payload.get("prompt") and "messages" not in payload
    )


class GameGate:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.suspended = False
        self.use_cloud = False
        self._cpu_until = 0.0

    def suspend(self) -> bool:
        """Returns True if this changed the state."""
        changed = not self.suspended
        self.suspended = True
        return changed

    def resume(self) -> bool:
        """Returns True if this changed the state."""
        changed = self.suspended
        self.suspended = False
        self._cpu_until = 0.0
        return changed

    def allow_cpu(self, seconds: float = DEFAULT_CPU_WINDOW) -> bool:
        """Let requests run on CPU for a while. Only meaningful while suspended."""
        if not self.suspended:
            return False
        self._cpu_until = self._clock() + max(0.0, min(float(seconds), MAX_CPU_WINDOW))
        return True

    @property
    def cpu_allowed(self) -> bool:
        return self.suspended and self._clock() < self._cpu_until

    def apply(self, path: str, payload: dict[str, Any], cpu_model: str = "") -> dict[str, Any]:
        """Return the payload to send for ``path``, or raise if game mode forbids it."""
        if not self.suspended or path not in INFERENCE_PATHS or is_unload_request(payload):
            return payload
        if not self.cpu_allowed:
            raise ProviderError(GAME_MODE_MESSAGE)
        options = {**payload.get("options", {}), "num_gpu": 0}
        out = {**payload, "options": options, "keep_alive": 0}
        if cpu_model and path != "/api/embed" and path != "/api/embeddings":
            out["model"] = cpu_model
        return out
