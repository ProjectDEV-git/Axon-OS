"""brain_generation.py — D-Bus API for text generation and chat, plus the helpers that build prompts and run completions.

Mixed into ``BrainService`` (see ``brain_service.py``); methods rely on its state
(``self.store``, ``self.config``, ``self.providers`` ...) set up in ``_setup``.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
import uuid
from typing import Any

import dbus
import dbus.service
from brain_ollama import (
    _OLLAMA_LOAD_TIMEOUT,
    _ollama_error_message,
)
from brain_sanitize import _sanitize_context, _sanitize_output
from constants import OLLAMA_BASE_URL
from context_window import context_limit, fit_messages, summarize_dropped
from memory import MemoryStore, memory_prompt
from prompts import CHAT_SYSTEM_PROMPT
from providers import ProviderError
from service_utils import rate_limited

logger = logging.getLogger("axon-brain")


class GenerationMixin(dbus.service.Object):
    """D-Bus API for text generation and chat, plus the helpers that build prompts and run completions."""

    @dbus.service.method("org.axonos.Brain", in_signature="sssb", out_signature="s")
    @rate_limited(rate=100, window_seconds=60)
    def Generate(self, prompt, context, model, stream):
        """Unified text generation interface. If streaming, returns a transaction ID."""
        if not self._validate_prompt(str(prompt)):
            return json.dumps({"error": "invalid prompt (empty or too long)"})
        if model and not self._validate_model_name(str(model)):
            return json.dumps({"error": f"invalid model name: {model!r}"})
        if not model:
            model, _reason = self.router.select_model(str(prompt), str(context))
        model = self._local_name(str(model))
        model = self._game_mode_model(model)

        system_prompt = ""
        if context:
            system_prompt = (
                f"Here is the user's desktop context:\n\n{_sanitize_context(str(context))}"
            )

        if stream:
            tx_id = str(uuid.uuid4())
            # FIX 4: Register transaction for cancellation support
            cancel_flag = threading.Event()
            with self._streams_lock:
                self._active_streams[tx_id] = cancel_flag
            threading.Thread(
                target=self._do_generate_stream,
                args=(tx_id, prompt, system_prompt, model),
                daemon=True,
            ).start()
            return tx_id
        else:
            return self._do_generate_sync(prompt, system_prompt, model)

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="b")
    def CancelStream(self, transaction_id: str) -> bool:
        """Cancel an active streaming transaction.

        Returns True if the stream was found and cancelled, False if no
        matching transaction was active.
        """
        with self._streams_lock:
            cancel_flag = self._active_streams.get(transaction_id)
        if cancel_flag is not None:
            cancel_flag.set()
            self.logger.debug("Stream %s cancellation requested", transaction_id)
            return True
        return False

    @dbus.service.method("org.axonos.Brain", in_signature="ssssb", out_signature="s")
    @rate_limited(rate=100, window_seconds=60)
    def SendMessage(self, conversation_id, message, context, model, stream):
        """Persists user message and streams or blocks assistant reply with ambient context."""
        if model and not self._validate_model_name(str(model)):
            return json.dumps({"error": f"invalid model name: {model!r}"})
        self.store.add_message(conversation_id, "user", message)

        if not model:
            model, _reason = self.router.select_model(str(message), str(context))
        model = self._local_name(str(model))
        model = self._game_mode_model(model)

        if stream:
            tx_id = str(uuid.uuid4())
            # FIX 4: Register transaction for cancellation support
            cancel_flag = threading.Event()
            with self._streams_lock:
                self._active_streams[tx_id] = cancel_flag
            threading.Thread(
                target=self._do_chat_stream,
                args=(tx_id, conversation_id, context, model),
                daemon=True,
            ).start()
            return tx_id
        else:
            resp = self._do_chat_sync(conversation_id, context, model)
            self.store.add_message(conversation_id, "assistant", resp)
            return resp

    @dbus.service.method("org.axonos.Brain", in_signature="ssss", out_signature="s")
    @rate_limited(rate=60, window_seconds=60)
    def SendMessageAgent(self, conversation_id, message, context, model):
        """Like SendMessage, but the model may call tools (search, context,
        calculator, memory, desktop settings) before answering.

        Returns a transaction id. Progress arrives as ToolCall signals, the
        answer as TokenGenerated, and the end as GenerationCompleted.
        """
        if model and not self._validate_model_name(str(model)):
            return json.dumps({"error": f"invalid model name: {model!r}"})
        self.store.add_message(conversation_id, "user", message)
        if not model:
            model, _reason = self.router.select_model(str(message), str(context), needs_tools=True)
        model = self._game_mode_model(self._local_name(str(model)))
        tx_id = str(uuid.uuid4())
        with self._streams_lock:
            self._active_streams[tx_id] = threading.Event()
        threading.Thread(
            target=self._do_agent, args=(tx_id, conversation_id, context, model), daemon=True
        ).start()
        return tx_id

    @dbus.service.signal("org.axonos.Brain", signature="ss")
    def TokenGenerated(self, transaction_id, token):
        """Fires when a stream chunk is generated."""
        pass

    @dbus.service.signal("org.axonos.Brain", signature="ss")
    def ToolCall(self, transaction_id, step_json):
        """Fires after the agent runs a tool (name, arguments, result)."""
        pass

    @dbus.service.signal("org.axonos.Brain", signature="sbs")
    def GenerationCompleted(self, transaction_id, success, error_msg):
        """Fires when stream finishes."""
        pass

    def _complete(
        self,
        model: str,
        system: str,
        prompt: str,
        temperature: float | None = None,
        timeout: float = 60.0,
    ) -> str:
        """Return one non-streamed completion from any model. Raises on failure."""
        model = self._local_name(model)
        if self._is_cloud(model):
            msgs = [{"role": "system", "content": system}] if system else []
            msgs.append({"role": "user", "content": prompt})
            chunks = self.providers.chat(
                model, msgs, stream=False, temperature=temperature, timeout=timeout
            )
            return _sanitize_output("".join(chunks))
        model = self._resolve_local_model(model)
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["options"] = {"temperature": temperature}
        with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return _sanitize_output(data.get("response", ""))

    def _ollama_stream(self, path, payload, extract):
        """Yield tokens from an Ollama NDJSON streaming endpoint."""
        payload = {**payload, "model": self._resolve_local_model(payload["model"])}
        try:
            r = self._http_post(f"{OLLAMA_BASE_URL}{path}", payload, timeout=_OLLAMA_LOAD_TIMEOUT)
        except (urllib.error.URLError, OSError) as e:
            raise ProviderError(_ollama_error_message(e, payload["model"])) from None
        with r:
            # FIX 3: Set per-read timeout on the underlying socket
            self._set_stream_timeout(r)
            for raw_line in r:
                line = raw_line.decode().strip()
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise ProviderError(f"Ollama error: {chunk['error']}")
                token = extract(chunk)
                if token:
                    yield token

    def _do_generate_sync(self, prompt, system, model):
        try:
            return self._complete(model, system, prompt, timeout=_OLLAMA_LOAD_TIMEOUT)
        except ProviderError as e:
            return f"[Error: {e}]"
        except (urllib.error.URLError, OSError) as e:
            return f"[Error: {_ollama_error_message(e, model)}]"
        except Exception as e:
            logger.debug("Generate failed: %s", e)
            return "[Error: AI generation unavailable]"

    def _do_generate_stream(self, tx_id, prompt, system, model):
        if self._is_cloud(model):
            msgs = [{"role": "system", "content": system}] if system else []
            msgs.append({"role": "user", "content": prompt})
            tokens = self.providers.chat(model, msgs, stream=True)
        else:
            payload = {"model": model, "prompt": prompt, "stream": True}
            if system:
                payload["system"] = system
            tokens = self._ollama_stream(
                "/api/generate", payload, lambda chunk: chunk.get("response", "")
            )
        self._run_stream(tx_id, tokens, "Generation")

    def _chat_messages(self, conv_id, context, model="", extra_system=""):
        """Build the /api/chat message list, system prompt first.

        Ollama's chat endpoint has no top-level "system" field (it is silently
        ignored), so the safety rules and context must be a system message.
        Stored messages with role "system" are dropped: only the service
        decides what the system prompt says. Remembered facts relevant to the
        latest user message are added, and old turns are trimmed to fit the
        model's context window.
        """
        system_prompt = CHAT_SYSTEM_PROMPT
        conv_prompt = self.store.get_system_prompt(conv_id)
        if conv_prompt:
            system_prompt += (
                f"\n\nConversation instructions:\n{_sanitize_context(str(conv_prompt))}"
            )
        model = str(model or "")
        # Auto context stays on the machine: cloud models only get context the
        # caller passed explicitly (the AI panel warns before sending it).
        if not context and model and not self._is_cloud(model):
            context = self._auto_context()
        if context:
            system_prompt += f"\n\nHere is the user's current desktop context:\n{_sanitize_context(str(context))}"
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in self.store.get_messages(conv_id)
            if m["role"] in ("user", "assistant")
        ]
        last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        facts = self._recall(last_user)
        if facts:
            system_prompt += "\n\n" + memory_prompt(facts)
        if extra_system:
            system_prompt += "\n\n" + extra_system
        msgs = [{"role": "system", "content": system_prompt}, *history]
        budget = context_limit(model, cloud=self._is_cloud(model) if model else False)
        cfg = getattr(self, "config", None) or {}
        budget = min(budget, int(cfg.get("max_context_tokens", 32768)))
        kept, dropped = fit_messages(msgs, budget)
        note = summarize_dropped(dropped)
        if note:
            kept[0] = {"role": "system", "content": kept[0]["content"] + "\n\n" + note}
        return kept

    def _recall(self, query: str) -> list[str]:
        cfg = getattr(self, "config", None) or {}
        memory: MemoryStore | None = getattr(self, "memory", None)
        if not query or memory is None or not cfg.get("memory_enabled", True):
            return []
        try:
            return memory.recall(query, k=5)
        except Exception as e:
            logger.debug("Memory recall failed: %s", e)
            return []

    def _embed_text(self, text: str) -> list[float] | None:
        """Embedding vector for *text*, or None when no embedding model is available."""
        try:
            data = json.loads(self.GetEmbeddings(text, ""))
        except Exception:
            return None
        if isinstance(data, list) and data and all(isinstance(x, (int, float)) for x in data):
            return [float(x) for x in data]
        return None

    def _auto_context(self) -> str:
        """Desktop context from the Context service, when the user allows it."""
        cfg = getattr(self, "config", None)
        if not cfg or not cfg.get("auto_context", True):
            return ""
        try:
            bus = dbus.SessionBus()
            obj = bus.get_object("org.axonos.Context", "/org/axonos/Context")
            iface = dbus.Interface(obj, "org.axonos.Context")
            return str(iface.GetContextForPrompt(dbus.Int32(1500), timeout=2))
        except Exception as e:
            logger.debug("Auto context unavailable: %s", e)
            return ""

    def _complete_messages(self, model: str, msgs: list[dict[str, str]]) -> str:
        """One non-streamed chat completion for a full message list."""
        if self._is_cloud(model):
            return _sanitize_output("".join(self.providers.chat(model, msgs, stream=False)))
        payload = {"model": self._resolve_local_model(model), "messages": msgs, "stream": False}
        with self._http_post(
            f"{OLLAMA_BASE_URL}/api/chat", payload, timeout=_OLLAMA_LOAD_TIMEOUT
        ) as resp:
            data = json.loads(resp.read().decode())
            return _sanitize_output(data.get("message", {}).get("content", ""))

    def _do_chat_sync(self, conv_id, context, model):
        api_msgs = self._chat_messages(conv_id, context, model)
        try:
            if self._is_cloud(model):
                return _sanitize_output("".join(self.providers.chat(model, api_msgs, stream=False)))
            model = self._resolve_local_model(model)
            payload = {
                "model": model,
                "messages": api_msgs,
                "stream": False,
            }
            with self._http_post(
                f"{OLLAMA_BASE_URL}/api/chat", payload, timeout=_OLLAMA_LOAD_TIMEOUT
            ) as resp:
                data = json.loads(resp.read().decode())
                return _sanitize_output(data.get("message", {}).get("content", ""))
        except ProviderError as e:
            return f"[Error: {e}]"
        except (urllib.error.URLError, OSError) as e:
            return f"[Error: {_ollama_error_message(e, model)}]"
        except Exception as e:
            logger.debug("Chat failed: %s", e)
            return "[Error: AI chat unavailable]"

    def _do_chat_stream(self, tx_id, conv_id, context, model):
        api_msgs = self._chat_messages(conv_id, context, model)
        if self._is_cloud(model):
            tokens = self.providers.chat(model, api_msgs, stream=True)
        else:
            payload = {"model": model, "messages": api_msgs, "stream": True}
            tokens = self._ollama_stream(
                "/api/chat", payload, lambda chunk: chunk.get("message", {}).get("content", "")
            )
        self._run_stream(
            tx_id,
            tokens,
            "Chat",
            on_done=lambda text: self.store.add_message(conv_id, "assistant", text),
        )
