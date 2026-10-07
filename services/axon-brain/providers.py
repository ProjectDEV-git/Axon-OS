"""AI provider registry: local Ollama plus cloud and company endpoints.

Any model is addressed by a *model reference*:

* a bare name (``qwen2.5:7b``) is a local Ollama model, as before;
* ``@<provider>/<model>`` picks a model from a configured provider, e.g.
  ``@openai/gpt-4.1``, ``@anthropic/claude-sonnet-4-5``, ``@gemini/gemini-2.5-pro``
  or ``@work/gpt-4o`` for a company gateway.

Provider settings live in ``~/.local/share/axon/providers.json`` (no secrets);
API keys and OAuth tokens live in the system keyring (see ``credentials``).
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _log_helper import resolve_logger as configure_app_logger
from credentials import CredentialStore
from http_util import HTTPRequestError, check_url, request, request_json
from oauth import OAuthConfig, OAuthError, refresh_tokens

log = configure_app_logger("axon-providers")

KINDS = ("ollama", "openai", "anthropic", "gemini")
OLLAMA_ID = "ollama"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 4096
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_REF_RE = re.compile(r"^@([a-z0-9][a-z0-9-]{0,31})/(.+)$")
_MODEL_CACHE_TTL = 60.0
# env_key is preset-only: a custom provider must not be able to read another service's key
_FIELDS = {"id", "kind", "label", "base_url", "auth_header", "models", "oauth", "enabled"}
_LIST_TIMEOUT = 8.0


class ProviderError(RuntimeError):
    """A provider call failed in a way worth showing to the user."""


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Split a model reference into ``(provider_id, model)``.

    Bare names belong to Ollama. Raises ``ValueError`` for malformed refs.
    """
    if not ref.startswith("@"):
        return OLLAMA_ID, ref
    m = _REF_RE.match(ref)
    if not m:
        raise ValueError(f"malformed model reference: {ref!r}")
    return m.group(1), m.group(2)


def model_ref(provider_id: str, model: str) -> str:
    """Build the reference string for *model* on *provider_id*."""
    return model if provider_id == OLLAMA_ID else f"@{provider_id}/{model}"


@dataclass
class ProviderConfig:
    """One configured provider. ``builtin`` ones come from :data:`PRESETS`."""

    id: str
    kind: str
    label: str
    base_url: str
    env_key: str = ""
    # openai kind: "bearer", "api-key" (Azure) or "none" (local servers such as LM Studio)
    auth_header: str = "bearer"
    models: list[str] = field(default_factory=list)  # pinned names, e.g. Azure deployments
    oauth: OAuthConfig | None = None
    enabled: bool = True
    builtin: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], builtin: bool = False) -> ProviderConfig:
        oauth = data.get("oauth")
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            label=str(data.get("label") or data["id"]),
            base_url=str(data["base_url"]).rstrip("/"),
            env_key=str(data.get("env_key", "")),
            auth_header=str(data.get("auth_header", "bearer")),
            models=[str(m) for m in data.get("models", [])],
            oauth=OAuthConfig.from_dict(oauth) if isinstance(oauth, dict) and oauth else None,
            enabled=bool(data.get("enabled", True)),
            builtin=builtin,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "base_url": self.base_url,
            "enabled": self.enabled,
        }
        if self.env_key:
            out["env_key"] = self.env_key
        if self.auth_header != "bearer":
            out["auth_header"] = self.auth_header
        if self.models:
            out["models"] = list(self.models)
        if self.oauth:
            out["oauth"] = self.oauth.to_dict()
        return out

    def validate(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError("provider id must be 1-32 lowercase letters, digits or '-'")
        if self.kind not in KINDS:
            raise ValueError(f"unknown provider kind {self.kind!r}")
        if self.auth_header not in ("bearer", "api-key", "none"):
            raise ValueError("auth_header must be 'bearer', 'api-key' or 'none'")
        check_url(self.base_url)
        if self.oauth and self.oauth.flow not in ("device", "pkce", "openrouter"):
            raise ValueError(f"unknown OAuth flow {self.oauth.flow!r}")


GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language.retriever",
]

