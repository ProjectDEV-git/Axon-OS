"""Ollama timeouts and error helpers shared by the Brain's HTTP and streaming code."""

import json
import urllib.error

# Per-read timeout for Ollama streaming (seconds), set once tokens start
# flowing. If no data arrives within this window, the stream is considered hung.
_STREAM_READ_TIMEOUT = 120.0

# How long to wait for Ollama's first byte on a generate/chat request. Ollama
# sends nothing until the model is loaded into memory and the prompt is read,
# which takes minutes for a cold 7B+ model on CPU or a slow disk.
_OLLAMA_LOAD_TIMEOUT = 300.0

# How long a fetched list of installed Ollama models stays fresh (seconds).
_INSTALLED_MODELS_TTL = 15.0


def _is_retryable(exc: BaseException) -> bool:
    """True for errors worth retrying: Ollama not accepting connections yet.

    HTTP errors (e.g. 404 model not found) and timeouts (model still loading)
    are not retried: repeating them only multiplies the wait.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        exc = exc.reason if isinstance(exc.reason, BaseException) else exc
    if isinstance(exc, TimeoutError):
        return False
    return isinstance(exc, (ConnectionError, FileNotFoundError))


def _ollama_error_message(exc: BaseException, model: str = "") -> str:
    """Turn an Ollama HTTP failure into a message a user can act on."""
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            detail = json.loads(exc.read().decode(errors="replace") or "{}").get("error", "")
        except Exception:
            pass
        if exc.code == 404 and model:
            return f"Model '{model}' is not downloaded. Run: ollama pull {model}"
        return f"Ollama error {exc.code}: {detail}" if detail else f"Ollama error {exc.code}"
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return "Ollama took too long to respond (the model may still be loading)"
    if isinstance(reason, (ConnectionError, FileNotFoundError)):
        return "Ollama is not running. Start it with: sudo systemctl start ollama"
    return f"Ollama request failed: {reason}"


def _same_model(a: str, b: str) -> bool:
    """Compare Ollama tags, treating a missing tag as ``:latest``."""

    def norm(name: str) -> str:
        return name if ":" in name else f"{name}:latest"

    return norm(a) == norm(b)
