"""
ai_helper.py — AI integration layer for Axon Terminal.

Connects to the org.axonos.Brain D-Bus service to provide:
  • Command failure diagnosis (captures last command + stderr → AI analysis)
  • Natural language → shell command translation (NL command bar)
  • Smart suggestion generation after errors

All AI is local via Ollama, proxied through the Brain D-Bus service.
Falls back gracefully when the Brain service is unavailable.
"""

from __future__ import annotations

import json
import threading
from typing import Optional, Callable

import dbus
from gi.repository import GLib


class AIHelper:
    """Interface between Axon Terminal and the org.axonos.Brain D-Bus service."""

    BRAIN_BUS_NAME = "org.axonos.Brain"
    BRAIN_OBJ_PATH = "/org/axonos/Brain"
    BRAIN_IFACE = "org.axonos.Brain"

    CONTEXT_BUS_NAME = "org.axonos.Context"
    CONTEXT_OBJ_PATH = "/org/axonos/Context"

    def __init__(self) -> None:
        self._bus: Optional[dbus.SessionBus] = None
        self._brain: Optional[dbus.Interface] = None
        self._context: Optional[dbus.Interface] = None
        self._available: bool = False
        self._connect()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Attempt to connect to the Brain and Context D-Bus services."""
        try:
            self._bus = dbus.SessionBus()
        except dbus.exceptions.DBusException:
            self._available = False
            return

        # Brain service
        try:
            brain_obj = self._bus.get_object(self.BRAIN_BUS_NAME, self.BRAIN_OBJ_PATH)
            self._brain = dbus.Interface(brain_obj, self.BRAIN_IFACE)
            self._available = True
        except dbus.exceptions.DBusException:
            self._brain = None
            self._available = False

        # Context service (optional — not critical)
        try:
            ctx_obj = self._bus.get_object(self.CONTEXT_BUS_NAME, self.CONTEXT_OBJ_PATH)
            self._context = dbus.Interface(ctx_obj, self.CONTEXT_BUS_NAME)
        except dbus.exceptions.DBusException:
            self._context = None

    @property
    def is_available(self) -> bool:
        """Return True when the Brain D-Bus service is reachable."""
        return self._available

    def _ensure_connected(self) -> bool:
        """Reconnect if needed. Returns True when the Brain proxy is ready."""
        if self._brain is not None:
            return True
        self._connect()
        return self._brain is not None

    def _get_context_string(self) -> str:
        """Retrieve the desktop context string, or an empty string."""
        if self._context is not None:
            try:
                return str(self._context.GetContextString())
            except Exception:
                pass
        return ""

    # ------------------------------------------------------------------
    # Diagnose command failure
    # ------------------------------------------------------------------

    def diagnose_failure(
        self,
        command: str,
        exit_code: int,
        stderr_output: str,
        callback: Callable[[str], None],
    ) -> None:
        """Diagnose a failed command asynchronously.

        Args:
            command: The shell command that failed.
            exit_code: The exit code of the failed command.
            stderr_output: Captured stderr output.
            callback: Called on the GLib main thread with the diagnosis text.
        """
        if not self._ensure_connected():
            GLib.idle_add(callback, "⚠ AI diagnosis unavailable — Brain service is offline.")
            return

        thread = threading.Thread(
            target=self._diagnose_worker,
            args=(command, exit_code, stderr_output, callback),
            daemon=True,
        )
        thread.start()

    def _diagnose_worker(
        self,
        command: str,
        exit_code: int,
        stderr_output: str,
        callback: Callable[[str], None],
    ) -> None:
        prompt = (
            f"The following shell command failed with exit code {exit_code}:\n"
            f"```\n{command}\n```\n"
            f"Stderr output:\n```\n{stderr_output[:2000]}\n```\n\n"
            "Explain the error concisely and suggest how to fix it. "
            "Be brief (3-5 lines max). If you suggest a corrected command, "
            "wrap it in a code block."
        )
        context = self._get_context_string()
        try:
            assert self._brain is not None
            result = str(self._brain.Generate(prompt, context, "", False))
            GLib.idle_add(callback, result)
        except Exception as exc:
            GLib.idle_add(callback, f"⚠ AI diagnosis failed: {exc}")

    # ------------------------------------------------------------------
    # Natural language → shell command
    # ------------------------------------------------------------------

    def translate_to_command(
        self,
        natural_language: str,
        callback: Callable[[str], None],
    ) -> None:
        """Convert natural language to a shell command asynchronously.

        Args:
            natural_language: The user's plain-English description.
            callback: Called on the GLib main thread with the generated command.
        """
        if not self._ensure_connected():
            GLib.idle_add(callback, "")
            return

        thread = threading.Thread(
            target=self._translate_worker,
            args=(natural_language, callback),
            daemon=True,
        )
        thread.start()

    def _translate_worker(
        self,
        natural_language: str,
        callback: Callable[[str], None],
    ) -> None:
        prompt = (
            f"Convert this request into a single Linux shell command:\n"
            f"\"{natural_language}\"\n\n"
            "Reply with ONLY the command — no explanation, no markdown, no backticks. "
            "If the request is ambiguous, pick the most common interpretation."
        )
        context = self._get_context_string()
        try:
            assert self._brain is not None
            result = str(self._brain.Generate(prompt, context, "", False)).strip()
            # Strip any accidental backtick wrapping
            if result.startswith("```") and result.endswith("```"):
                result = result[3:-3].strip()
            if result.startswith("`") and result.endswith("`"):
                result = result[1:-1].strip()
            GLib.idle_add(callback, result)
        except Exception:
            GLib.idle_add(callback, "")

    # ------------------------------------------------------------------
    # Smart suggestion after errors
    # ------------------------------------------------------------------

    def get_suggestions(
        self,
        command: str,
        stderr_output: str,
        callback: Callable[[list[str]], None],
    ) -> None:
        """Generate 1-3 corrective command suggestions asynchronously.

        Args:
            command: The failed command.
            stderr_output: Captured stderr.
            callback: Called with a list of suggested commands.
        """
        if not self._ensure_connected():
            GLib.idle_add(callback, [])
            return

        thread = threading.Thread(
            target=self._suggestions_worker,
            args=(command, stderr_output, callback),
            daemon=True,
        )
        thread.start()

    def _suggestions_worker(
        self,
        command: str,
        stderr_output: str,
        callback: Callable[[list[str]], None],
    ) -> None:
        prompt = (
            f"This shell command failed:\n```\n{command}\n```\n"
            f"Error output:\n```\n{stderr_output[:1500]}\n```\n\n"
            "Suggest 1-3 corrected or alternative commands that would fix or "
            "work around this error. Reply ONLY as a JSON array of command "
            "strings, e.g. [\"cmd1\", \"cmd2\"]. No other text."
        )
        context = self._get_context_string()
        try:
            assert self._brain is not None
            raw = str(self._brain.Generate(prompt, context, "", False)).strip()
            # Try to parse JSON array from the response
            suggestions = self._parse_suggestions(raw)
            GLib.idle_add(callback, suggestions)
        except Exception:
            GLib.idle_add(callback, [])

    @staticmethod
    def _parse_suggestions(raw: str) -> list[str]:
        """Best-effort extraction of a JSON array from the AI response."""
        # Try direct JSON parse
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if s]
        except (json.JSONDecodeError, TypeError):
            pass

        # Try to find an embedded JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
                if isinstance(parsed, list):
                    return [str(s).strip() for s in parsed if s]
            except (json.JSONDecodeError, TypeError):
                pass

        return []