PRESETS: dict[str, dict[str, Any]] = {
    "ollama": {
        "id": "ollama",
        "kind": "ollama",
        "label": "Ollama (on this computer)",
        "base_url": "http://localhost:11434",
    },
    "openai": {
        "id": "openai",
        "kind": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "id": "anthropic",
        "kind": "anthropic",
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "id": "gemini",
        "kind": "gemini",
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "env_key": "GEMINI_API_KEY",
        # Google sign-in needs an OAuth "Desktop app" client ID from Google Cloud.
        "oauth": {
            "flow": "pkce",
            "issuer": "https://accounts.google.com",
            "scopes": GOOGLE_SCOPES,
            "extra_params": {"access_type": "offline", "prompt": "consent"},
        },
    },
    "openrouter": {
        "id": "openrouter",
        "kind": "openai",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "oauth": {"flow": "openrouter"},
    },
}


# ---------------------------------------------------------------------------
# Wire clients
# ---------------------------------------------------------------------------


def _sse_data(resp: Any) -> Iterator[Any]:
    """Yield decoded JSON objects from ``data:`` lines of a server-sent event stream."""
    for raw in resp:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


class ChatClient:
    """Talks to one provider's HTTP API."""

    def __init__(self, cfg: ProviderConfig, headers: dict[str, str]) -> None:
        self.cfg = cfg
        self.headers = headers

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = True,
        temperature: float | None = None,
        timeout: float = 60.0,
    ) -> Iterator[str]:
        """Yield response text; one chunk when ``stream`` is False."""
        raise NotImplementedError


class OllamaClient(ChatClient):
    def list_models(self) -> list[str]:
        data = request_json("GET", f"{self.cfg.base_url}/api/tags", timeout=_LIST_TIMEOUT)
        return [m["name"] for m in data.get("models", []) if "name" in m]


class OpenAIClient(ChatClient):
    """OpenAI Chat Completions, and every compatible API (OpenRouter, Groq,
    Mistral, Together, Azure OpenAI v1, LM Studio, vLLM, LiteLLM ...)."""

    def list_models(self) -> list[str]:
        data = request_json(
            "GET", f"{self.cfg.base_url}/models", headers=self.headers, timeout=_LIST_TIMEOUT
        )
        return sorted(str(m["id"]) for m in data.get("data", []) if "id" in m)

    def chat(self, model, messages, stream=True, temperature=None, timeout=60.0):
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        if temperature is not None:
            body["temperature"] = temperature
        url = f"{self.cfg.base_url}/chat/completions"
        with request("POST", url, json_body=body, headers=self.headers, timeout=timeout) as r:
            if not stream:
                data = json.loads(r.read().decode())
                yield data["choices"][0]["message"].get("content") or ""
                return
            for obj in _sse_data(r):
                choices = obj.get("choices") or []
                if choices:
                    text = (choices[0].get("delta") or {}).get("content")
                    if text:
                        yield text


class AnthropicClient(ChatClient):
    def list_models(self) -> list[str]:
        data = request_json(
            "GET",
            f"{self.cfg.base_url}/models?limit=100",
            headers=self.headers,
            timeout=_LIST_TIMEOUT,
        )
        return [str(m["id"]) for m in data.get("data", []) if "id" in m]

    def chat(self, model, messages, stream=True, temperature=None, timeout=60.0):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] in ("user", "assistant")]
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "messages": turns,
            "stream": stream,
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        url = f"{self.cfg.base_url}/messages"
        with request("POST", url, json_body=body, headers=self.headers, timeout=timeout) as r:
            if not stream:
                data = json.loads(r.read().decode())
                yield "".join(
                    b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
                )
                return
            for obj in _sse_data(r):
                if obj.get("type") == "content_block_delta":
                    text = (obj.get("delta") or {}).get("text")
                    if text:
                        yield text
                elif obj.get("type") == "error":
                    msg = (obj.get("error") or {}).get("message", "stream error")
                    raise ProviderError(f"{self.cfg.label}: {msg}")


