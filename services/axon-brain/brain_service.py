#!/usr/bin/env python3
"""Axon Brain D-Bus Service - Centralized AI inference and model management."""

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
import tomllib
from gi.repository import GLib

try:
    from axon_logger import configure_app_logger
except ImportError:  # running standalone — repo root / installed shim not on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from axon_logger import configure_app_logger
    except ImportError:
        import logging as _logging

        def configure_app_logger(
            name: str,
            level: int = _logging.INFO,
            log_file: str | None = None,
            json_output: bool = False,
        ) -> _logging.Logger:
            _logging.basicConfig(level=level)
            return _logging.getLogger(name)


logger = configure_app_logger("axon-brain")

# Ensure we can import hardware_profiler and conversation_store
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hardware_profiler
from ai_router import AIRouter
from constants import (
    AXON_DIR,
    MAX_MODEL_NAME_LEN,
    MAX_PROMPT_LEN,
    OLLAMA_BASE_URL,
)
from conversation_store import ConversationStore
from prompts import CHAT_SYSTEM_PROMPT
from service_utils import rate_limited

CONFIG_FILE = AXON_DIR / "config.toml"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MAX_CONTEXT_LEN = 2000


def _sanitize_output(text: str) -> str:
    """Strip ANSI escape sequences and null bytes from AI output."""
    text = _ANSI_RE.sub("", text)
    text = text.replace("\x00", "")
    return text


def _sanitize_context(context: str) -> str:
    """Truncate and clean context before embedding in system prompt."""
    safe = context.replace("\x00", "")
    if len(safe) > _MAX_CONTEXT_LEN:
        safe = safe[:_MAX_CONTEXT_LEN]
    return safe


