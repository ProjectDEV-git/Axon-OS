#!/usr/bin/env python3
"""Axon Brain D-Bus Service - Centralized AI inference and model management."""

import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# services/ must be importable before any local module: systemd runs this file
# as a script, so only its own directory is on sys.path.
_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import dbus
import dbus.mainloop.glib
import dbus.service
from _log_helper import resolve_logger as configure_app_logger
from gi.repository import GLib
from service_base import ServiceBase

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

logger = configure_app_logger("axon-brain")

# Ensure we can import hardware_profiler and conversation_store
_from_services = str(Path(__file__).resolve().parent)
if _from_services not in sys.path:
    sys.path.insert(0, _from_services)

import agent_actions
import hardware_profiler
import oauth
from agent_loop import run_agent
from ai_router import AIRouter
from brain_classify import ClassifyMixin
from brain_config import MODEL_TIERS
from brain_conversations import ConversationsMixin
from brain_gamemode import GameModeMixin
from brain_generation import GenerationMixin
from brain_http import OllamaHttpMixin
from brain_ollama import (
    _INSTALLED_MODELS_TTL,
    _OLLAMA_LOAD_TIMEOUT,
    _STREAM_READ_TIMEOUT,
    _is_retryable,
    _ollama_error_message,
    _same_model,
)
from brain_providers import ProvidersMixin
from brain_sanitize import (
    _ANSI_RE,
    _INJECTION_PATTERNS,
    _MAX_CONTEXT_LEN,
    _require_http_url,
    _sanitize_context,
    _sanitize_output,
)
from brain_tokens import TokenBuffer
from constants import (
    AXON_DIR,
    MAX_MODEL_NAME_LEN,
    MAX_PROMPT_LEN,
    OLLAMA_BASE_URL,
)
from conversation_store import ConversationStore
from credentials import CredentialStore
from game_gate import GameGate
from memory import MemoryStore
from prompts import CHAT_SYSTEM_PROMPT
from providers import (
    ProviderError,
    ProviderRegistry,
    parse_model_ref,
)
from tools import TOOL_PROTOCOL_NOTE

__all__ = [
    "CHAT_SYSTEM_PROMPT",
    "_ANSI_RE",
    "_INJECTION_PATTERNS",
    "_INSTALLED_MODELS_TTL",
    "_MAX_CONTEXT_LEN",
    "_OLLAMA_LOAD_TIMEOUT",
    "_STREAM_READ_TIMEOUT",
    "BrainService",
    "TokenBuffer",
    "_is_retryable",
    "_ollama_error_message",
    "_require_http_url",
    "_same_model",
    "_sanitize_context",
    "_sanitize_output",
]

CONFIG_FILE = AXON_DIR / "config.toml"
# Written by the graphical installer; holds the model the user picked there.
INSTALLER_CONFIG_FILE = Path.home() / ".axon" / "config.toml"
PROVIDERS_FILE = AXON_DIR / "providers.json"


# Local Ollama is never reached through an HTTP proxy: a desktop proxy setting
# (http_proxy without a matching no_proxy) would otherwise swallow every call.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_installer_model() -> str:
    """Return the model chosen in the installer (``[ai].default_model``), or ``""``."""
    try:
        with open(INSTALLER_CONFIG_FILE, "rb") as f:
            model = tomllib.load(f).get("ai", {}).get("default_model", "")
    except (OSError, ValueError):
        return ""
    return model if isinstance(model, str) else ""


