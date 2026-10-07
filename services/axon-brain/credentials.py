"""Provider credential storage in the system keyring.

API keys and OAuth tokens for cloud AI providers are kept in the user's
Secret Service keyring (GNOME Keyring on Axon OS) through libsecret, never
in a config file. Each provider has one secret: a JSON object that may hold
``api_key`` and/or ``access_token``, ``refresh_token``, ``expires_at``.

When libsecret is unavailable (containers, CI), the store refuses to save and
only environment variables can supply keys, so secrets never fall back to
plaintext on disk.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from _log_helper import resolve_logger as configure_app_logger

log = configure_app_logger("axon-credentials")

_SCHEMA_NAME = "org.axonos.Brain.ProviderCredential"


class CredentialError(RuntimeError):
    """Raised when credentials cannot be stored or read."""


class _SecretBackend:
    """libsecret (Secret Service) backend."""

    def __init__(self) -> None:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret

        self._secret = Secret
        self._schema = Secret.Schema.new(
            _SCHEMA_NAME,
            Secret.SchemaFlags.NONE,
            {"provider": Secret.SchemaAttributeType.STRING},
        )

    def get(self, provider_id: str) -> str | None:
        result: str | None = self._secret.password_lookup_sync(
            self._schema, {"provider": provider_id}, None
        )
        return result

    def set(self, provider_id: str, value: str) -> None:
        ok = self._secret.password_store_sync(
            self._schema,
            {"provider": provider_id},
            self._secret.COLLECTION_DEFAULT,
            f"Axon AI provider: {provider_id}",
            value,
            None,
        )
        if not ok:
            raise CredentialError("keyring refused to store the secret")

    def delete(self, provider_id: str) -> None:
        self._secret.password_clear_sync(self._schema, {"provider": provider_id}, None)


class MemoryBackend:
    """In-process backend, for tests."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self.data.get(provider_id)

    def set(self, provider_id: str, value: str) -> None:
        self.data[provider_id] = value

    def delete(self, provider_id: str) -> None:
        self.data.pop(provider_id, None)


class CredentialStore:
    """Read and write per-provider secrets.

    Args:
        backend: Object with ``get``/``set``/``delete``. Defaults to libsecret;
            ``None`` when libsecret cannot be loaded.
    """

    def __init__(self, backend: Any = "auto") -> None:
        if backend == "auto":
            try:
                backend = _SecretBackend()
            except Exception as e:  # ImportError, ValueError from require_version
                log.warning("System keyring unavailable, cloud keys only via env vars: %s", e)
                backend = None
        self._backend = backend
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True when secrets can be saved."""
        return self._backend is not None

    def get(self, provider_id: str) -> dict[str, Any]:
        """Return the stored secret dict for *provider_id* (empty if none)."""
        if self._backend is None:
            return {}
        with self._lock:
            try:
                raw = self._backend.get(provider_id)
            except Exception as e:
                log.warning("Keyring lookup failed for %s: %s", provider_id, e)
                return {}
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def update(self, provider_id: str, **fields: Any) -> None:
        """Merge *fields* into the stored secret; a ``None`` value removes the key."""
        if self._backend is None:
            raise CredentialError("no system keyring available to store credentials")
        with self._lock:
            try:
                raw = self._backend.get(provider_id)
            except Exception as e:
                raise CredentialError(f"could not read the keyring: {e}") from None
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            for k, v in fields.items():
                if v is None:
                    data.pop(k, None)
                else:
                    data[k] = v
            try:
                if data:
                    self._backend.set(provider_id, json.dumps(data))
                else:
                    self._backend.delete(provider_id)
            except CredentialError:
                raise
            except Exception as e:  # GLib.Error: keyring locked or missing
                raise CredentialError(f"could not save to the keyring: {e}") from None

    def clear(self, provider_id: str) -> None:
        """Forget every secret stored for *provider_id*."""
        if self._backend is None:
            return
        with self._lock:
            try:
                self._backend.delete(provider_id)
            except Exception as e:
                log.warning("Keyring delete failed for %s: %s", provider_id, e)

    def api_key(self, provider_id: str, env_var: str = "") -> str:
        """Return the provider's API key from the keyring, else from *env_var*."""
        key = self.get(provider_id).get("api_key", "") if provider_id else ""
        if not key and env_var:
            key = os.environ.get(env_var, "")
        return str(key)
