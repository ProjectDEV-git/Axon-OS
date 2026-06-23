"""Shared constants for Axon OS services.

Centralizes hardcoded values that were previously scattered across service files.
Import from here instead of duplicating magic numbers.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (XDG Base Directory Specification compliant)
# ---------------------------------------------------------------------------
_XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

AXON_DIR = _XDG_DATA_HOME / "axon"
AXON_CONFIG_DIR = _XDG_CONFIG_HOME / "axon"
AXON_CACHE_DIR = _XDG_CACHE_HOME / "axon"

CONVERSATIONS_DB = AXON_DIR / "conversations.db"
SEMANTIC_INDEX_DB = AXON_DIR / "semantic-index.db"
WHISPER_DIR = AXON_DIR / "models" / "whisper"

# ---------------------------------------------------------------------------
# Ollama / AI
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
MAX_MODEL_NAME_LEN = 256
MAX_PROMPT_LEN = 10_000
AI_AUDIT_TIMEOUT = 25  # seconds

# ---------------------------------------------------------------------------
# D-Bus service names
# ---------------------------------------------------------------------------
DBUS_NAME_BRAIN = "org.axonos.Brain"
DBUS_NAME_CONTEXT = "org.axonos.Context"
DBUS_NAME_SEARCH = "org.axonos.Search"
DBUS_NAME_VOICE = "org.axonos.Voice"
DBUS_NAME_GUI_AGENT = "org.axonos.GuiAgent"
DBUS_NAME_SANDBOX = "org.axonos.Sandbox"

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_RECORD_SECONDS = 30
MAX_CLIPBOARD_HISTORY = 50
MAX_CLIPBOARD_ENTRY_LEN = 500
MAX_FILE_BYTES = 512 * 1024
RESCAN_INTERVAL = 15 * 60  # seconds between full rescans
