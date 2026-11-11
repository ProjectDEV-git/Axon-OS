"""brain_providers.py — D-Bus API for Ollama status/models and cloud providers (keys, OAuth sign-in).

Mixed into ``BrainService`` (see ``brain_service.py``); methods rely on its state
(``self.store``, ``self.config``, ``self.providers`` ...) set up in ``_setup``.
"""

import json
import logging
import threading

import dbus
import dbus.service
from brain_config import MODEL_TIERS
from constants import OLLAMA_BASE_URL
from credentials import CredentialError
from providers import ProviderError
from service_utils import rate_limited

logger = logging.getLogger("axon-brain")


class ProvidersMixin(dbus.service.Object):
    """D-Bus API for Ollama status/models and cloud providers (keys, OAuth sign-in)."""

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def GetStatus(self):
        """Returns JSON about Ollama and model config status."""
        status = {"ollama_online": False, "active_models": [], "configured_models": self.config}
        try:
            with self._http_get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0, max_retries=1) as resp:
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
            with self._http_get(f"{OLLAMA_BASE_URL}/api/tags", max_retries=1) as resp:
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

    @staticmethod
    def _json_error(message: str) -> str:
        return json.dumps({"ok": False, "error": message})

    @dbus.service.signal("org.axonos.Brain", signature="sss")
    def SignInStatus(self, provider_id, state, info_json):
        """Fires as OAuth sign-in progresses (awaiting_user, done, error)."""
        pass

    @dbus.service.signal("org.axonos.Brain", signature="sxxs")
    def PullProgress(self, model_name, completed_bytes, total_bytes, status):
        """Fires during model downloading updates."""
        pass