class GeminiClient(ChatClient):
    def list_models(self) -> list[str]:
        data = request_json(
            "GET",
            f"{self.cfg.base_url}/models?pageSize=200",
            headers=self.headers,
            timeout=_LIST_TIMEOUT,
        )
        out = []
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                out.append(str(m.get("name", "")).removeprefix("models/"))
        return [m for m in out if m]

    def chat(self, model, messages, stream=True, temperature=None, timeout=60.0):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] in ("user", "assistant")
        ]
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if temperature is not None:
            body["generationConfig"] = {"temperature": temperature}
        name = urllib.parse.quote(model.removeprefix("models/"), safe="-._")
        action = "streamGenerateContent?alt=sse" if stream else "generateContent"
        url = f"{self.cfg.base_url}/models/{name}:{action}"
        with request("POST", url, json_body=body, headers=self.headers, timeout=timeout) as r:
            objs = _sse_data(r) if stream else iter([json.loads(r.read().decode())])
            for obj in objs:
                for cand in obj.get("candidates", [])[:1]:
                    for part in (cand.get("content") or {}).get("parts", []):
                        if part.get("text"):
                            yield part["text"]


_CLIENTS: dict[str, type[ChatClient]] = {
    "ollama": OllamaClient,
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
    "gemini": GeminiClient,
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """Loads provider configs, resolves credentials and builds clients.

    Args:
        path: providers.json location.
        credentials: Keyring-backed secret store.
        ollama_url: Base URL for the built-in Ollama provider.
    """

    def __init__(self, path: Path, credentials: CredentialStore, ollama_url: str = "") -> None:
        self._path = path
        self._creds = credentials
        self._ollama_url = ollama_url
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._overrides: dict[str, dict[str, Any]] = {}
        self._providers: dict[str, ProviderConfig] = {}
        self._model_cache: tuple[float, dict[str, Any]] | None = None
        self.load()

    # -- persistence ---------------------------------------------------

    def load(self) -> None:
        overrides: dict[str, dict[str, Any]] = {}
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for entry in data.get("providers", []):
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                        overrides[entry["id"]] = entry
            except (OSError, ValueError) as e:
                log.warning("Ignoring unreadable %s: %s", self._path, e)
        with self._lock:
            self._overrides = overrides
            self._rebuild()

    def _rebuild(self) -> None:
        providers: dict[str, ProviderConfig] = {}
        for pid, preset in PRESETS.items():
            merged = {**preset, **self._overrides.get(pid, {}), "id": pid, "kind": preset["kind"]}
            if (
                pid == OLLAMA_ID
                and self._ollama_url
                and "base_url" not in self._overrides.get(pid, {})
            ):
                merged["base_url"] = self._ollama_url
            providers[pid] = ProviderConfig.from_dict(merged, builtin=True)
        for pid, entry in self._overrides.items():
            if pid in PRESETS:
                continue
            try:
                cfg = ProviderConfig.from_dict(entry)
                cfg.validate()
                providers[pid] = cfg
            except (KeyError, ValueError) as e:
                log.warning("Skipping invalid provider %r: %s", pid, e)
        self._providers = providers
        self._model_cache = None

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"providers": list(self._overrides.values())}, indent=2))
        tmp.replace(self._path)

    # -- queries -------------------------------------------------------

    @property
    def keyring_available(self) -> bool:
        return self._creds.available

    def get(self, provider_id: str) -> ProviderConfig:
        with self._lock:
            cfg = self._providers.get(provider_id)
        if cfg is None:
            raise ProviderError(f"unknown AI provider {provider_id!r}")
        return cfg

    def all(self) -> list[ProviderConfig]:
        with self._lock:
            return list(self._providers.values())

    def describe(self) -> list[dict[str, Any]]:
        """Provider list for the settings UI, with sign-in state (never secrets)."""
        out = []
        for cfg in self.all():
            secret = self._creds.get(cfg.id) if cfg.kind != "ollama" else {}
            has_env = bool(self._creds.api_key("", _env_key(cfg)))
            info = cfg.to_dict()
            info.update(
                {
                    "builtin": cfg.builtin,
                    "has_api_key": bool(secret.get("api_key")) or has_env,
                    "signed_in": bool(secret.get("access_token")),
                    "oauth_flow": cfg.oauth.flow if cfg.oauth else "",
                    "oauth_ready": bool(cfg.oauth and cfg.oauth.ready()),
                    "configured": self.is_configured(cfg, secret),
                }
            )
            info.pop("oauth", None)
            if cfg.oauth:
                # client IDs and endpoints are not secret; the UI edits them
                info["oauth"] = {
                    k: v for k, v in cfg.oauth.to_dict().items() if k != "client_secret"
                }
            out.append(info)
        return out

    def is_configured(self, cfg: ProviderConfig, secret: dict[str, Any] | None = None) -> bool:
        if cfg.kind == "ollama":
            return True
        if secret is None:
            secret = self._creds.get(cfg.id)
        return bool(
            secret.get("access_token")
            or secret.get("api_key")
            or self._creds.api_key("", _env_key(cfg))
            or cfg.auth_header == "none"
        )

    # -- mutation ------------------------------------------------------

    def upsert(self, data: dict[str, Any]) -> ProviderConfig:
        """Add or update a provider from a settings-UI dict.

        Changing where a provider sends requests (base URL, OAuth endpoints or
        client) forgets its stored credentials, so a key can never be
        redirected to a different server by editing the config.
        """
        pid = str(data.get("id", ""))
        with self._lock:
            old = self._providers.get(pid)
            if pid in PRESETS:
                allowed = {"label", "base_url", "models", "enabled", "oauth", "auth_header"}
                patch = {k: v for k, v in data.items() if k in allowed}
                override = {**self._overrides.get(pid, {}), **patch, "id": pid}
                candidate = {**PRESETS[pid], **override, "kind": PRESETS[pid]["kind"]}
            else:
                override = {k: v for k, v in data.items() if k in _FIELDS}
                candidate = override
            cfg = ProviderConfig.from_dict(candidate, builtin=pid in PRESETS)
            cfg.validate()
            if (
                old is not None
                and old.oauth
                and cfg.oauth
                and not cfg.oauth.client_secret
                and old.oauth.client_id == cfg.oauth.client_id
            ):
                # the UI never sees the client secret, so keep it across edits
                cfg.oauth.client_secret = old.oauth.client_secret
                override["oauth"] = cfg.oauth.to_dict()
            if old is not None and _destination(old) != _destination(cfg):
                self._creds.clear(pid)
            self._overrides[pid] = override
            self._save()
            self._rebuild()
            return self._providers[pid]

    def remove(self, provider_id: str) -> None:
        """Delete a custom provider, or reset a built-in one; forgets its credentials."""
        with self._lock:
            if provider_id not in self._providers:
                raise ProviderError(f"unknown AI provider {provider_id!r}")
            self._overrides.pop(provider_id, None)
            self._save()
            self._rebuild()
        self._creds.clear(provider_id)

    def set_api_key(self, provider_id: str, key: str) -> None:
        cfg = self.get(provider_id)
        if cfg.kind == "ollama":
            raise ProviderError("Ollama runs locally and needs no key")
        self._creds.update(provider_id, api_key=key.strip() or None)
        self._model_cache = None

    def store_tokens(self, provider_id: str, tokens: dict[str, Any]) -> None:
        self._creds.update(provider_id, **tokens)
        self._model_cache = None

    def sign_out(self, provider_id: str) -> None:
        self._creds.update(provider_id, access_token=None, refresh_token=None, expires_at=None)
        self._model_cache = None

    # -- credentials ---------------------------------------------------

    def _access_token(self, cfg: ProviderConfig) -> str:
        secret = self._creds.get(cfg.id)
        token = str(secret.get("access_token", ""))
        if not token:
            return ""
        expires_at = int(secret.get("expires_at", 0) or 0)
        if not expires_at or expires_at - 60 > time.time():
            return token
        refresh = str(secret.get("refresh_token", ""))
        if not refresh or not cfg.oauth:
            return ""
        with self._refresh_lock:
            # another thread may have refreshed while we waited
            secret = self._creds.get(cfg.id)
            if int(secret.get("expires_at", 0) or 0) - 60 > time.time():
                return str(secret.get("access_token", ""))
            try:
                tokens = refresh_tokens(cfg.oauth, refresh)
            except (OAuthError, HTTPRequestError, ValueError) as e:
                log.warning("Token refresh failed for %s: %s", cfg.id, e)
                raise ProviderError(
                    f"{cfg.label} sign-in expired. Sign in again in Settings > AI Models."
                ) from None
            self._creds.update(cfg.id, **tokens)
            return str(tokens["access_token"])

    def auth_headers(self, cfg: ProviderConfig) -> dict[str, str]:
        if cfg.kind == "ollama":
            return {}
        headers: dict[str, str] = {}
        if cfg.kind == "anthropic":
            headers["anthropic-version"] = ANTHROPIC_VERSION
        token = self._access_token(cfg)
        if token:
            headers["Authorization"] = f"Bearer {token}"
            return headers
        key = self._creds.api_key(cfg.id, _env_key(cfg))
        if not key and cfg.auth_header == "none":
            return headers
        if not key:
            raise ProviderError(
                f"{cfg.label} is not set up. Add an API key or sign in in Settings > AI Models."
            )
        if cfg.kind == "anthropic":
            headers["x-api-key"] = key
        elif cfg.kind == "gemini":
            headers["x-goog-api-key"] = key
        elif cfg.auth_header == "api-key":
            headers["api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def client(self, provider_id: str) -> ChatClient:
        cfg = self.get(provider_id)
        if not cfg.enabled:
            raise ProviderError(f"{cfg.label} is turned off in Settings > AI Models")
        return _CLIENTS[cfg.kind](cfg, self.auth_headers(cfg))

    # -- inference -----------------------------------------------------

    def chat(
        self,
        ref: str,
        messages: list[dict[str, str]],
        stream: bool = True,
        temperature: float | None = None,
        timeout: float = 60.0,
    ) -> Iterator[str]:
        """Run a chat on a cloud model reference, translating errors for the user."""
        provider_id, model = parse_model_ref(ref)
        client = self.client(provider_id)
        try:
            yield from client.chat(model, messages, stream, temperature, timeout)
        except HTTPRequestError as e:
            raise ProviderError(_friendly(client.cfg, e)) from None

    def list_models(self, refresh: bool = False) -> dict[str, Any]:
        """Return ``{"models": [...], "errors": {provider: msg}}`` across providers."""
        cached = self._model_cache
        if cached and not refresh and time.monotonic() - cached[0] < _MODEL_CACHE_TTL:
            return cached[1]
        targets = [c for c in self.all() if c.enabled and self.is_configured(c)]

        def fetch(cfg: ProviderConfig) -> tuple[ProviderConfig, list[str], str]:
            names = list(cfg.models)
            try:
                listed = _CLIENTS[cfg.kind](cfg, self.auth_headers(cfg)).list_models()
                names += [n for n in listed if n not in names]
                return cfg, names, ""
            except HTTPRequestError as e:
                return cfg, names, _friendly(cfg, e)
            except (ProviderError, ValueError, KeyError, TypeError, AttributeError) as e:
                return cfg, names, str(e)

        models: list[dict[str, str]] = []
        errors: dict[str, str] = {}
        if targets:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                for cfg, names, err in pool.map(fetch, targets):
                    for name in names:
                        models.append(
                            {
                                "id": model_ref(cfg.id, name),
                                "name": name,
                                "provider": cfg.id,
                                "provider_label": cfg.label,
                            }
                        )
                    if err:
                        errors[cfg.id] = err
        result = {"models": models, "errors": errors}
        self._model_cache = (time.monotonic(), result)
        return result


def _env_key(cfg: ProviderConfig) -> str:
    """The env var that may supply a key, only while the preset's own URL is in use."""
    preset = PRESETS.get(cfg.id)
    if not cfg.builtin or not preset or cfg.base_url != preset["base_url"].rstrip("/"):
        return ""
    return cfg.env_key


def _destination(cfg: ProviderConfig) -> tuple[Any, ...]:
    """Everything that decides which server receives this provider's credentials."""
    o = cfg.oauth
    oauth_dest = (
        (o.flow, o.client_id, o.issuer, o.authorize_url, o.token_url, o.device_url) if o else None
    )
    return (cfg.kind, cfg.base_url, cfg.auth_header, oauth_dest)


def _friendly(cfg: ProviderConfig, err: HTTPRequestError) -> str:
    if err.status in (401, 403):
        return f"{cfg.label} rejected the credentials. Check the API key or sign in again."
    if err.status == 404:
        return f"{cfg.label} does not know that model."
    if err.status == 429:
        return f"{cfg.label} rate limit or quota reached. Try again shortly."
    if err.status == 0 and cfg.kind == "ollama":
        return "Ollama is not running on this computer."
    if err.status == 0:
        return f"Cannot reach {cfg.label}. Check the network connection."
    return f"{cfg.label} returned an error ({err.status})."


__all__ = [
    "KINDS",
    "PRESETS",
    "ProviderConfig",
    "ProviderError",
    "ProviderRegistry",
    "model_ref",
    "parse_model_ref",
]
