"""OAuth 2.0 sign-in for AI providers that allow it.

Three flows are supported:

* ``device`` — Device Authorization Grant (RFC 8628). The user opens a URL on
  any device and types a short code. Used for company identity providers such
  as Microsoft Entra ID (Azure OpenAI) or Okta-fronted gateways.
* ``pkce`` — Authorization Code with PKCE (RFC 7636) and a loopback redirect
  (RFC 8252). The browser signs in and redirects to ``127.0.0.1``. Used for
  Google (Gemini) and most OIDC providers.
* ``openrouter`` — OpenRouter's PKCE variant, which returns an API key instead
  of an access token.

Endpoints come from the provider config, or from OIDC discovery when only an
``issuer`` is set.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from http_util import HTTPRequestError, check_url, request_json

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
OPENROUTER_AUTH_URL = "https://openrouter.ai/auth"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/keys"
LOGIN_TIMEOUT = 300.0  # seconds the user has to finish signing in


class OAuthError(RuntimeError):
    """Sign-in failed or was cancelled."""


@dataclass
class OAuthConfig:
    """How a provider signs users in. Mirrors the ``oauth`` block of providers.json."""

    flow: str  # "device" | "pkce" | "openrouter"
    client_id: str = ""
    client_secret: str = ""  # only for "installed app" clients where it is not secret
    issuer: str = ""
    authorize_url: str = ""
    token_url: str = ""
    device_url: str = ""
    scopes: list[str] = field(default_factory=list)
    extra_params: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthConfig:
        return cls(
            flow=str(data.get("flow", "")),
            client_id=str(data.get("client_id", "")),
            client_secret=str(data.get("client_secret", "")),
            issuer=str(data.get("issuer", "")),
            authorize_url=str(data.get("authorize_url", "")),
            token_url=str(data.get("token_url", "")),
            device_url=str(data.get("device_url", "")),
            scopes=[str(s) for s in data.get("scopes", [])],
            extra_params={str(k): str(v) for k, v in data.get("extra_params", {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"flow": self.flow}
        for key in (
            "client_id",
            "client_secret",
            "issuer",
            "authorize_url",
            "token_url",
            "device_url",
        ):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.scopes:
            out["scopes"] = list(self.scopes)
        if self.extra_params:
            out["extra_params"] = dict(self.extra_params)
        return out

    def ready(self) -> bool:
        """True when sign-in can start (OpenRouter needs no client registration)."""
        if self.flow == "openrouter":
            return True
        if self.flow not in ("device", "pkce") or not self.client_id:
            return False
        return bool(self.issuer or self.token_url)


def pkce_pair() -> tuple[str, str]:
    """Return a (code_verifier, S256 code_challenge) pair."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def resolve_endpoints(cfg: OAuthConfig) -> OAuthConfig:
    """Fill missing endpoints from ``{issuer}/.well-known/openid-configuration``."""
    if not cfg.issuer:
        return cfg
    need = not cfg.token_url or (
        (cfg.flow == "device" and not cfg.device_url)
        or (cfg.flow == "pkce" and not cfg.authorize_url)
    )
    if not need:
        return cfg
    cfg = dataclasses.replace(cfg)
    issuer = check_url(cfg.issuer, allow_local_http=False).rstrip("/")
    meta = request_json("GET", f"{issuer}/.well-known/openid-configuration", timeout=15.0)
    if not isinstance(meta, dict):
        raise OAuthError("identity provider returned no discovery document")
    cfg.token_url = cfg.token_url or str(meta.get("token_endpoint", ""))
    cfg.authorize_url = cfg.authorize_url or str(meta.get("authorization_endpoint", ""))
    cfg.device_url = cfg.device_url or str(meta.get("device_authorization_endpoint", ""))
    return cfg


def _token_response(data: Any) -> dict[str, Any]:
    """Normalize a token endpoint response into what the keyring stores."""
    if not isinstance(data, dict) or not data.get("access_token"):
        raise OAuthError("token endpoint returned no access token")
    out: dict[str, Any] = {"access_token": str(data["access_token"])}
    if data.get("refresh_token"):
        out["refresh_token"] = str(data["refresh_token"])
    if data.get("expires_in"):
        out["expires_at"] = int(time.time()) + int(data["expires_in"])
    return out


def _error_code(err: HTTPRequestError) -> str:
    try:
        return str(json.loads(err.body).get("error", ""))
    except (ValueError, AttributeError):
        return ""


def _describe(err: HTTPRequestError) -> str:
    """The identity provider's own explanation, first line only, else the status."""
    try:
        body = json.loads(err.body)
        text = str(body.get("error_description") or body.get("error") or "")
    except (ValueError, AttributeError):
        text = ""
    return text.splitlines()[0][:200] if text else str(err)


