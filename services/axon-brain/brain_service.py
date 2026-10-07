#!/usr/bin/env python3
"""Axon Brain D-Bus Service - Centralized AI inference and model management."""

import json
import re
import shutil
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
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

import hardware_profiler
import oauth
from ai_router import AIRouter
from constants import (
    AXON_DIR,
    MAX_MODEL_NAME_LEN,
    MAX_PROMPT_LEN,
    OLLAMA_BASE_URL,
)
from conversation_store import ConversationStore
from credentials import CredentialError, CredentialStore
from prompts import CHAT_SYSTEM_PROMPT
from providers import (
    OLLAMA_ID,
    ProviderError,
    ProviderRegistry,
    parse_model_ref,
)
from service_utils import rate_limited

CONFIG_FILE = AXON_DIR / "config.toml"
PROVIDERS_FILE = AXON_DIR / "providers.json"
MODEL_TIERS = ("speed_model", "general_model", "deep_model")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MAX_CONTEXT_LEN = 500

# Patterns commonly used in prompt injection attacks.
_INJECTION_PATTERNS = re.compile(
    r"(ignore previous|ignore all previous|you are now|system:|"
    r"assistant:|IMPORTANT:|disregard.*instructions|new instructions|"
    r"override.*system|forget everything)",
    re.IGNORECASE,
)

