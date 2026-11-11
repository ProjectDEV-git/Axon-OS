"""brain_gamemode.py — Game mode (driven by org.axonos.Gaming): suspend/resume and request gating.

Mixed into ``BrainService`` (see ``brain_service.py``); methods rely on its state
(``self.store``, ``self.config``, ``self.providers`` ...) set up in ``_setup``.
"""

import json
import logging
import threading
import time

import dbus
import dbus.service
from constants import OLLAMA_BASE_URL

logger = logging.getLogger("axon-brain")


class GameModeMixin(dbus.service.Object):
    """Game mode (driven by org.axonos.Gaming): suspend/resume and request gating."""

    def get_dbus_properties(self, interface):
        if interface != "org.axonos.Brain":
            return {}
        return {"Suspended": dbus.Boolean(self._gate.suspended)}

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="b")
    def Suspend(self, reason):
        """Free the GPU: unload every Ollama model and refuse GPU work until Resume."""
        changed = self._gate.suspend()
        logger.info("Suspended (%s)", str(reason)[:80])
        with self._streams_lock:
            for flag in self._active_streams.values():
                flag.set()  # stop streams that would keep a model busy
        threading.Thread(target=self._unload_all_models, daemon=True).start()
        if changed:
            self.SuspendedChanged(True)
            self.notify_property_changed("org.axonos.Brain", "Suspended")
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="b")
    def Resume(self):
        """Back to normal. Models load again on the next request, not now."""
        if self._gate.resume():
            logger.info("Resumed")
            self.SuspendedChanged(False)
            self.notify_property_changed("org.axonos.Brain", "Suspended")
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="b", out_signature="")
    def SetGameModeCloud(self, enabled):
        """Answer requests with the user's cloud provider while suspended."""
        self._gate.use_cloud = bool(enabled)

    @dbus.service.method("org.axonos.Brain", in_signature="u", out_signature="b")
    def AllowCpuOnly(self, seconds):
        """The user chose "Run anyway": allow Speed-model, CPU-only requests for a while."""
        return self._gate.allow_cpu(float(seconds))

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="b")
    def IsSuspended(self):
        return self._gate.suspended

    @dbus.service.signal("org.axonos.Brain", signature="b")
    def SuspendedChanged(self, suspended):
        """Fires when game mode pauses or resumes the AI."""
        pass

    def _unload_all_models(self):
        """Ask Ollama to drop every loaded model. Tries twice to catch a load in flight."""
        for attempt in range(2):
            if attempt:
                time.sleep(2.0)
            if not self._gate.suspended:
                return
            try:
                with self._http_get(f"{OLLAMA_BASE_URL}/api/ps", timeout=3.0, max_retries=1) as r:
                    loaded = [
                        m.get("name") or m.get("model")
                        for m in json.loads(r.read()).get("models", [])
                    ]
            except Exception as e:
                logger.debug("Could not list loaded models: %s", e)
                return
            for name in filter(None, loaded):
                try:
                    with self._http_post(
                        f"{OLLAMA_BASE_URL}/api/generate",
                        {"model": name, "keep_alive": 0},
                        timeout=10.0,
                        max_retries=1,
                    ) as r:
                        r.read()
                    logger.info("Unloaded %s", name)
                except Exception as e:
                    logger.debug("Could not unload %s: %s", name, e)

    def _game_mode_payload(self, url, payload):
        """Apply game mode to a request for Ollama (raises if it must not run)."""
        gate = getattr(self, "_gate", None)
        if gate is None or not gate.suspended or not url.startswith(OLLAMA_BASE_URL):
            return payload
        cpu_model = ""
        if gate.cpu_allowed:
            with self._config_lock:
                speed = str(self.config.get("speed_model", ""))
            if speed and not self._is_cloud(speed):
                cpu_model = self._resolve_local_model(self._local_name(speed))
        return gate.apply(url[len(OLLAMA_BASE_URL) :], payload, cpu_model)