def refresh_tokens(cfg: OAuthConfig, refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a new access token."""
    cfg = resolve_endpoints(cfg)
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    form["client_id"] = cfg.client_id
    if cfg.client_secret:
        form["client_secret"] = cfg.client_secret
    if cfg.scopes:
        form["scope"] = " ".join(cfg.scopes)
    tokens = _token_response(request_json("POST", cfg.token_url, form=form, timeout=20.0))
    tokens.setdefault("refresh_token", refresh_token)
    return tokens


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------


def device_flow(
    cfg: OAuthConfig,
    on_prompt: Callable[[dict[str, Any]], None],
    cancel: threading.Event,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run the device authorization grant and return tokens.

    Args:
        cfg: Provider OAuth config with ``device_url``/``token_url`` or ``issuer``.
        on_prompt: Called once with ``verification_uri`` and ``user_code`` to show.
        cancel: Set to abort polling.
        sleep: Injectable for tests.
    """
    cfg = resolve_endpoints(cfg)
    if not cfg.device_url or not cfg.token_url:
        raise OAuthError("this provider does not offer device sign-in")
    form = {"client_id": cfg.client_id, **cfg.extra_params}
    if cfg.scopes:
        form["scope"] = " ".join(cfg.scopes)
    start = request_json("POST", check_url(cfg.device_url, False), form=form, timeout=20.0)
    if not isinstance(start, dict) or "device_code" not in start:
        raise OAuthError("identity provider did not start device sign-in")
    on_prompt(
        {
            "verification_uri": start.get("verification_uri") or start.get("verification_url", ""),
            "verification_uri_complete": start.get("verification_uri_complete", ""),
            "user_code": start.get("user_code", ""),
        }
    )
    interval = float(start.get("interval", 5))
    deadline = time.monotonic() + min(float(start.get("expires_in", 900)), 900.0)
    poll = {"grant_type": DEVICE_GRANT, "device_code": start["device_code"]}
    poll["client_id"] = cfg.client_id
    if cfg.client_secret:
        poll["client_secret"] = cfg.client_secret
    while time.monotonic() < deadline:
        sleep(interval)
        if cancel.is_set():
            raise OAuthError("sign-in cancelled")
        try:
            data = request_json("POST", cfg.token_url, form=poll, timeout=20.0)
        except HTTPRequestError as e:
            code = _error_code(e)
            if code == "authorization_pending":
                continue
            if code == "slow_down":
                interval += 5
                continue
            if code == "access_denied":
                raise OAuthError("sign-in was declined") from None
            if code == "expired_token":
                break
            raise OAuthError(f"sign-in failed: {code or e}") from None
        return _token_response(data)
    raise OAuthError("sign-in code expired before it was used")


# ---------------------------------------------------------------------------
# Loopback PKCE flow
# ---------------------------------------------------------------------------


class _CallbackServer(http.server.HTTPServer):
    result: dict[str, str]
    done: threading.Event


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        self.server.result = {k: v[0] for k, v in query.items() if v}
        ok = "code" in self.server.result
        body = (
            "<h2>Signed in to Axon OS.</h2><p>You can close this tab.</p>"
            if ok
            else "<h2>Sign-in did not complete.</h2><p>Return to Axon Settings and try again.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())
        self.server.done.set()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # keep codes out of the journal


def _wait_for_callback(
    server: _CallbackServer, cancel: threading.Event, timeout: float
) -> dict[str, str]:
    server.timeout = 0.5
    deadline = time.monotonic() + timeout
    while not server.done.is_set():
        if cancel.is_set():
            raise OAuthError("sign-in cancelled")
        if time.monotonic() > deadline:
            raise OAuthError("sign-in timed out")
        server.handle_request()
    return server.result


def _start_server() -> _CallbackServer:
    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    server.result = {}
    server.done = threading.Event()
    return server


def pkce_flow(
    cfg: OAuthConfig,
    on_prompt: Callable[[dict[str, Any]], None],
    cancel: threading.Event,
    timeout: float = LOGIN_TIMEOUT,
) -> dict[str, Any]:
    """Run authorization code + PKCE with a loopback redirect and return tokens.

    For ``flow == "openrouter"`` the result is ``{"api_key": ...}``.
    """
    verifier, challenge = pkce_pair()
    server = _start_server()
    try:
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
        state = secrets.token_urlsafe(24)
        if cfg.flow == "openrouter":
            auth_url = (
                OPENROUTER_AUTH_URL
                + "?"
                + urllib.parse.urlencode(
                    {
                        "callback_url": redirect_uri,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                    }
                )
            )
        else:
            cfg = resolve_endpoints(cfg)
            if not cfg.authorize_url or not cfg.token_url:
                raise OAuthError("this provider does not offer browser sign-in")
            params = {
                "response_type": "code",
                "client_id": cfg.client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                **cfg.extra_params,
            }
            if cfg.scopes:
                params["scope"] = " ".join(cfg.scopes)
            auth_url = check_url(cfg.authorize_url, False) + "?" + urllib.parse.urlencode(params)
        on_prompt({"auth_url": auth_url})
        result = _wait_for_callback(server, cancel, timeout)
    finally:
        server.server_close()

    if "error" in result:
        raise OAuthError(f"sign-in failed: {result['error']}")
    code = result.get("code", "")
    if not code:
        raise OAuthError("sign-in returned no authorization code")

    if cfg.flow == "openrouter":
        # The code is bound to our code_challenge, so a forged callback cannot
        # be exchanged without the verifier; OpenRouter does not echo state.
        data = request_json(
            "POST",
            OPENROUTER_KEY_URL,
            json_body={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
            timeout=20.0,
        )
        if not isinstance(data, dict) or not data.get("key"):
            raise OAuthError("OpenRouter returned no key")
        return {"api_key": str(data["key"])}

    if not secrets.compare_digest(result.get("state", ""), state):
        raise OAuthError("sign-in response did not match this request")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "client_id": cfg.client_id,
    }
    if cfg.client_secret:
        form["client_secret"] = cfg.client_secret
    return _token_response(request_json("POST", cfg.token_url, form=form, timeout=20.0))


def run_flow(
    cfg: OAuthConfig,
    on_prompt: Callable[[dict[str, Any]], None],
    cancel: threading.Event,
) -> dict[str, Any]:
    """Run whichever flow *cfg* names."""
    if not cfg.ready():
        raise OAuthError("sign-in is not configured for this provider (missing client ID)")
    try:
        if cfg.flow == "device":
            return device_flow(cfg, on_prompt, cancel)
        return pkce_flow(cfg, on_prompt, cancel)
    except HTTPRequestError as e:
        raise OAuthError(f"sign-in failed: {_describe(e)}") from None
