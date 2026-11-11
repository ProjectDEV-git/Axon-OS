"""panel_stream.py — Sending a message and streaming the answer into a bubble.

Mixed into ``AIPanelWindow`` (see ``panel.py``); methods rely on its widgets/state.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Importing panel_compat first puts services/ and apps/shared on sys.path.
import panel_compat  # noqa: F401
from gi.repository import GLib

# isort: split
from axon_gtk_utils import allow_cpu_only, announce
from context_settings import SENSITIVE_ITEMS, load_settings, sent_files
from i18n import _
from panel_bubble import MessageBubble
from panel_compat import tool_sources
from ui_logic import (
    announcement,
    answer_meta,
    parse_action,
    split_error,
)


class StreamMixin:
    """Sending a message and streaming the answer into a bubble."""

    def _add_bubble(self, role: str, text: str = "") -> MessageBubble:
        bubble = MessageBubble(role, text)
        self._messages_box.append(bubble)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self) -> None:
        def _do_scroll() -> bool:
            adj = self._scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False

        GLib.idle_add(_do_scroll)

    def _send_message(self, text: str) -> None:
        text = text.strip()
        if not text or self._streaming:
            return
        if self._needs_cloud_warning():
            self._confirm_cloud_send(text)
            return
        self._submit(text)

    def _submit(self, text: str) -> None:
        self._entry.set_text("")
        self._add_bubble("user", text)
        self._start_request(text)

    def _start_request(self, text: str) -> None:
        """Stream an answer to *text* into a new assistant bubble."""
        request_id = self._requests.start()
        self._set_streaming(True)
        self._stream_prompt = text
        self._stream_started = time.monotonic()
        self._first_token_at: float | None = None
        self._stream_bubble = self._add_bubble("assistant", "")

        exclude = set(self._ctx_excluded)
        if self.current_origin().leaves_computer and self._cloud_ack_conv != self._conv_id:
            # Not warned in this conversation: send no sensitive context
            exclude |= SENSITIVE_ITEMS
        snapshot = self._ctx_snapshot
        self._ctx_excluded = set()  # removals apply to one message

        # The availability check and context lookup are D-Bus/HTTP calls:
        # they run in the worker so the panel never freezes.
        model = self._client.model
        thread = threading.Thread(
            target=self._stream_response,
            args=(text, model, request_id, frozenset(exclude), snapshot),
            daemon=True,
        )
        thread.start()

    def _set_streaming(self, active: bool) -> None:
        self._streaming = active
        self._spinner.set_visible(active)
        self._send_btn.set_visible(not active)
        self._stop_btn.set_visible(active)
        if active:
            self._spinner.start()
        else:
            self._spinner.stop()

    def _cancel_stream(self) -> None:
        """Stop the streaming answer, keep what arrived and mark it cancelled."""
        if not self._requests.cancel():
            return
        self._set_streaming(False)
        if self._stream_bubble is not None:
            partial = self._stream_bubble.get_text()
            self._stream_bubble.set_final_text(
                partial + "\n\n" + _("(Cancelled)") if partial else _("Cancelled")
            )
            announce(self, announcement("cancelled"))
            self._stream_bubble = None
        self._entry.grab_focus()
        cancel = getattr(self._client, "cancel", None)
        if cancel is not None:
            threading.Thread(target=cancel, daemon=True).start()

    def _stream_response(
        self,
        text: str,
        model: str,
        request_id: int,
        exclude: frozenset[str] = frozenset(),
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        if not self._client.is_available():
            GLib.idle_add(self._on_ai_unavailable, request_id)
            return
        sources: list[str] = []
        if hasattr(self._ctx_reader, "snapshot"):
            if snapshot is None:
                snapshot = self._ctx_reader.snapshot()
            ctx = self._ctx_reader.build_context_string(exclude=exclude, snapshot=snapshot)
            if ctx:
                settings = getattr(self._ctx_reader, "settings", load_settings)()
                sources = sent_files(snapshot, settings, exclude)
        else:
            ctx = self._ctx_reader.build_context_string()
        accumulated = ""
        try:
            for chunk in self._client.send_message_stream(
                self._conv_id, text, model=model, ctx=ctx
            ):
                if self._stream_cancelled or not self._requests.is_current(request_id):
                    return
                accumulated += chunk
                GLib.idle_add(self._on_chunk, chunk, request_id)
        except Exception as exc:
            error_text = f"\n[Error: {exc}]"
            accumulated += error_text
            GLib.idle_add(self._on_chunk, error_text, request_id)
        steps = list(getattr(self._client, "tool_steps", []) or [])
        if not self._stream_cancelled:
            GLib.idle_add(self._on_stream_done, accumulated, request_id, sources, steps)

    def _on_ai_unavailable(self, request_id: int) -> bool:
        if not self._requests.finish(request_id):
            return False
        self._set_streaming(False)
        if self._stream_bubble is not None:
            bubble = self._stream_bubble
            self._stream_bubble = None
            self._show_bubble_error(
                bubble, _("Axon Brain service is offline or Ollama is not running."), offline=True
            )
        return False

    def _on_chunk(self, chunk: str, request_id: int) -> bool:
        if self._requests.is_current(request_id) and self._first_token_at is None:
            self._first_token_at = time.monotonic()
        if self._requests.is_current(request_id) and self._stream_bubble is not None:
            self._stream_bubble.append_text(chunk)
            self._scroll_to_bottom()
        return False

    def _on_stream_done(
        self,
        full_text: str,
        request_id: int,
        sources: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> bool:
        if not self._requests.finish(request_id):
            return False  # cancelled; the bubble already says so
        self._set_streaming(False)
        self._refresh_context()
        if self._stream_bubble is not None:
            bubble = self._stream_bubble
            self._stream_bubble = None
            body, error = split_error(full_text)
            action = parse_action(full_text) if error is None else None
            if action is not None:
                self._show_action_card(bubble, action)
            elif error is None:
                bubble.set_final_text(full_text)
            if error is None:
                now = time.monotonic()
                first = self._first_token_at
                meta = answer_meta(
                    self._client.model,
                    now - self._stream_started,
                    None if first is None else first - self._stream_started,
                )
                used = list(dict.fromkeys(str(s.get("name", "")) for s in steps or []))
                if used:
                    meta += " · " + _("used: {tools}").format(tools=", ".join(used))
                bubble.set_meta(meta)
                bubble.set_sources(sources or [])
                bubble.set_found_files(tool_sources(steps or []))
                self._offer_regenerate(bubble, self._stream_prompt)
                announce(self, announcement("answer", bubble.get_text()))
            else:
                self._show_bubble_error(
                    bubble, f"{body}\n\n{error}" if body else error, offline=False
                )
        return False

    def _offer_regenerate(self, bubble: MessageBubble, prompt: str) -> None:
        """Put "Regenerate" on the newest answer only."""
        old = getattr(self, "_regenerate_btn", None)
        if old is not None:
            old.set_visible(False)
        self._regenerate_btn = bubble.add_action(
            _("Regenerate"), lambda: self._retry(bubble, prompt)
        )
        self._regenerate_btn.set_tooltip_text(_("Ask again for a new answer"))

    def _retry(self, bubble: MessageBubble, prompt: str) -> None:
        if self._streaming or not prompt:
            return
        self._messages_box.remove(bubble)
        self._start_request(prompt)

    def _run_anyway(self, bubble: MessageBubble, prompt: str) -> None:
        """Let Brain answer on the CPU for a while (never the GPU), then ask again."""

        def allowed(ok: bool) -> None:
            if ok:
                self._retry(bubble, prompt)

        allow_cpu_only(allowed)