def _require_http_url(url: str) -> None:
    """Reject non-HTTP(S) URLs; urllib would also open file:// and custom schemes."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing non-HTTP URL: {url!r}")


def _sanitize_output(text: str) -> str:
    """Strip ANSI escape sequences and null bytes from AI output."""
    text = _ANSI_RE.sub("", text)
    text = text.replace("\x00", "")
    return text


def _sanitize_context(context: str) -> str:
    """Sanitize and wrap context before embedding in system prompt.

    Strips null bytes, normalizes Unicode to NFKD (to catch homoglyph
    bypasses), removes common prompt-injection patterns, truncates to
    _MAX_CONTEXT_LEN chars, and wraps in untrusted tags so the model
    treats the content as inert data.
    """
    safe = context.replace("\x00", "")
    safe = unicodedata.normalize("NFKD", safe)
    safe = _INJECTION_PATTERNS.sub("", safe)
    if len(safe) > _MAX_CONTEXT_LEN:
        safe = safe[:_MAX_CONTEXT_LEN]
    return f"<untrusted_context>{safe}</untrusted_context>"


class TokenBuffer:
    """Batch token signals to prevent D-Bus signal flooding.

    Accumulates tokens and flushes them either when the buffer is full
    or when the flush interval has elapsed, whichever comes first.
    """

    def __init__(
        self,
        emit_fn: Callable[[str, str], object],
        flush_interval: float = 0.1,
        max_tokens: int = 10,
    ) -> None:
        self._buffer: list[tuple[str, str]] = []  # (transaction_id, token)
        self._last_flush = time.monotonic()
        self._flush_interval = flush_interval
        self._max_tokens = max_tokens
        self._emit_fn = emit_fn
        self._lock = threading.Lock()

    def add(self, token: str, transaction_id: str) -> None:
        """Add a token to the buffer, flushing if thresholds are exceeded."""
        with self._lock:
            self._buffer.append((transaction_id, token))
            now = time.monotonic()
            if (
                len(self._buffer) >= self._max_tokens
                or now - self._last_flush >= self._flush_interval
            ):
                self._flush()

    def flush(self) -> None:
        """Force-flush any buffered tokens."""
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        """Emit all buffered tokens. Caller must hold _lock."""
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        self._last_flush = time.monotonic()
        for tx_id, tok in batch:
            self._emit_fn(tx_id, tok)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)


# Per-read timeout for Ollama streaming (seconds).
# If no data arrives within this window, the stream is considered hung.
_STREAM_READ_TIMEOUT = 30.0


class BrainService(ServiceBase):
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

    def _http_post(self, url, payload, stream=False, timeout=60.0, max_retries=5):
        """Helper to execute urllib POST requests with retry logic."""
        _require_http_url(url)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        max_backoff = 30.0
        for attempt in range(max_retries):
            try:
                return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - http(s) only
            except (urllib.error.URLError, OSError):
                if attempt == max_retries - 1:
                    raise
                backoff = min(2.0**attempt, max_backoff)
                time.sleep(backoff)

    def _http_get(self, url, timeout=5.0):
        """Helper to execute urllib GET requests with retry logic."""
        _require_http_url(url)
        req = urllib.request.Request(url)
        max_retries = 5
        max_backoff = 30.0
        for attempt in range(max_retries):
            try:
                return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - http(s) only
            except (urllib.error.URLError, OSError):
                if attempt == max_retries - 1:
                    raise
                backoff = min(2.0**attempt, max_backoff)
                time.sleep(backoff)

    # ------------------------------------------------------------------
    # Input validation (defence against injection / abuse)
    # ------------------------------------------------------------------

    # Ollama tags: alnum start, then alnum plus . _ : / - (namespaced models
    # like "library/llama3" are allowed; ".." path traversal is not).
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
    def _is_cloud(model: str) -> bool:
        """True if *model* is served by a configured provider rather than Ollama."""
        return parse_model_ref(model)[0] != OLLAMA_ID

    @staticmethod
    def _local_name(model: str) -> str:
        """``@ollama/x`` and ``x`` both name the local Ollama model ``x``."""
        provider, name = parse_model_ref(model)
        return name if provider == OLLAMA_ID else model

    @staticmethod
    def _validate_prompt(prompt):
        """True if prompt is a non-empty string within the length limit."""
        if not isinstance(prompt, str) or not prompt:
            return False
        return len(prompt) <= BrainService._MAX_PROMPT_LEN

    @staticmethod
    def _set_stream_timeout(response: Any, timeout: float = _STREAM_READ_TIMEOUT) -> None:
        """Set a per-read socket timeout on an HTTP response for streaming.

        Prevents the daemon thread from blocking forever if Ollama hangs
        mid-stream. Raises ``TimeoutError`` on the next read if no data
        arrives within *timeout* seconds.
        """
        try:
            fp = response.raw._fp
            if fp is not None and hasattr(fp, "sock") and fp.sock is not None:
                fp.sock.settimeout(timeout)
        except (AttributeError, OSError):
            pass  # Socket not yet connected or already closed

    # ------------------------------------------------------------------
    # D-Bus Methods
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def GetStatus(self):
        """Returns JSON about Ollama and model config status."""
        status = {"ollama_online": False, "active_models": [], "configured_models": self.config}
        try:
            with self._http_get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0) as resp:
                if resp.status == 200:
                    status["ollama_online"] = True
                    data = json.loads(resp.read().decode())
                    status["active_models"] = [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.debug("Could not query Ollama status: %s", e)
        return json.dumps(status)

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def ListModels(self):
        """Returns local pulled models as a JSON array."""
        try:
            with self._http_get(f"{OLLAMA_BASE_URL}/api/tags") as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    return json.dumps(data.get("models", []))
        except Exception as e:
            return json.dumps({"error": str(e)})
        return "[]"

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="b")
    @rate_limited(rate=10, window_seconds=60)
    def PullModel(self, model_name):
        """Starts model pull in a background thread."""
        if not self._validate_model_name(str(model_name)):
            logger.warning("Rejected PullModel for invalid name: %r", model_name)
            return False
        threading.Thread(target=self._do_pull_model, args=(str(model_name),), daemon=True).start()
        return True

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

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def CreateConversation(self, system_prompt, title):
        conv_id = self.store.create_conversation(
            system_prompt=system_prompt if system_prompt else None, title=title if title else None
        )
        return conv_id

    @dbus.service.method("org.axonos.Brain", in_signature="sss", out_signature="b")
    def AddMessage(self, conversation_id, role, content):
        # "system" is not accepted: any session client could otherwise plant
        # persistent instructions in a conversation
        if role not in ("user", "assistant"):
            role = "user"
        self.store.add_message(conversation_id, role, content)
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    def GetMessages(self, conversation_id):
        messages = self.store.get_messages(conversation_id)
        return json.dumps(messages)

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def ListConversations(self):
        conversations = self.store.list_conversations()
        return json.dumps(conversations)

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="b")
    def DeleteConversation(self, conversation_id):
        self.store.delete_conversation(conversation_id)
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="b")
    def UpdateTitle(self, conversation_id, title):
        self.store.update_title(conversation_id, title)
        return True

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

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def ClassifyWindow(self, title, wm_class):
        """Classifies a newly opened window title/class into one of the 9 spaces."""
        model = self.router.get_model_for_classify_window()
        prompt = f"App: {_sanitize_context(wm_class)}\nWindow Title: {_sanitize_context(title)}"
        system_prompt = (
            "You are a workspace routing assistant for Axon OS. Classify this window into one of these 9 workspace spaces:\n"
            "Code, Web, Chat, Files, Media, Work, Personal, Terminal, Notes.\n"
            "Respond with ONLY the exact space name (one word, capitalised, e.g., 'Code' or 'Web'). No other text or markdown."
        )
        try:
            raw = self._complete(model, system_prompt, prompt, temperature=0.1, timeout=5.0)
            result = raw.strip().replace('"', "").replace("'", "")
            valid_spaces = [
                "Code",
                "Web",
                "Chat",
                "Files",
                "Media",
                "Work",
                "Personal",
                "Terminal",
                "Notes",
            ]
            for s in valid_spaces:
                if s.lower() in result.lower():
                    return s
        except Exception as e:
            logger.debug("Window classification failed: %s", e)
        return "Default"

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    @rate_limited(rate=100, window_seconds=60)
    def ClassifyIntent(self, text):
        """Spotlight-style classification of workspace intents using the Speed model."""
        model = self.router.get_model_for_intent()
        system_prompt = (
            "You are a command classifier for Axon OS. Classify the user query into one of these types:\n"
            "1. Run command: {'action': 'run_command', 'command': '<shell command>'}\n"
            "2. Open application: {'action': 'open_app', 'app': '<executable>'}\n"
            "3. Default answer: Just respond in plain text.\n"
            "Respond ONLY with valid JSON if action, otherwise plain text. Keep it brief."
        )
        try:
            result = self._complete(model, system_prompt, text, temperature=0.1, timeout=10.0)
        except Exception as e:
            logger.debug("ClassifyIntent failed: %s", e)
            return '{"action": "error", "message": "AI classification unavailable"}'
        result = result.strip()
        if result.startswith("{"):
            try:
                parsed = json.loads(result)
                if (
                    isinstance(parsed, dict)
                    and parsed.get("action") == "run_command"
                    and isinstance(parsed.get("command"), str)
                ):
                    return json.dumps({"action": "run_command", "command": parsed["command"]})
                if (
                    isinstance(parsed, dict)
                    and parsed.get("action") == "open_app"
                    and isinstance(parsed.get("app"), str)
                ):
                    return json.dumps({"action": "open_app", "app": parsed["app"]})
            except json.JSONDecodeError:
                pass
            return text
        return result

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def GetEmbeddings(self, prompt, model):
        """Generates embedding vector for a given prompt using Ollama."""
        if not model:
            model = self.config.get("embedding_model", "nomic-embed-text")
        try:
            # Try newer /api/embed endpoint first
            payload = {"model": model, "input": prompt}
            try:
                # Keep retries low: a blocking D-Bus call times out after ~25s,
                # and the /api/embeddings fallback below still needs to run.
                with self._http_post(
                    f"{OLLAMA_BASE_URL}/api/embed", payload, timeout=15.0, max_retries=2
                ) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        embeddings = data.get("embeddings", [])
                        if embeddings:
                            return json.dumps(embeddings[0])
            except Exception as e:
                logger.debug("Embeddings endpoint failed, trying fallback: %s", e)

            # Fallback to /api/embeddings
            payload = {"model": model, "prompt": prompt}
            with self._http_post(
                f"{OLLAMA_BASE_URL}/api/embeddings", payload, timeout=15.0, max_retries=2
            ) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    return json.dumps(data.get("embedding", []))
        except Exception as e:
            return json.dumps({"error": str(e)})
        return "[]"

    # ------------------------------------------------------------------
    # Providers: any model, API keys and OAuth sign-in
    # ------------------------------------------------------------------

    @staticmethod
    def _json_error(message: str) -> str:
        return json.dumps({"ok": False, "error": message})

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def ListProviders(self):
        """Providers with sign-in state. Never includes keys or tokens."""
        return json.dumps(
            {
                "providers": self.providers.describe(),
                "keyring_available": self.providers.keyring_available,
            }
        )

    @dbus.service.method("org.axonos.Brain", in_signature="b", out_signature="s")
    def ListAllModels(self, refresh):
        """Every usable model across local and cloud providers.

        Returns ``{"models": [{"id", "name", "provider", "provider_label"}],
        "errors": {provider: message}}``; pass ``id`` as the model argument
        of Generate or SendMessage.
        """
        return json.dumps(self.providers.list_models(refresh=bool(refresh)))

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    @rate_limited(rate=20, window_seconds=60)
    def SaveProvider(self, provider_json):
        """Add or update a provider from JSON (see providers.ProviderConfig)."""
        try:
            data = json.loads(str(provider_json))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            cfg = self.providers.upsert(data)
        except (ValueError, KeyError, TypeError, ProviderError) as e:
            return self._json_error(str(e))
        return json.dumps({"ok": True, "id": cfg.id})

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    def RemoveProvider(self, provider_id):
        """Delete a custom provider or reset a built-in one, forgetting its credentials."""
        try:
            self.providers.remove(str(provider_id))
        except ProviderError as e:
            return self._json_error(str(e))
        return json.dumps({"ok": True})

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    @rate_limited(rate=20, window_seconds=60)
    def SetApiKey(self, provider_id, api_key):
        """Store (or with an empty key, forget) a provider API key in the keyring."""
        try:
            self.providers.set_api_key(str(provider_id), str(api_key))
        except (ProviderError, CredentialError) as e:
            return self._json_error(str(e))
        return json.dumps({"ok": True})

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    def SignOut(self, provider_id):
        """Forget a provider's OAuth tokens (API keys are kept)."""
        try:
            self.providers.get(str(provider_id))
            self.providers.sign_out(str(provider_id))
        except (ProviderError, CredentialError) as e:
            return self._json_error(str(e))
        return json.dumps({"ok": True})

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    @rate_limited(rate=10, window_seconds=60)
    def StartSignIn(self, provider_id):
        """Begin OAuth sign-in in the background.

        Progress is reported by the SignInStatus signal and GetSignInStatus:
        ``awaiting_user`` carries ``auth_url`` (open it in a browser) or
        ``verification_uri`` + ``user_code`` (show them), then ``done`` or
        ``error``.
        """
        pid = str(provider_id)
        try:
            cfg = self.providers.get(pid)
        except ProviderError as e:
            return self._json_error(str(e))
        if not cfg.oauth or not cfg.oauth.ready():
            return self._json_error(f"{cfg.label} has no sign-in configured")
        if not self.providers.keyring_available:
            return self._json_error("no system keyring is available to keep the sign-in")
        with self._oauth_lock:
            old = self._oauth_sessions.get(pid)
            if old and old["state"] in ("starting", "awaiting_user"):
                old["cancel"].set()
            session = {"state": "starting", "info": {}, "cancel": threading.Event()}
            self._oauth_sessions[pid] = session
        threading.Thread(target=self._do_sign_in, args=(pid, session), daemon=True).start()
        return json.dumps({"ok": True})

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    def GetSignInStatus(self, provider_id):
        with self._oauth_lock:
            session = self._oauth_sessions.get(str(provider_id))
            if session is None:
                return json.dumps({"state": "idle"})
            return json.dumps({"state": session["state"], **session["info"]})

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="b")
    def CancelSignIn(self, provider_id):
        with self._oauth_lock:
            session = self._oauth_sessions.get(str(provider_id))
        if session is None:
            return False
        session["cancel"].set()
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def GetModelSettings(self):
        """The model chosen for each routing tier (no network calls, unlike GetStatus)."""
        with self._config_lock:
            return json.dumps({k: self.config.get(k, "") for k in MODEL_TIERS})

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

    # ------------------------------------------------------------------
    # D-Bus Signals
    # ------------------------------------------------------------------

    @dbus.service.signal("org.axonos.Brain", signature="ss")
    def TokenGenerated(self, transaction_id, token):
        """Fires when a stream chunk is generated."""
        pass

    @dbus.service.signal("org.axonos.Brain", signature="sbs")
    def GenerationCompleted(self, transaction_id, success, error_msg):
        """Fires when stream finishes."""
        pass

    @dbus.service.signal("org.axonos.Brain", signature="sss")
    def SignInStatus(self, provider_id, state, info_json):
        """Fires as OAuth sign-in progresses (awaiting_user, done, error)."""
        pass

    @dbus.service.signal("org.axonos.Brain", signature="sxxs")
    def PullProgress(self, model_name, completed_bytes, total_bytes, status):
        """Fires during model downloading updates."""
        pass

    # ------------------------------------------------------------------
    # Background Workers
    # ------------------------------------------------------------------

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
        with self._http_post(f"{OLLAMA_BASE_URL}{path}", payload) as r:
            # FIX 3: Set per-read timeout on the underlying socket
            self._set_stream_timeout(r)
            for raw_line in r:
                line = raw_line.decode().strip()
                if not line:
                    continue
                token = extract(json.loads(line))
                if token:
                    yield token

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

    def _do_generate_sync(self, prompt, system, model):
        try:
            return self._complete(model, system, prompt)
        except ProviderError as e:
            return f"[Error: {e}]"
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

    def _chat_messages(self, conv_id, context):
        """Build the /api/chat message list, system prompt first.

        Ollama's chat endpoint has no top-level "system" field (it is silently
        ignored), so the safety rules and context must be a system message.
        Stored messages with role "system" are dropped: only the service
        decides what the system prompt says.
        """
        system_prompt = CHAT_SYSTEM_PROMPT
        conv_prompt = self.store.get_system_prompt(conv_id)
        if conv_prompt:
            system_prompt += f"\n\nConversation instructions:\n{_sanitize_context(str(conv_prompt))}"
        if context:
            system_prompt += f"\n\nHere is the user's current desktop context:\n{_sanitize_context(str(context))}"
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in self.store.get_messages(conv_id)
            if m["role"] in ("user", "assistant")
        ]
        return [{"role": "system", "content": system_prompt}, *history]

    def _do_chat_sync(self, conv_id, context, model):
        api_msgs = self._chat_messages(conv_id, context)
        try:
            if self._is_cloud(model):
                return _sanitize_output("".join(self.providers.chat(model, api_msgs, stream=False)))
            payload = {
                "model": model,
                "messages": api_msgs,
                "stream": False,
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/chat", payload) as resp:
                data = json.loads(resp.read().decode())
                return _sanitize_output(data.get("message", {}).get("content", ""))
        except ProviderError as e:
            return f"[Error: {e}]"
        except Exception as e:
            logger.debug("Chat failed: %s", e)
            return "[Error: AI chat unavailable]"

    def _do_chat_stream(self, tx_id, conv_id, context, model):
        api_msgs = self._chat_messages(conv_id, context)
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


if __name__ == "__main__":
    BrainService.main()
