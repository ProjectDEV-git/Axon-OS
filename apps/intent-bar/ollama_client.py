"""D-Bus proxy client for Intent Bar connecting to org.axonos.Brain."""

from __future__ import annotations

import json
import queue
from collections.abc import Iterator
from typing import Any

import dbus
from dbus.mainloop.glib import DBusGMainLoop

_ERROR_NOT_RUNNING: str = "[error] Axon Brain service is not reachable."


class OllamaClient:
    """Proxy client that forwards calls to the org.axonos.Brain D-Bus service."""

    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        timeout: float = 60.0,
    ) -> None:
        # Initialize DBus GLib integration if not already done
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        try:
            self.brain = self.bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
        except Exception:
            self.brain = None

        self.model = model
        self.q: queue.Queue[str | None] = queue.Queue()
        self.current_tx: str | None = None

        # Register signal listeners
        self.bus.add_signal_receiver(
            self._on_token_generated,
            signal_name="TokenGenerated",
            dbus_interface="org.axonos.Brain",
        )
        self.bus.add_signal_receiver(
            self._on_generation_completed,
            signal_name="GenerationCompleted",
            dbus_interface="org.axonos.Brain",
        )

    def _on_token_generated(self, transaction_id: str, token: str) -> None:
        if self.current_tx and transaction_id == self.current_tx:
            self.q.put(token)

    def _on_generation_completed(self, transaction_id: str, success: bool, error_msg: str) -> None:
        if self.current_tx and transaction_id == self.current_tx:
            if not success:
                self.q.put(f"\n[error] {error_msg}")
            self.q.put(None)  # Sentinel to stop generator

    def _get_brain(self) -> Any:
        if self.brain is None:
            try:
                self.brain = self.bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            except Exception as e:
                raise RuntimeError("org.axonos.Brain service is offline or unreachable.") from e
        return self.brain

    # ------------------------------------------------------------------
    # Health / discovery
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True when the Ollama daemon and Brain service are running."""
        try:
            brain = self._get_brain()
            status_json = brain.GetStatus()
            status = json.loads(status_json)
            return status.get("ollama_online", False)
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return the names of locally available models."""
        try:
            brain = self._get_brain()
            models_json = brain.ListModels()
            models = json.loads(models_json)
            return [m["name"] for m in models]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Generate (single-turn completion)
    # ------------------------------------------------------------------

    def generate(self, prompt: str, system: str = "") -> str:
        """Return a complete response for *prompt* (non-streaming)."""
        try:
            brain = self._get_brain()
            return str(brain.Generate(prompt, system, self.model, False))
        except Exception as exc:
            return f"[error] {exc}"

    def generate_stream(self, prompt: str, system: str = "") -> Iterator[str]:
        """Yield response tokens for *prompt* one by one (streaming) via D-Bus."""
        try:
            brain = self._get_brain()
            # Reset queue
            while not self.q.empty():
                self.q.get_nowait()

            # Call Generate to start stream and get transaction ID
            self.current_tx = str(brain.Generate(prompt, system, self.model, True))

            # Consume tokens from queue
            while True:
                token = self.q.get()
                if token is None:
                    break
                yield token
        except Exception as exc:
            yield f"\n[error] {exc}"
        finally:
            self.current_tx = None

    # ------------------------------------------------------------------
    # Chat (multi-turn)
    # ------------------------------------------------------------------

    def create_conversation(self, system_prompt: str = "", title: str = "") -> str:
        """Create a new conversation session and return its ID."""
        try:
            brain = self._get_brain()
            return str(brain.CreateConversation(system_prompt, title))
        except Exception:
            import uuid

            return str(uuid.uuid4())

    def list_conversations(self) -> list[dict[str, Any]]:
        """List all conversation sessions."""
        try:
            brain = self._get_brain()
            convs_json = brain.ListConversations()
            return list(json.loads(convs_json))
        except Exception:
            return []

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Retrieve all messages in a conversation."""
        try:
            brain = self._get_brain()
            msgs_json = brain.GetMessages(conversation_id)
            return list(json.loads(msgs_json))
        except Exception:
            return []

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation session."""
        try:
            brain = self._get_brain()
            brain.DeleteConversation(conversation_id)
        except Exception:
            pass

    def send_message_stream(
        self, conversation_id: str, message: str, model: str = ""
    ) -> Iterator[str]:
        """Send a message to a conversation and yield response tokens via D-Bus."""
        try:
            brain = self._get_brain()
            # Reset queue
            while not self.q.empty():
                self.q.get_nowait()

            # Call SendMessage to start stream and get transaction ID
            self.current_tx = str(brain.SendMessage(conversation_id, message, "", model, True))

            # Consume tokens from queue
            while True:
                token = self.q.get()
                if token is None:
                    break
                yield token
        except Exception as exc:
            yield f"\n[error] {exc}"
        finally:
            self.current_tx = None

    def chat(self, messages: list[dict[str, Any]], system: str = "") -> str:
        """Return a complete assistant reply for *messages* (non-streaming)."""
        prompt = messages[-1].get("content", "") if messages else ""
        return self.generate(prompt, system)

    def chat_stream(self, messages: list[dict[str, Any]], system: str = "") -> Iterator[str]:
        """Yield assistant reply tokens for *messages* one by one (streaming)."""
        prompt = messages[-1].get("content", "") if messages else ""
        return self.generate_stream(prompt, system)

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Unregister D-Bus signal receivers."""
        try:
            self.bus.remove_signal_receiver(
                self._on_token_generated,
                signal_name="TokenGenerated",
                dbus_interface="org.axonos.Brain",
            )
        except Exception:
            pass
        try:
            self.bus.remove_signal_receiver(
                self._on_generation_completed,
                signal_name="GenerationCompleted",
                dbus_interface="org.axonos.Brain",
            )
        except Exception:
            pass

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
