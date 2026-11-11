"""brain_http.py — Plain-HTTP access to local Ollama: POST/GET with retry, installed-model cache.

Mixed into ``BrainService`` (see ``brain_service.py``); methods rely on its state
(``self.store``, ``self.config``, ``self.providers`` ...) set up in ``_setup``.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from brain_ollama import (
    _INSTALLED_MODELS_TTL,
    _STREAM_READ_TIMEOUT,
)
from brain_sanitize import _require_http_url
from constants import OLLAMA_BASE_URL
from providers import OLLAMA_ID, parse_model_ref

logger = logging.getLogger("axon-brain")


class OllamaHttpMixin:
    """Plain-HTTP access to local Ollama: POST/GET with retry, installed-model cache."""

    if TYPE_CHECKING:
        # Defined on BrainService; declared here for the type checker only.
        _config_lock: Any
        config: Any

        def _resolve_local_model(self, model: str) -> str: ...

        @staticmethod
        def _open_with_retry(req: Any, timeout: Any, max_retries: Any) -> Any: ...

    def _http_post(self, url, payload, stream=False, timeout=60.0, max_retries=5):
        """POST JSON to Ollama, retrying only while it refuses connections."""
        _require_http_url(url)
        payload = self._game_mode_payload(url, payload)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        return self._open_with_retry(req, timeout, max_retries)

    def _game_mode_payload(self, url, payload):
        """Apply game mode to a request for Ollama (raises if it must not run)."""
        gate = getattr(self, "_gate", None)
        if gate is None or not gate.suspended or not url.startswith(OLLAMA_BASE_URL):
            return payload
        cpu_model = ""
        if gate.cpu_allowed:
            with self._config_lock:
                speed = str(self.config.get("speed_model", ""))
            if speed and not self._is_cloud(speed):
                cpu_model = self._resolve_local_model(self._local_name(speed))
        return gate.apply(url[len(OLLAMA_BASE_URL) :], payload, cpu_model)

    def _http_get(self, url, timeout=5.0, max_retries=5):
        """GET from Ollama, retrying only while it refuses connections."""
        _require_http_url(url)
        return self._open_with_retry(urllib.request.Request(url), timeout, max_retries)

    def _installed_models(self) -> list[str] | None:
        """Names of models pulled into Ollama, or ``None`` if it can't be reached."""
        now = time.monotonic()
        cache: tuple[float, list[str]] | None = getattr(self, "_installed_cache", None)
        if cache is not None and now - cache[0] < _INSTALLED_MODELS_TTL:
            return cache[1]
        try:
            with self._http_get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3.0, max_retries=1) as r:
                names = [m["name"] for m in json.loads(r.read().decode()).get("models", [])]
        except Exception as e:
            logger.debug("Could not list installed Ollama models: %s", e)
            return None
        self._installed_cache = (now, names)
        return names

    @staticmethod
    def _is_cloud(model: str) -> bool:
        """True if *model* is served by a configured provider rather than Ollama."""
        return parse_model_ref(model)[0] != OLLAMA_ID

    @staticmethod
    def _local_name(model: str) -> str:
        """``@ollama/x`` and ``x`` both name the local Ollama model ``x``."""
        provider, name = parse_model_ref(model)
        return name if provider == OLLAMA_ID else model

    @staticmethod
    def _set_stream_timeout(response: Any, timeout: float = _STREAM_READ_TIMEOUT) -> None:
        """Set a per-read socket timeout on an HTTP response for streaming.

        Prevents the daemon thread from blocking forever if Ollama hangs
        mid-stream. Raises ``TimeoutError`` on the next read if no data
        arrives within *timeout* seconds.
        """
        try:
            # http.client.HTTPResponse (urllib): fp is a BufferedReader over a
            # SocketIO whose _sock is the connection.
            sock = getattr(getattr(response.fp, "raw", None), "_sock", None)
            if sock is None:  # requests/urllib3-style response
                fp = response.raw._fp
                sock = getattr(fp, "sock", None) if fp is not None else None
            if sock is not None:
                sock.settimeout(timeout)
        except (AttributeError, OSError):
            pass  # Socket not yet connected or already closed
