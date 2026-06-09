#!/usr/bin/env python3
import os
import sys
import json
import uuid
import threading
import sqlite3
import urllib.request
import urllib.error
import tomllib
from pathlib import Path
from datetime import datetime

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# Ensure we can import hardware_profiler and conversation_store
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hardware_profiler
from conversation_store import ConversationStore

# Path configurations
AXON_DIR = Path.home() / ".axon"
CONFIG_FILE = AXON_DIR / "config.toml"
OLLAMA_BASE_URL = "http://localhost:11434"

class BrainService(dbus.service.Object):
    def __init__(self):
        # Initialise GLib main loop integration with D-Bus
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        
        # Request name org.axonos.Brain
        try:
            self.bus_name = dbus.service.BusName('org.axonos.Brain', bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            print("org.axonos.Brain service is already running.")
            sys.exit(1)
            
        dbus.service.Object.__init__(self, self.session_bus, '/org/axonos/Brain')
        
        # Initialize sub-components
        self.store = ConversationStore()
        self.load_config()
        print("Axon Brain D-Bus Service registered successfully at /org/axonos/Brain")

    def save_config(self):
        """Saves current configuration to TOML format."""
        try:
            content = "# Axon OS AI Configuration\n\n"
            for k, v in self.config.items():
                # Escape any backslashes or quotes
                escaped_v = str(v).replace("\\", "\\\\").replace('"', '\\"')
                content += f'{k} = "{escaped_v}"\n'
            CONFIG_FILE.write_text(content)
        except Exception as e:
            print(f"Error saving config to {CONFIG_FILE}: {e}")

    def load_config(self):
        """Loads model config, profiles hardware if not present."""
        AXON_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "rb") as f:
                    self.config = tomllib.load(f)
                # Verify required keys exist
                if all(k in self.config for k in ("speed_model", "general_model", "deep_model")):
                    return
            except Exception:
                pass
        
        # Profile hardware and save default config
        profile = hardware_profiler.profile_hardware()
        self.config = {
            "speed_model": profile["recommendations"]["speed"]["model"],
            "general_model": profile["recommendations"]["general"]["model"],
            "deep_model": profile["recommendations"]["deep"]["model"]
        }
        self.save_config()

    def _http_post(self, url, payload, stream=False, timeout=60.0):
        """Helper to execute urllib POST requests."""
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        return urllib.request.urlopen(req, timeout=timeout)

    def _http_get(self, url, timeout=5.0):
        """Helper to execute urllib GET requests."""
        req = urllib.request.Request(url)
        return urllib.request.urlopen(req, timeout=timeout)

    # ------------------------------------------------------------------
    # D-Bus Methods
    # ------------------------------------------------------------------

    @dbus.service.method('org.axonos.Brain', in_signature='', out_signature='s')
    def GetStatus(self):
        """Returns JSON about Ollama and model config status."""
        status = {
            "ollama_online": False,
            "active_models": [],
            "configured_models": self.config
        }
        try:
            with self._http_get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0) as resp:
                if resp.status == 200:
                    status["ollama_online"] = True
                    data = json.loads(resp.read().decode())
                    status["active_models"] = [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return json.dumps(status)

    @dbus.service.method('org.axonos.Brain', in_signature='', out_signature='s')
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

    @dbus.service.method('org.axonos.Brain', in_signature='s', out_signature='b')
    def PullModel(self, model_name):
        """Starts model pull in a background thread."""
        threading.Thread(target=self._do_pull_model, args=(model_name,), daemon=True).start()
        return True

    @dbus.service.method('org.axonos.Brain', in_signature='sssb', out_signature='s')
    def Generate(self, prompt, context, model, stream):
        """Unified text generation interface. If streaming, returns a transaction ID."""
        if not model:
            model = self.config["general_model"]
            
        system_prompt = ""
        if context:
            system_prompt = f"Here is the user's desktop context:\n\n{context}"
            
        if stream:
            tx_id = str(uuid.uuid4())
            threading.Thread(
                target=self._do_generate_stream, 
                args=(tx_id, prompt, system_prompt, model), 
                daemon=True
            ).start()
            return tx_id
        else:
            return self._do_generate_sync(prompt, system_prompt, model)

    @dbus.service.method('org.axonos.Brain', in_signature='ss', out_signature='s')
    def CreateConversation(self, system_prompt, title):
        conv_id = self.store.create_conversation(
            system_prompt=system_prompt if system_prompt else None,
            title=title if title else None
        )
        return conv_id

    @dbus.service.method('org.axonos.Brain', in_signature='sss', out_signature='b')
    def AddMessage(self, conversation_id, role, content):
        self.store.add_message(conversation_id, role, content)
        return True

    @dbus.service.method('org.axonos.Brain', in_signature='s', out_signature='s')
    def GetMessages(self, conversation_id):
        messages = self.store.get_messages(conversation_id)
        return json.dumps(messages)

    @dbus.service.method('org.axonos.Brain', in_signature='', out_signature='s')
    def ListConversations(self):
        conversations = self.store.list_conversations()
        return json.dumps(conversations)

    @dbus.service.method('org.axonos.Brain', in_signature='s', out_signature='b')
    def DeleteConversation(self, conversation_id):
        self.store.delete_conversation(conversation_id)
        return True

    @dbus.service.method('org.axonos.Brain', in_signature='ss', out_signature='b')
    def UpdateTitle(self, conversation_id, title):
        self.store.update_title(conversation_id, title)
        return True

    @dbus.service.method('org.axonos.Brain', in_signature='ssssb', out_signature='s')
    def SendMessage(self, conversation_id, message, context, model, stream):
        """Persists user message and streams or blocks assistant reply with ambient context."""
        self.store.add_message(conversation_id, "user", message)
        
        if not model:
            model = self.config["general_model"]
            
        if stream:
            tx_id = str(uuid.uuid4())
            threading.Thread(
                target=self._do_chat_stream,
                args=(tx_id, conversation_id, context, model),
                daemon=True
            ).start()
            return tx_id
        else:
            resp = self._do_chat_sync(conversation_id, context, model)
            self.store.add_message(conversation_id, "assistant", resp)
            return resp

    @dbus.service.method('org.axonos.Brain', in_signature='ss', out_signature='s')
    def ClassifyWindow(self, title, wm_class):
        """Classifies a newly opened window title/class into one of the 9 spaces."""
        model = self.config["speed_model"]
        prompt = f"App: {wm_class}\nWindow Title: {title}"
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
                "options": {"temperature": 0.1}
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload, timeout=5.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    result = data.get("response", "").strip().replace('"', '').replace("'", "")
                    valid_spaces = ["Code", "Web", "Chat", "Files", "Media", "Work", "Personal", "Terminal", "Notes"]
                    for s in valid_spaces:
                        if s.lower() in result.lower():
                            return s
        except Exception:
            pass
        return "Default"

    @dbus.service.method('org.axonos.Brain', in_signature='s', out_signature='s')
    def ClassifyIntent(self, text):
        """Spotlight-style classification of workspace intents using the Speed model."""
        model = self.config["speed_model"]
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
                "options": {"temperature": 0.1}
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload, timeout=10.0) as resp:
                if resp.status == 200:
                    result_data = json.loads(resp.read().decode())
                    result = result_data.get("response", "").strip()
                    return result
        except Exception as e:
            return f"[Error: {e}]"
        return text

    @dbus.service.method('org.axonos.Brain', in_signature='ss', out_signature='s')
    def GetEmbeddings(self, prompt, model):
        """Generates embedding vector for a given prompt using Ollama."""
        if not model:
            # Try speed model, general model or default nomic-embed-text
            model = self.config.get("speed_model", "nomic-embed-text")
        try:
            # Try newer /api/embed endpoint first
            payload = {"model": model, "input": prompt}
            try:
                with self._http_post(f"{OLLAMA_BASE_URL}/api/embed", payload, timeout=15.0) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        embeddings = data.get("embeddings", [])
                        if embeddings:
                            return json.dumps(embeddings[0])
            except Exception:
                pass

            # Fallback to /api/embeddings
            payload = {"model": model, "prompt": prompt}
            with self._http_post(f"{OLLAMA_BASE_URL}/api/embeddings", payload, timeout=15.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    return json.dumps(data.get("embedding", []))
        except Exception as e:
            return json.dumps({"error": str(e)})
        return "[]"

    # ------------------------------------------------------------------
    # D-Bus Signals
    # ------------------------------------------------------------------

    @dbus.service.signal('org.axonos.Brain', signature='ss')
    def TokenGenerated(self, transaction_id, token):
        """Fires when a stream chunk is generated."""
        pass

    @dbus.service.signal('org.axonos.Brain', signature='sbs')
    def GenerationCompleted(self, transaction_id, success, error_msg):
        """Fires when stream finishes."""
        pass

    @dbus.service.signal('org.axonos.Brain', signature='sxxs')
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
            self.PullProgress(model_name, 0, 0, f"Error: {e}")

    def _do_generate_sync(self, prompt, system, model):
        try:
            payload = {"model": model, "prompt": prompt, "stream": False}
            if system:
                payload["system"] = system
            with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload) as resp:
                data = json.loads(resp.read().decode())
                return data.get("response", "")
        except Exception as e:
            return f"[Error: {e}]"

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
                        self.TokenGenerated(tx_id, token)
            self.GenerationCompleted(tx_id, True, "")
        except Exception as e:
            self.GenerationCompleted(tx_id, False, str(e))

    def _do_chat_sync(self, conv_id, context, model):
        messages = self.store.get_messages(conv_id)
        api_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        system_prompt = (
            "You are Axon AI, a helpful desktop assistant integrated into Axon OS. "
            "Be concise and practical. You can use **bold** and *italic* markdown."
        )
        if context:
            system_prompt += f"\n\nHere is the user's current desktop context:\n{context}"
        try:
            payload = {
                "model": model, 
                "messages": api_msgs, 
                "stream": False,
                "system": system_prompt
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/chat", payload) as resp:
                data = json.loads(resp.read().decode())
                return data.get("message", {}).get("content", "")
        except Exception as e:
            return f"[Error: {e}]"

    def _do_chat_stream(self, tx_id, conv_id, context, model):
        messages = self.store.get_messages(conv_id)
        api_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        system_prompt = (
            "You are Axon AI, a helpful desktop assistant integrated into Axon OS. "
            "Be concise and practical. You can use **bold** and *italic* markdown."
        )
        if context:
            system_prompt += f"\n\nHere is the user's current desktop context:\n{context}"
            
        accumulated = ""
        try:
            payload = {
                "model": model, 
                "messages": api_msgs, 
                "stream": True,
                "system": system_prompt
            }
            with self._http_post(f"{OLLAMA_BASE_URL}/api/chat", payload) as r:
                for raw_line in r:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        accumulated += token
                        self.TokenGenerated(tx_id, token)
            # Save final response
            self.store.add_message(conv_id, "assistant", accumulated)
            self.GenerationCompleted(tx_id, True, "")
        except Exception as e:
            self.GenerationCompleted(tx_id, False, str(e))

if __name__ == '__main__':
    # Start loop
    loop = GLib.MainLoop()
    service = BrainService()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("Stopping Axon Brain service...")
        loop.quit()
