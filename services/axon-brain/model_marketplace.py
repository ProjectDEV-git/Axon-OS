"""AI Model Marketplace — model catalog, download management, disk monitoring.

Provides a D-Bus interface (org.axonos.ModelMarketplace) for browsing
available models, managing downloads, and monitoring disk usage. Talks
to Ollama for actual model operations and maintains a local catalog
of recommended models with metadata.
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axon_logger import configure_app_logger

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import AXON_DIR, OLLAMA_BASE_URL

log = configure_app_logger("axon-marketplace", level=__import__("logging").INFO)

import urllib.error
import urllib.request

CATALOG_FILE = AXON_DIR / "model_catalog.json"

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_MAX_MODEL_NAME_LEN = 256


def _validate_model_name(name: str) -> bool:
    """True if name is a safe Ollama model tag."""
    if not isinstance(name, str) or not name:
        return False
    if len(name) > _MAX_MODEL_NAME_LEN or ".." in name:
        return False
    return bool(_MODEL_NAME_RE.match(name))


# Curated model catalog with metadata
DEFAULT_CATALOG = [
    {
        "name": "llama3.2:3b",
        "family": "llama",
        "size_category": "small",
        "description": "Fast, lightweight model for quick tasks and classification",
        "use_case": "speed",
        "parameters": "3B",
        "quantization": "Q4_K_M",
        "license": "Meta Llama",
        "tags": ["fast", "lightweight", "classification"],
    },
    {
        "name": "mistral:7b",
        "family": "mistral",
        "size_category": "medium",
        "description": "Balanced model for general-purpose assistant tasks",
        "use_case": "general",
        "parameters": "7B",
        "quantization": "Q4_K_M",
        "license": "Apache 2.0",
        "tags": ["balanced", "general", "assistant"],
    },
    {
        "name": "qwen2.5:7b",
        "family": "qwen",
        "size_category": "medium",
        "description": "Strong reasoning and code generation capabilities",
        "use_case": "general",
        "parameters": "7B",
        "quantization": "Q4_K_M",
        "license": "Apache 2.0",
        "tags": ["reasoning", "code", "multilingual"],
    },
    {
        "name": "codellama:7b",
        "family": "codellama",
        "size_category": "medium",
        "description": "Specialized for code generation and explanation",
        "use_case": "code",
        "parameters": "7B",
        "quantization": "Q4_K_M",
        "license": "Meta Llama",
        "tags": ["code", "programming", "completion"],
    },
    {
        "name": "deepseek-coder:6.7b",
        "family": "deepseek",
        "size_category": "medium",
        "description": "Excellent code understanding and generation",
        "use_case": "code",
        "parameters": "6.7B",
        "quantization": "Q4_K_M",
        "license": "MIT",
        "tags": ["code", "analysis", "debugging"],
    },
    {
        "name": "phi3:mini",
        "family": "phi",
        "size_category": "small",
        "description": "Microsoft's efficient small model for on-device use",
        "use_case": "speed",
        "parameters": "3.8B",
        "quantization": "Q4_K_M",
        "license": "MIT",
        "tags": ["fast", "efficient", "on-device"],
    },
    {
        "name": "gemma2:9b",
        "family": "gemma",
        "size_category": "medium",
        "description": "Google's high-performance model with strong reasoning",
        "use_case": "general",
        "parameters": "9B",
        "quantization": "Q4_K_M",
        "license": "Gemma Terms",
        "tags": ["reasoning", "general", "google"],
    },
    {
        "name": "nomic-embed-text",
        "family": "nomic",
        "size_category": "tiny",
        "description": "Text embedding model for semantic search (used by Axon Search)",
        "use_case": "embedding",
        "parameters": "137M",
        "quantization": "f16",
        "license": "Apache 2.0",
        "tags": ["embedding", "search", "vector"],
    },
    {
        "name": "llama3.2:1b",
        "family": "llama",
        "size_category": "tiny",
        "description": "Ultra-lightweight model for minimal resource systems",
        "use_case": "speed",
        "parameters": "1B",
        "quantization": "Q4_K_M",
        "license": "Meta Llama",
        "tags": ["ultra-fast", "minimal", "embedded"],
    },
    {
        "name": "neural-chat:7b",
        "family": "neural-chat",
        "size_category": "medium",
        "description": "Intel's conversational model optimized for dialogue",
        "use_case": "general",
        "parameters": "7B",
        "quantization": "Q4_K_M",
        "license": "Apache 2.0",
        "tags": ["conversational", "dialogue", "chat"],
    },
]


def _http_get(url, timeout=5.0):
    req = urllib.request.Request(url)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception:
        return None


def _http_post(url, payload, timeout=10.0):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception:
        return None


class ModelMarketplaceService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        try:
            self.bus_name = dbus.service.BusName(
                "org.axonos.ModelMarketplace", bus=self.session_bus
            )
        except dbus.exceptions.NameExistsException:
            log.error("org.axonos.ModelMarketplace service is already running.")
            sys.exit(1)
        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/ModelMarketplace")

        self._catalog = self._load_catalog()
        self._downloads: dict[str, dict] = {}
        self._lock = threading.Lock()
        log.info("ModelMarketplace registered at /org/axonos/ModelMarketplace")

    # ------------------------------------------------------------------
    # D-Bus API
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.ModelMarketplace", out_signature="s")
    def ListCatalog(self):
        """Return the full model catalog as JSON."""
        return json.dumps(self._catalog)

    @dbus.service.method("org.axonos.ModelMarketplace", in_signature="s", out_signature="s")
    def SearchCatalog(self, query):
        """Search catalog by name, description, or tags."""
        if not query:
            return json.dumps(self._catalog)
        q = query.lower()
        results = [
            m
            for m in self._catalog
            if q in m["name"].lower()
            or q in m.get("description", "").lower()
            or any(q in tag for tag in m.get("tags", []))
        ]
        return json.dumps(results)

    @dbus.service.method("org.axonos.ModelMarketplace", out_signature="s")
    def ListInstalled(self):
        """Return models currently pulled into Ollama."""
        try:
            resp = _http_get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3.0)
            if resp is None:
                return "[]"
            try:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    models = data.get("models", [])
                    enriched = []
                    for m in models:
                        info = self._find_in_catalog(m["name"])
                        enriched.append(
                            {
                                "name": m["name"],
                                "size": m.get("size", 0),
                                "size_gb": round(m.get("size", 0) / (1024**3), 2),
                                "modified": m.get("modified_at", ""),
                                "family": info.get("family", "unknown") if info else "unknown",
                                "description": info.get("description", "") if info else "",
                            }
                        )
                    return json.dumps(enriched)
            finally:
                resp.close()
        except Exception as e:
            return json.dumps({"error": str(e)})
        return "[]"

    @dbus.service.method("org.axonos.ModelMarketplace", in_signature="s", out_signature="b")
    def PullModel(self, model_name):
        """Start downloading a model. Returns True if started."""
        if not _validate_model_name(model_name):
            return False
        with self._lock:
            if model_name in self._downloads:
                return False
            self._downloads[model_name] = {
                "status": "downloading",
                "progress": 0,
                "started": time.time(),
            }
        threading.Thread(target=self._do_pull, args=(model_name,), daemon=True).start()
        return True

    @dbus.service.method("org.axonos.ModelMarketplace", in_signature="s", out_signature="b")
    def DeleteModel(self, model_name):
        """Delete a pulled model from Ollama."""
        if not _validate_model_name(model_name):
            return False
        try:
            resp = _http_post(
                f"{OLLAMA_BASE_URL}/api/delete",
                {"name": model_name},
                timeout=10.0,
            )
            try:
                return resp is not None and resp.status == 200
            finally:
                if resp is not None:
                    resp.close()
        except Exception:
            return False

    @dbus.service.method("org.axonos.ModelMarketplace", out_signature="s")
    def GetDiskUsage(self):
        """Return disk usage information for models."""
        try:
            total_bytes = 0
            model_count = 0
            resp = _http_get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3.0)
            if resp is not None:
                try:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        for m in data.get("models", []):
                            total_bytes += m.get("size", 0)
                            model_count += 1
                finally:
                    resp.close()

            # Get system disk info
            stat = os.statvfs(str(AXON_DIR)) if AXON_DIR.exists() else None
            disk_total = stat.f_blocks * stat.f_frsize if stat else 0
            disk_free = stat.f_bavail * stat.f_frsize if stat else 0

            return json.dumps(
                {
                    "models_size_gb": round(total_bytes / (1024**3), 2),
                    "model_count": model_count,
                    "disk_total_gb": round(disk_total / (1024**3), 2),
                    "disk_free_gb": round(disk_free / (1024**3), 2),
                    "disk_used_percent": round((1 - disk_free / disk_total) * 100, 1)
                    if disk_total > 0
                    else 0,
                }
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

    @dbus.service.method("org.axonos.ModelMarketplace", out_signature="s")
    def GetDownloadStatus(self):
        """Return status of active downloads."""
        with self._lock:
            return json.dumps(dict(self._downloads))

    @dbus.service.method("org.axonos.ModelMarketplace", out_signature="s")
    def GetRecommendations(self):
        """Get model recommendations based on installed models."""
        installed = json.loads(self.ListInstalled())
        installed_names = {m["name"] for m in installed} if isinstance(installed, list) else set()

        recommendations = []
        for model in self._catalog:
            if model["name"] not in installed_names:
                score: float = 0.0
                if model["use_case"] == "speed":
                    score = 0.9
                elif model["use_case"] == "general":
                    score = 0.8
                elif model["use_case"] == "code":
                    score = 0.7
                elif model["use_case"] == "embedding":
                    score = 0.6
                recommendations.append({**model, "relevance_score": score})

        recommendations.sort(key=lambda m: m["relevance_score"], reverse=True)
        return json.dumps(recommendations[:5])

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    @dbus.service.signal("org.axonos.ModelMarketplace", signature="ssx")
    def PullProgress(self, model_name, status, progress):
        """Fires during model download. progress is 0-100 or -1 for indeterminate."""

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------

    def _do_pull(self, model_name):
        try:
            resp = _http_post(f"{OLLAMA_BASE_URL}/api/pull", {"name": model_name})
            try:
                if resp:
                    for raw_line in resp:
                        line = raw_line.decode().strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        status = data.get("status", "")
                        completed = data.get("completed", 0)
                        total = data.get("total", 0)
                        progress = int(completed / total * 100) if total > 0 else -1
                        self.PullProgress(model_name, status, progress)
                        with self._lock:
                            if model_name in self._downloads:
                                self._downloads[model_name]["status"] = status
                                self._downloads[model_name]["progress"] = progress
                    with self._lock:
                        if model_name in self._downloads:
                            self._downloads[model_name]["status"] = "completed"
                            self._downloads[model_name]["progress"] = 100
                else:
                    with self._lock:
                        if model_name in self._downloads:
                            self._downloads[model_name]["status"] = "error"
            finally:
                if resp is not None:
                    resp.close()
        except Exception as e:
            with self._lock:
                if model_name in self._downloads:
                    self._downloads[model_name]["status"] = f"error: {e}"
            log.error("Pull failed for %s: %s", model_name, e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_catalog(self) -> list[dict]:
        if CATALOG_FILE.exists():
            try:
                result = json.loads(CATALOG_FILE.read_text())
                if isinstance(result, list):
                    return result
            except Exception as e:
                log.warning("Catalog file unreadable, resetting to defaults: %s", e)
        self._save_catalog(DEFAULT_CATALOG)
        return DEFAULT_CATALOG

    def _save_catalog(self, catalog: list) -> None:
        try:
            AXON_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CATALOG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(catalog, indent=2))
            tmp.replace(CATALOG_FILE)
        except Exception as e:
            log.warning("Failed to save catalog: %s", e)

    def _find_in_catalog(self, name: str) -> dict | None:
        for m in self._catalog:
            if m["name"] == name:
                return m
        return None


if __name__ == "__main__":
    import signal

    loop = GLib.MainLoop()
    service = ModelMarketplaceService()

    def _shutdown(signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        loop.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        loop.run()
    except KeyboardInterrupt:
        loop.quit()