class BrainService(
    GameModeMixin,
    ProvidersMixin,
    ConversationsMixin,
    ClassifyMixin,
    GenerationMixin,
    OllamaHttpMixin,
    ServiceBase,
):
    BUS_NAME = "org.axonos.Brain"

    OBJECT_PATH = "/org/axonos/Brain"

    SERVICE_NAME = "axon-brain"

    def _setup(self):
        # Initialize sub-components
        self._config_lock = threading.RLock()
        self.store = ConversationStore()
        self.load_config()
        self.router = AIRouter(self.config)
        self.providers = ProviderRegistry(PROVIDERS_FILE, CredentialStore(), OLLAMA_BASE_URL)
        self._oauth_lock = threading.Lock()
        self._oauth_sessions: dict[str, dict[str, Any]] = {}
        # FIX 4: Transaction registry for stream cancellation
        self._streams_lock = threading.Lock()
        self._active_streams: dict[str, threading.Event] = {}
        # FIX 5: Token buffer for backpressure on signal emission
        # Use GLib.idle_add to safely emit signals from worker threads
        self._token_buffer = TokenBuffer(
            emit_fn=lambda tx_id, tok: GLib.idle_add(self.TokenGenerated, tx_id, tok),
            flush_interval=0.1,
            max_tokens=10,
        )
        # Game mode: Brain always starts awake. The Gaming service puts it back to
        # sleep when it sees Brain (re)appear during a game.
        self._gate = GameGate()
        # Long-term memory and agent tools
        self.memory = MemoryStore(AXON_DIR / "memory.db", embed=self._embed_text)
        self.tools = agent_actions.build_registry(self)
        threading.Thread(target=self._warm_up_ollama, daemon=True).start()

    def _warm_up_ollama(self, wait_seconds: float = 180.0) -> None:
        """Load the speed model into Ollama once it is up.

        Intent and window classification give a model only 5-10 seconds, far
        less than a cold load takes, so without this the first requests after
        boot always fail. A generate request with no prompt only loads the model.
        """
        deadline = time.monotonic() + wait_seconds
        while self._installed_models() is None:
            if time.monotonic() > deadline:
                logger.info("Ollama not reachable at %s; skipping warm-up", OLLAMA_BASE_URL)
                return
            time.sleep(5)
        with self._config_lock:
            speed = str(self.config.get("speed_model", ""))
        if not speed or self._is_cloud(speed):
            return
        if self._gate.suspended:
            logger.info("Game mode is on; skipping model warm-up")
            return
        model = self._resolve_local_model(self._local_name(speed))
        try:
            with self._http_post(
                f"{OLLAMA_BASE_URL}/api/generate",
                {"model": model, "stream": False},
                timeout=_OLLAMA_LOAD_TIMEOUT,
                max_retries=1,
            ) as r:
                r.read()
            logger.info("Loaded %s into Ollama", model)
        except (urllib.error.URLError, OSError) as e:
            logger.info("Warm-up of %s failed: %s", model, _ollama_error_message(e, model))

    def _cleanup(self):
        """Close DB connection pool on shutdown."""
        self.store.close_all()

    def save_config(self):
        """Saves current configuration to TOML format atomically."""
        with self._config_lock:
            try:
                content = "# Axon OS AI Configuration\n\n"
                for k, v in self.config.items():
                    if isinstance(v, bool):
                        content += f"{k} = {'true' if v else 'false'}\n"
                    elif isinstance(v, (int, float)):
                        content += f"{k} = {v}\n"
                    else:
                        escaped_v = str(v).replace("\\", "\\\\").replace('"', '\\"')
                        content += f'{k} = "{escaped_v}"\n'
                tmp_path = CONFIG_FILE.with_suffix(".tmp")
                tmp_path.write_text(content)
                tmp_path.replace(CONFIG_FILE)
            except Exception as e:
                logger.exception("Error saving config to %s: %s", CONFIG_FILE, e)

    def load_config(self):
        """Loads model config, profiles hardware if not present."""
        AXON_DIR.mkdir(parents=True, exist_ok=True)
        with self._config_lock:
            if CONFIG_FILE.exists():
                try:
                    with open(CONFIG_FILE, "rb") as f:
                        self.config = tomllib.load(f)
                    # Verify required keys exist
                    if all(
                        k in self.config for k in ("speed_model", "general_model", "deep_model")
                    ):
                        return
                except Exception as e:
                    logger.debug("Config file not loaded, using defaults: %s", e)
                    # Back up corrupted config before replacing
                    try:
                        backup_path = CONFIG_FILE.with_suffix(".toml.bak")
                        shutil.copy2(CONFIG_FILE, backup_path)
                        logger.info("Corrupted config backed up to %s", backup_path)
                    except OSError as backup_err:
                        logger.warning("Could not back up corrupted config: %s", backup_err)

            # The model picked in the installer is the one that was downloaded,
            # so use it for every tier rather than models that may be missing.
            installer_model = _read_installer_model()
            if installer_model and self._validate_model_name(installer_model):
                self.config = dict.fromkeys(MODEL_TIERS, installer_model)
                self.save_config()
                return

            # Profile hardware and save default config
            try:
                profile = hardware_profiler.profile_hardware()
                self.config = {
                    "speed_model": profile["recommendations"]["speed"]["model"],
                    "general_model": profile["recommendations"]["general"]["model"],
                    "deep_model": profile["recommendations"]["deep"]["model"],
                }
            except Exception as e:
                logger.warning("Hardware profiling failed, using fallback defaults: %s", e)
                self.config = {
                    "speed_model": "qwen2.5:1.5b",
                    "general_model": "qwen2.5:7b",
                    "deep_model": "qwen2.5:14b",
                }
            self.save_config()

    def _game_mode_model(self, model):
        """The model to use for a request: a cloud one if game mode asks for it.

        Local models stay as they are otherwise; ``_http_post`` then refuses (or
        moves to CPU) the request. Never loads anything onto the GPU.
        """
        gate = getattr(self, "_gate", None)
        if gate is None or not gate.suspended or not gate.use_cloud or self._is_cloud(model):
            return model
        with self._config_lock:
            candidates = [str(self.config.get("game_mode_cloud_model", ""))] + [
                str(self.config.get(k, "")) for k in MODEL_TIERS
            ]
        for cand in candidates:
            if cand and self._validate_model_name(cand) and self._is_cloud(cand):
                return cand
        return model

    @staticmethod
    def _open_with_retry(req, timeout, max_retries):
        max_backoff = 30.0
        for attempt in range(max_retries):
            try:
                return _LOCAL_OPENER.open(req, timeout=timeout)  # nosec B310 - http(s) only
            except (urllib.error.URLError, OSError) as e:
                if attempt == max_retries - 1 or not _is_retryable(e):
                    raise
                time.sleep(min(2.0**attempt, max_backoff))
        raise RuntimeError("unreachable")  # pragma: no cover

    def _resolve_local_model(self, model: str) -> str:
        """Return *model* if it is installed, else the best installed substitute.

        The tier config can name models that were never pulled (the hardware
        profile recommends several, the installer downloads one). Instead of
        failing every request with "model not found", fall back to the
        installer's choice, another configured tier, or any installed chat
        model. If Ollama is unreachable or nothing fits, *model* is returned
        unchanged so the caller reports the real error.
        """
        installed = self._installed_models()
        if not installed or any(_same_model(model, m) for m in installed):
            return model
        config = getattr(self, "config", {}) or {}
        candidates = [_read_installer_model()] + [str(config.get(k, "")) for k in MODEL_TIERS]
        chat_models = [m for m in installed if "embed" not in m.lower()]
        for cand in candidates:
            match = next((m for m in chat_models if cand and _same_model(cand, m)), None)
            if match:
                break
        else:
            match = chat_models[0] if chat_models else None
        if match is None:
            return model
        logger.warning("Model %s is not installed; using %s instead", model, match)
        return match

    _MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")

    _MAX_MODEL_NAME_LEN = MAX_MODEL_NAME_LEN

    _MAX_PROMPT_LEN = MAX_PROMPT_LEN

    @staticmethod
    def _validate_model_name(name):
        """True if name is a safe model reference (no shell/path injection).

        Accepts Ollama tags and ``@provider/model`` references to cloud models.
        """
        if not isinstance(name, str) or not name:
            return False
        if len(name) > BrainService._MAX_MODEL_NAME_LEN or ".." in name:
            return False
        try:
            _provider, model = parse_model_ref(name)
        except ValueError:
            return False
        return bool(BrainService._MODEL_NAME_RE.match(model))

    @staticmethod
    def _validate_prompt(prompt):
        """True if prompt is a non-empty string within the length limit."""
        if not isinstance(prompt, str) or not prompt:
            return False
        return len(prompt) <= BrainService._MAX_PROMPT_LEN

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def SetModel(self, tier, model):
        """Choose the model for a routing tier: speed, general, deep, or all.

        *model* is any reference from ListAllModels, e.g. ``qwen2.5:7b`` or
        ``@anthropic/claude-sonnet-4-5``.
        """
        tier = str(tier)
        model = str(model)
        keys = list(MODEL_TIERS) if tier == "all" else [f"{tier}_model"]
        if any(k not in MODEL_TIERS for k in keys):
            return self._json_error(f"unknown tier {tier!r}")
        if not self._validate_model_name(model):
            return self._json_error(f"invalid model name: {model!r}")
        model = self._local_name(model)
        try:
            self.providers.get(parse_model_ref(model)[0])
        except ProviderError as e:
            return self._json_error(str(e))
        with self._config_lock:
            for k in keys:
                self.config[k] = model
            self.save_config()
            self.router = AIRouter(self.config)
        return json.dumps({"ok": True, "models": {k: self.config[k] for k in MODEL_TIERS}})

    def _do_sign_in(self, provider_id: str, session: dict[str, Any]) -> None:
        cfg = self.providers.get(provider_id)
        assert cfg.oauth is not None

        def publish(state: str, info: dict[str, Any]) -> None:
            with self._oauth_lock:
                session["state"] = state
                session["info"] = info
            GLib.idle_add(self.SignInStatus, provider_id, state, json.dumps(info))

        try:
            tokens = oauth.run_flow(
                cfg.oauth, lambda info: publish("awaiting_user", info), session["cancel"]
            )
            self.providers.store_tokens(provider_id, tokens)
            publish("done", {})
        except Exception as e:
            logger.info("Sign-in to %s failed: %s", provider_id, e)
            publish("error", {"error": str(e)})

    def _do_pull_model(self, model_name):
        try:
            payload = {"name": model_name}
            with self._http_post(f"{OLLAMA_BASE_URL}/api/pull", payload) as r:
                for raw_line in r:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    status = data.get("status", "")
                    completed = data.get("completed", 0)
                    total = data.get("total", 0)
                    GLib.idle_add(self.PullProgress, model_name, completed, total, status)
        except Exception as e:
            logger.debug("Pull failed for %s: %s", model_name, e)
            GLib.idle_add(self.PullProgress, model_name, 0, 0, "Pull failed")

    def _run_stream(self, tx_id, tokens, label, on_done=None):
        """Pump *tokens* to TokenGenerated signals, honouring cancellation.

        Args:
            tx_id: Transaction id registered in ``_active_streams``.
            tokens: Iterator of raw text chunks from Ollama or a provider.
            label: "Generate" or "Chat", for logs and error messages.
            on_done: Called with the full text after a successful stream.
        """
        with self._streams_lock:
            cancel_flag = self._active_streams.get(tx_id)
        accumulated = ""
        try:
            try:
                for token in tokens:
                    # FIX 4: Check cancellation before processing
                    if cancel_flag is not None and cancel_flag.is_set():
                        logger.debug("%s stream %s cancelled by client", label, tx_id)
                        break
                    token = _sanitize_output(token)
                    accumulated += token
                    # FIX 5: Use token buffer for backpressure
                    self._token_buffer.add(token, tx_id)
            finally:
                # closes the HTTP response when a generator is abandoned mid-stream
                close = getattr(tokens, "close", None)
                if close is not None:
                    close()
            # Flush any remaining buffered tokens
            self._token_buffer.flush()
            if on_done is not None:
                on_done(accumulated)
            GLib.idle_add(self.GenerationCompleted, tx_id, True, "")
        except TimeoutError:
            logger.warning("%s stream %s timed out (model hung)", label, tx_id)
            self._token_buffer.flush()
            GLib.idle_add(self.GenerationCompleted, tx_id, False, f"{label} timed out")
        except ProviderError as e:
            logger.info("%s stream %s failed: %s", label, tx_id, e)
            self._token_buffer.flush()
            GLib.idle_add(self.GenerationCompleted, tx_id, False, str(e))
        except Exception as e:
            logger.debug("%s stream failed: %s", label, e)
            self._token_buffer.flush()
            GLib.idle_add(self.GenerationCompleted, tx_id, False, f"{label} failed")
        finally:
            with self._streams_lock:
                self._active_streams.pop(tx_id, None)

    def _do_agent(self, tx_id, conv_id, context, model):
        """Run the tool loop, then emit the answer as stream tokens."""
        extra = self.tools.describe_for_prompt() + "\n" + TOOL_PROTOCOL_NOTE
        api_msgs = self._chat_messages(conv_id, context, model, extra_system=extra)
        with self._streams_lock:
            cancel_flag = self._active_streams.get(tx_id)

        def on_tool(step):
            GLib.idle_add(self.ToolCall, tx_id, json.dumps(step)[:4000])

        try:
            result = run_agent(
                lambda m: self._complete_messages(model, m),
                api_msgs,
                self.tools,
                cancel=cancel_flag,
                on_tool=on_tool,
                approve=agent_actions.approve,
            )
        except ProviderError as e:
            self._fail_stream(tx_id, str(e))
            return
        except (urllib.error.URLError, OSError) as e:
            self._fail_stream(tx_id, _ollama_error_message(e, model))
            return
        except Exception as e:
            logger.debug("Agent failed: %s", e)
            self._fail_stream(tx_id, "Agent failed")
            return
        if result.cancelled:
            self._fail_stream(tx_id, "cancelled")
            return
        words = re.findall(r"\S+\s*", result.answer) or [result.answer]
        self._run_stream(
            tx_id,
            iter(words),
            "Agent",
            on_done=lambda text: self.store.add_message(conv_id, "assistant", text),
        )

    def _fail_stream(self, tx_id: str, message: str) -> None:
        with self._streams_lock:
            self._active_streams.pop(tx_id, None)
        GLib.idle_add(self.GenerationCompleted, tx_id, False, message)


if __name__ == "__main__":
    BrainService.main()
