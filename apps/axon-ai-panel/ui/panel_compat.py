"""panel_compat.py — import-path bootstrap and Ollama client fallback for the AI panel.

Importing this module (before ``i18n``/``ui_logic``/``axon_gtk_utils``) makes the
intent-bar, ``services/`` and shared GTK helper directories importable, then
exposes ``OllamaClient`` and ``tool_sources`` from intent-bar, or stand-ins when
that module is absent.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import OllamaClient from the intent-bar app, falling back to a built-in
# stub if the module is not yet populated.
# ---------------------------------------------------------------------------
_intent_bar_path = str(Path(__file__).resolve().parents[2] / "intent-bar")
if _intent_bar_path not in sys.path:
    sys.path.insert(0, _intent_bar_path)

# services/ sits next to apps/ in the source tree and the .deb, and inside
# the app directory for install.sh (~/.local/share/axon-os/services).
for _base in (Path(__file__).resolve().parents[3], Path(__file__).resolve().parents[2]):
    if (_base / "services").is_dir():
        sys.path.insert(0, str(_base / "services"))
        break

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

try:
    from ollama_client import tool_sources  # type: ignore[import]
except ImportError:

    def tool_sources(steps: list[dict[str, Any]]) -> list[str]:  # type: ignore[misc]
        return []


try:
    from ollama_client import OllamaClient  # type: ignore[import]
except ImportError:
    import urllib.error
    import urllib.request

    class OllamaClient:  # type: ignore[no-redef]
        """Minimal Ollama client used when intent-bar's module is absent."""

        BASE_URL: str = "http://localhost:11434"

        def __init__(self, model: str = "llama3.2:3b") -> None:
            self.model = model

        def is_available(self) -> bool:
            try:
                urllib.request.urlopen(f"{self.BASE_URL}/api/tags", timeout=2)  # nosec B310 - fixed http URL
                return True
            except Exception:
                return False

        def list_models(self) -> list[str]:
            """Return list of locally available model names."""
            try:
                with urllib.request.urlopen(f"{self.BASE_URL}/api/tags", timeout=5) as resp:  # nosec B310
                    data = json.loads(resp.read().decode())
                    return [m["name"] for m in data.get("models", [])]
            except Exception:
                return []

        def chat_stream(
            self,
            messages: list[dict],
            system: str = "",
        ) -> Iterator[str]:
            """Yield text chunks from the Ollama /api/chat endpoint."""
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": True,
            }
            if system:
                payload["system"] = system

            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self.BASE_URL}/api/chat",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - BASE_URL
                    for raw_line in resp:
                        line = raw_line.decode().strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            chunk = obj.get("message", {}).get("content", "") or obj.get(
                                "response", ""
                            )
                            if chunk:
                                yield chunk
                            if obj.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                yield f"\n[Error: {exc}]"

        def list_conversations(self):
            return []

        def get_messages(self, conv_id):
            return []

        def create_conversation(self, title=None):
            return "fallback"

        def delete_conversation(self, conv_id):
            pass

        def send_message_stream(self, conv_id, message, system=""):
            return iter(["AI service unavailable."])

        def list_memories(self):
            return []

        def add_memory(self, text):
            return False

        def forget_memory(self, fact_id):
            return False

        def clear_memories(self):
            return False