class BrainService(dbus.service.Object):
    def __init__(self):
        # Initialise GLib main loop integration with D-Bus
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()

        # Request name org.axonos.Brain
        try:
            self.bus_name = dbus.service.BusName("org.axonos.Brain", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            logger.error("org.axonos.Brain service is already running.")
            sys.exit(1)

        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/Brain")

        # Initialize sub-components
        self._config_lock = threading.RLock()
        self.store = ConversationStore()
        self.load_config()
        self.router = AIRouter(self.config)
        logger.info("Axon Brain D-Bus Service registered successfully at /org/axonos/Brain")

    def save_config(self):
        """Saves current configuration to TOML format atomically."""
        with self._config_lock:
            try:
                content = "# Axon OS AI Configuration\n\n"
                for k, v in self.config.items():
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

            # Profile hardware and save default config
            profile = hardware_profiler.profile_hardware()
            self.config = {
                "speed_model": profile["recommendations"]["speed"]["model"],
                "general_model": profile["recommendations"]["general"]["model"],
                "deep_model": profile["recommendations"]["deep"]["model"],
            }
            self.save_config()

    def _http_post(self, url, payload, stream=False, timeout=60.0, max_retries=5):
        """Helper to execute urllib POST requests with retry logic."""
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        max_backoff = 30.0
        for attempt in range(max_retries):
            try:
                return urllib.request.urlopen(req, timeout=timeout)
            except (urllib.error.URLError, OSError):
                if attempt == max_retries - 1:
                    raise
                backoff = min(2.0**attempt, max_backoff)
                time.sleep(backoff)

    def _http_get(self, url, timeout=5.0):
        """Helper to execute urllib GET requests with retry logic."""
        req = urllib.request.Request(url)
        max_retries = 5
        max_backoff = 30.0
        for attempt in range(max_retries):
            try:
                return urllib.request.urlopen(req, timeout=timeout)
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
        """True if name is a safe Ollama model tag (no shell/path injection)."""
        if not isinstance(name, str) or not name:
            return False
        if len(name) > BrainService._MAX_MODEL_NAME_LEN or ".." in name:
            return False
        return bool(BrainService._MODEL_NAME_RE.match(name))

    @staticmethod
    def _validate_prompt(prompt):
        """True if prompt is a non-empty string within the length limit."""
        if not isinstance(prompt, str) or not prompt:
            return False
        return len(prompt) <= BrainService._MAX_PROMPT_LEN

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

        system_prompt = ""
        if context:
            system_prompt = (
                f"Here is the user's desktop context:\n\n{_sanitize_context(str(context))}"
            )

        if stream:
            tx_id = str(uuid.uuid4())
            threading.Thread(
                target=self._do_generate_stream,
                args=(tx_id, prompt, system_prompt, model),
                daemon=True,
            ).start()
            return tx_id
        else:
            return self._do_generate_sync(prompt, system_prompt, model)

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def CreateConversation(self, system_prompt, title):
        conv_id = self.store.create_conversation(
            system_prompt=system_prompt if system_prompt else None, title=title if title else None
        )
        return conv_id

    @dbus.service.method("org.axonos.Brain", in_signature="sss", out_signature="b")
    def AddMessage(self, conversation_id, role, content):
        if role not in ("user", "assistant", "system"):
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
        self.store.add_message(conversation_id, "user", message)

        if not model:
            model, _reason = self.router.select_model(str(message), str(context))

        if stream:
            tx_id = str(uuid.uuid4())
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
            payload = {
                "model": model,
                "prompt": prompt,
                "system": system_prompt,
                "stream": False,
                "options": {"temperature": 0.1},
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload, timeout=5.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    result = data.get("response", "").strip().replace('"', "").replace("'", "")
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
            payload = {
                "model": model,
                "prompt": text,
                "system": system_prompt,
                "stream": False,
                "options": {"temperature": 0.1},
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload, timeout=10.0) as resp:
                if resp.status == 200:
                    result_data = json.loads(resp.read().decode())
                    result = result_data.get("response", "").strip()

                    if result.startswith("{"):
                        try:
                            parsed = json.loads(result)
                            if (
                                isinstance(parsed, dict)
                                and parsed.get("action") == "run_command"
                                and isinstance(parsed.get("command"), str)
                            ):
                                return json.dumps(
                                    {
                                        "action": "run_command",
                                        "command": parsed["command"],
                                    }
                                )
                            if (
                                isinstance(parsed, dict)
                                and parsed.get("action") == "open_app"
                                and isinstance(parsed.get("app"), str)
                            ):
                                return json.dumps(
                                    {
                                        "action": "open_app",
                                        "app": parsed["app"],
                                    }
                                )
                        except json.JSONDecodeError:
                            pass
                        return text
                    return result
        except Exception as e:
            logger.debug("ClassifyIntent failed: %s", e)
            return '{"action": "error", "message": "AI classification unavailable"}'
        return text

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def GetEmbeddings(self, prompt, model):
        """Generates embedding vector for a given prompt using Ollama."""
        if not model:
            # Try speed model, general model or default nomic-embed-text
            model = self.config.get("speed_model", "nomic-embed-text")
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
                    self.PullProgress(model_name, completed, total, status)
        except Exception as e:
            logger.debug("Pull failed for %s: %s", model_name, e)
            self.PullProgress(model_name, 0, 0, "Pull failed")

    def _do_generate_sync(self, prompt, system, model):
        try:
            payload = {"model": model, "prompt": prompt, "stream": False}
            if system:
                payload["system"] = system
            with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload) as resp:
                data = json.loads(resp.read().decode())
                return _sanitize_output(data.get("response", ""))
        except Exception as e:
            logger.debug("Generate failed: %s", e)
            return "[Error: AI generation unavailable]"

    def _do_generate_stream(self, tx_id, prompt, system, model):
        try:
            payload = {"model": model, "prompt": prompt, "stream": True}
            if system:
                payload["system"] = system
            with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload) as r:
                for raw_line in r:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        self.TokenGenerated(tx_id, _sanitize_output(token))
            self.GenerationCompleted(tx_id, True, "")
        except Exception as e:
            logger.debug("Generate stream failed: %s", e)
            self.GenerationCompleted(tx_id, False, "Generation failed")

    def _do_chat_sync(self, conv_id, context, model):
        messages = self.store.get_messages(conv_id)
        api_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        system_prompt = CHAT_SYSTEM_PROMPT
        if context:
            system_prompt += f"\n\nHere is the user's current desktop context:\n{_sanitize_context(str(context))}"
        try:
            payload = {
                "model": model,
                "messages": api_msgs,
                "stream": False,
                "system": system_prompt,
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/chat", payload) as resp:
                data = json.loads(resp.read().decode())
                return _sanitize_output(data.get("message", {}).get("content", ""))
        except Exception as e:
            logger.debug("Chat failed: %s", e)
            return "[Error: AI chat unavailable]"

    def _do_chat_stream(self, tx_id, conv_id, context, model):
        messages = self.store.get_messages(conv_id)
        api_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        system_prompt = CHAT_SYSTEM_PROMPT
        if context:
            system_prompt += f"\n\nHere is the user's current desktop context:\n{_sanitize_context(str(context))}"

        accumulated = ""
        try:
            payload = {
                "model": model,
                "messages": api_msgs,
                "stream": True,
                "system": system_prompt,
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/chat", payload) as r:
                for raw_line in r:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        token = _sanitize_output(token)
                        accumulated += token
                        self.TokenGenerated(tx_id, token)
            # Save final response
            self.store.add_message(conv_id, "assistant", accumulated)
            self.GenerationCompleted(tx_id, True, "")
        except Exception as e:
            logger.debug("Chat stream failed: %s", e)
            self.GenerationCompleted(tx_id, False, "Chat failed")


if __name__ == "__main__":
    import signal

    # Start loop
    loop = GLib.MainLoop()
    service = BrainService()

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        loop.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Stopping Axon Brain service...")
        loop.quit()
