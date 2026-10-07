"""Tests for any-model support: provider registry, cloud clients, OAuth and Brain wiring."""

import base64
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

import oauth
import providers
import pytest
from credentials import CredentialError, CredentialStore, MemoryBackend
from http_util import HTTPRequestError, check_url
from oauth import OAuthConfig, OAuthError
from providers import ProviderError, ProviderRegistry, model_ref, parse_model_ref

from services.axon_brain.brain_service import BrainService


class FakeResponse:
    """Stands in for a urllib response: iterable lines, read(), context manager."""

    def __init__(self, lines=None, body=None):
        self._lines = [line.encode() if isinstance(line, str) else line for line in lines or []]
        self._body = json.dumps(body).encode() if body is not None else b""

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def sse(*objs):
    return [f"data: {json.dumps(o)}\n" for o in objs] + ["data: [DONE]\n"]


@pytest.fixture
def creds():
    return CredentialStore(MemoryBackend())


@pytest.fixture
def registry(tmp_path, creds, monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return ProviderRegistry(tmp_path / "providers.json", creds, "http://localhost:11434")


# ---------------------------------------------------------------------------
# Model references and URLs
# ---------------------------------------------------------------------------


class TestModelRefs:
    def test_bare_name_is_ollama(self):
        assert parse_model_ref("qwen2.5:7b") == ("ollama", "qwen2.5:7b")
        assert parse_model_ref("library/llama3") == ("ollama", "library/llama3")

    def test_provider_ref(self):
        assert parse_model_ref("@openai/gpt-4.1") == ("openai", "gpt-4.1")
        assert parse_model_ref("@openrouter/anthropic/claude-3.5-sonnet") == (
            "openrouter",
            "anthropic/claude-3.5-sonnet",
        )

    @pytest.mark.parametrize("ref", ["@", "@openai", "@openai/", "@Open/x", "@bad_id/x"])
    def test_malformed_refs(self, ref):
        with pytest.raises(ValueError):
            parse_model_ref(ref)

    def test_model_ref_round_trip(self):
        assert model_ref("ollama", "llama3") == "llama3"
        assert model_ref("gemini", "gemini-2.5-pro") == "@gemini/gemini-2.5-pro"


class TestCheckUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "http://localhost:11434",
            "http://127.0.0.1:1234/v1",
            "http://192.168.1.20:8000/v1",
            "http://host.docker.internal:11434",
        ],
    )
    def test_allowed(self, url):
        assert check_url(url) == url

    @pytest.mark.parametrize(
        "url", ["http://api.example.com/v1", "file:///etc/passwd", "ftp://x", "https://"]
    )
    def test_rejected(self, url):
        with pytest.raises(ValueError):
            check_url(url)

    def test_local_http_can_be_disallowed(self):
        with pytest.raises(ValueError):
            check_url("http://localhost:8080", allow_local_http=False)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class TestCredentialStore:
    def test_update_merges_and_removes(self, creds):
        creds.update("openai", api_key="sk-1")
        creds.update("openai", access_token="tok")
        assert creds.get("openai") == {"api_key": "sk-1", "access_token": "tok"}
        creds.update("openai", api_key=None)
        assert creds.get("openai") == {"access_token": "tok"}

    def test_empty_secret_deletes_entry(self, creds):
        creds.update("x", api_key="k")
        creds.update("x", api_key=None)
        assert "x" not in creds._backend.data

    def test_env_fallback(self, creds, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "from-env")
        assert creds.api_key("openai", "SOME_KEY") == "from-env"
        creds.update("openai", api_key="from-keyring")
        assert creds.api_key("openai", "SOME_KEY") == "from-keyring"

    def test_no_backend_refuses_to_store(self):
        store = CredentialStore(None)
        assert not store.available
        assert store.get("openai") == {}
        with pytest.raises(CredentialError):
            store.update("openai", api_key="k")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_presets_loaded(self, registry):
        ids = {p.id for p in registry.all()}
        assert {"ollama", "openai", "anthropic", "gemini", "openrouter"} <= ids
        assert registry.get("ollama").base_url == "http://localhost:11434"

    def test_add_custom_provider_persists(self, registry, tmp_path, creds):
        registry.upsert(
            {
                "id": "work",
                "kind": "openai",
                "label": "Work gateway",
                "base_url": "https://ai.corp.example/v1",
                "models": ["gpt-4o"],
            }
        )
        again = ProviderRegistry(tmp_path / "providers.json", creds)
        assert again.get("work").label == "Work gateway"
        assert again.get("work").models == ["gpt-4o"]

    def test_custom_provider_requires_https(self, registry):
        with pytest.raises(ValueError):
            registry.upsert(
                {"id": "x", "kind": "openai", "label": "X", "base_url": "http://evil.example"}
            )

    def test_custom_provider_cannot_borrow_env_keys(self, registry, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        registry.upsert(
            {
                "id": "x",
                "kind": "openai",
                "label": "X",
                "base_url": "https://evil.example/v1",
                "env_key": "OPENAI_API_KEY",
            }
        )
        with pytest.raises(ProviderError):
            registry.auth_headers(registry.get("x"))

    def test_env_key_only_for_preset_url(self, registry, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        assert registry.auth_headers(registry.get("openai"))["Authorization"] == "Bearer sk-env"
        registry.upsert({"id": "openai", "base_url": "https://proxy.example/v1"})
        with pytest.raises(ProviderError):
            registry.auth_headers(registry.get("openai"))

    def test_changing_destination_forgets_credentials(self, registry, creds):
        registry.set_api_key("openai", "sk-1")
        registry.upsert({"id": "openai", "label": "My OpenAI"})
        assert creds.get("openai") == {"api_key": "sk-1"}
        registry.upsert({"id": "openai", "base_url": "https://other.example/v1"})
        assert creds.get("openai") == {}

    def test_builtin_kind_cannot_change(self, registry):
        registry.upsert({"id": "anthropic", "kind": "openai"})
        assert registry.get("anthropic").kind == "anthropic"

    def test_remove_resets_builtin_and_clears_key(self, registry, creds):
        registry.upsert({"id": "openai", "base_url": "https://other.example/v1"})
        registry.set_api_key("openai", "sk")
        registry.remove("openai")
        assert registry.get("openai").base_url == "https://api.openai.com/v1"
        assert creds.get("openai") == {}

    def test_client_secret_kept_across_edits(self, registry):
        oauth_cfg = {
            "flow": "pkce",
            "issuer": "https://accounts.google.com",
            "client_id": "cid",
            "client_secret": "csecret",
        }
        registry.upsert({"id": "gemini", "oauth": oauth_cfg})
        shown = next(p for p in registry.describe() if p["id"] == "gemini")
        assert "client_secret" not in shown["oauth"]
        registry.upsert({"id": "gemini", "label": "Gemini", "oauth": shown["oauth"]})
        assert registry.get("gemini").oauth.client_secret == "csecret"

    def test_describe_never_leaks_secrets(self, registry):
        registry.set_api_key("anthropic", "sk-ant-secret")
        text = json.dumps(registry.describe())
        assert "sk-ant-secret" not in text
        info = next(p for p in registry.describe() if p["id"] == "anthropic")
        assert info["has_api_key"] and info["configured"]

    def test_auth_headers_per_kind(self, registry):
        registry.set_api_key("anthropic", "a")
        registry.set_api_key("gemini", "g")
        registry.upsert(
            {
                "id": "azure",
                "kind": "openai",
                "label": "Azure",
                "base_url": "https://r.openai.azure.com/openai/v1",
                "auth_header": "api-key",
            }
        )
        registry.set_api_key("azure", "z")
        assert registry.auth_headers(registry.get("anthropic"))["x-api-key"] == "a"
        assert registry.auth_headers(registry.get("gemini")) == {"x-goog-api-key": "g"}
        assert registry.auth_headers(registry.get("azure")) == {"api-key": "z"}

    def test_local_server_needs_no_key(self, registry):
        registry.upsert(
            {
                "id": "lmstudio",
                "kind": "openai",
                "label": "LM Studio",
                "base_url": "http://localhost:1234/v1",
                "auth_header": "none",
            }
        )
        assert registry.auth_headers(registry.get("lmstudio")) == {}

    def test_oauth_token_preferred_over_key(self, registry, creds):
        creds.update("gemini", api_key="g", access_token="tok", expires_at=int(time.time()) + 3600)
        assert registry.auth_headers(registry.get("gemini")) == {"Authorization": "Bearer tok"}

    def test_expired_token_refreshed(self, registry, creds):
        registry.upsert(
            {
                "id": "work",
                "kind": "openai",
                "label": "Work",
                "base_url": "https://ai.corp.example/v1",
                "oauth": {"flow": "device", "client_id": "c", "token_url": "https://t/x"},
            }
        )
        creds.update("work", access_token="old", refresh_token="r", expires_at=1)
        new = {"access_token": "new", "refresh_token": "r2", "expires_at": int(time.time()) + 3600}
        with patch.object(providers, "refresh_tokens", return_value=new) as refresh:
            headers = registry.auth_headers(registry.get("work"))
        refresh.assert_called_once()
        assert headers == {"Authorization": "Bearer new"}
        assert creds.get("work")["refresh_token"] == "r2"

    def test_failed_refresh_asks_to_sign_in_again(self, registry, creds):
        registry.upsert(
            {
                "id": "work",
                "kind": "openai",
                "label": "Work",
                "base_url": "https://ai.corp.example/v1",
                "oauth": {"flow": "device", "client_id": "c", "token_url": "https://t/x"},
            }
        )
        creds.update("work", access_token="old", refresh_token="r", expires_at=1)
        with patch.object(providers, "refresh_tokens", side_effect=OAuthError("invalid_grant")):
            with pytest.raises(ProviderError, match="Sign in again"):
                registry.auth_headers(registry.get("work"))

    def test_unconfigured_provider_explains_setup(self, registry):
        with pytest.raises(ProviderError, match="not set up"):
            list(registry.chat("@openai/gpt-4.1", [{"role": "user", "content": "hi"}]))

    def test_list_models_aggregates_and_reports_errors(self, registry):
        registry.set_api_key("openai", "sk")
        registry.set_api_key("anthropic", "sk")

        def fake(method, url, **kw):
            if "11434" in url:
                return {"models": [{"name": "llama3:8b"}]}
            if "openai.com" in url:
                return {"data": [{"id": "gpt-4.1"}, {"id": "gpt-4o"}]}
            raise HTTPRequestError("HTTP 401", 401)

        with patch.object(providers, "request_json", side_effect=fake):
            result = registry.list_models(refresh=True)
        ids = {m["id"] for m in result["models"]}
        assert {"llama3:8b", "@openai/gpt-4.1", "@openai/gpt-4o"} <= ids
        assert "rejected the credentials" in result["errors"]["anthropic"]
        assert "gemini" not in result["errors"]  # not configured, not queried


# ---------------------------------------------------------------------------
# Wire clients
# ---------------------------------------------------------------------------

MSGS = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
    {"role": "user", "content": "again"},
]


class TestClients:
    def test_openai_stream(self, registry):
        registry.set_api_key("openai", "sk")
        lines = sse(
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
        )
        with patch.object(providers, "request", return_value=FakeResponse(lines)) as req:
            out = "".join(registry.chat("@openai/gpt-4.1", MSGS))
        assert out == "Hello"
        args, kwargs = req.call_args
        assert args[1] == "https://api.openai.com/v1/chat/completions"
        assert kwargs["json_body"]["messages"] == MSGS
        assert kwargs["headers"]["Authorization"] == "Bearer sk"

    def test_anthropic_stream_moves_system_prompt(self, registry):
        registry.set_api_key("anthropic", "sk")
        lines = [
            "event: content_block_delta\n",
            *sse(
                {"type": "message_start"},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
            ),
        ]
        with patch.object(providers, "request", return_value=FakeResponse(lines)) as req:
            out = "".join(registry.chat("@anthropic/claude-sonnet-4-5", MSGS))
        assert out == "Hi"
        body = req.call_args.kwargs["json_body"]
        assert body["system"] == "be brief"
        assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
        assert body["max_tokens"] > 0
        assert req.call_args.kwargs["headers"]["anthropic-version"]

    def test_anthropic_stream_error_event(self, registry):
        registry.set_api_key("anthropic", "sk")
        lines = sse({"type": "error", "error": {"message": "overloaded"}})
        with patch.object(providers, "request", return_value=FakeResponse(lines)):
            with pytest.raises(ProviderError, match="overloaded"):
                list(registry.chat("@anthropic/claude-sonnet-4-5", MSGS))

    def test_gemini_stream(self, registry):
        registry.set_api_key("gemini", "g")
        lines = sse({"candidates": [{"content": {"parts": [{"text": "Yo"}]}}]})
        with patch.object(providers, "request", return_value=FakeResponse(lines)) as req:
            out = "".join(registry.chat("@gemini/gemini-2.5-flash", MSGS))
        assert out == "Yo"
        url = req.call_args.args[1]
        assert url.endswith("/models/gemini-2.5-flash:streamGenerateContent?alt=sse")
        body = req.call_args.kwargs["json_body"]
        assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]

    def test_non_stream(self, registry):
        registry.set_api_key("openai", "sk")
        resp = FakeResponse(body={"choices": [{"message": {"content": "done"}}]})
        with patch.object(providers, "request", return_value=resp):
            assert "".join(registry.chat("@openai/gpt-4.1", MSGS, stream=False)) == "done"

    def test_http_errors_become_friendly(self, registry):
        registry.set_api_key("openai", "sk")
        with patch.object(providers, "request", side_effect=HTTPRequestError("x", 429)):
            with pytest.raises(ProviderError, match="rate limit"):
                list(registry.chat("@openai/gpt-4.1", MSGS))

    def test_gemini_list_filters_chat_models(self, registry):
        registry.set_api_key("gemini", "g")
        data = {
            "models": [
                {
                    "name": "models/gemini-2.5-pro",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }
        with patch.object(providers, "request_json", return_value=data):
            client = registry.client("gemini")
            assert client.list_models() == ["gemini-2.5-pro"]


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


class TestOAuth:
    def test_pkce_pair(self):
        verifier, challenge = oauth.pkce_pair()
        assert 43 <= len(verifier) <= 128
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        assert challenge == expected.rstrip(b"=").decode()

    def test_ready(self):
        assert OAuthConfig(flow="openrouter").ready()
        assert not OAuthConfig(flow="pkce", issuer="https://accounts.google.com").ready()
        assert OAuthConfig(flow="device", client_id="c", issuer="https://x").ready()
        assert not OAuthConfig(flow="magic", client_id="c", issuer="https://x").ready()

    def test_discovery_fills_endpoints_without_mutating(self):
        cfg = OAuthConfig(flow="device", client_id="c", issuer="https://login.example/t/v2.0")
        meta = {
            "token_endpoint": "https://login.example/token",
            "device_authorization_endpoint": "https://login.example/device",
        }
        with patch.object(oauth, "request_json", return_value=meta) as req:
            resolved = oauth.resolve_endpoints(cfg)
        assert req.call_args.args[1] == (
            "https://login.example/t/v2.0/.well-known/openid-configuration"
        )
        assert resolved.device_url == "https://login.example/device"
        assert cfg.device_url == ""

    def _device_cfg(self):
        return OAuthConfig(
            flow="device",
            client_id="cid",
            device_url="https://idp.example/device",
            token_url="https://idp.example/token",
            scopes=["api://x/.default", "offline_access"],
        )

    def test_device_flow_polls_until_approved(self):
        pending = HTTPRequestError("x", 400, json.dumps({"error": "authorization_pending"}))
        slow = HTTPRequestError("x", 400, json.dumps({"error": "slow_down"}))
        responses = [
            {
                "device_code": "dc",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://idp.example/activate",
                "interval": 1,
            },
            pending,
            slow,
            {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        ]

        def fake(method, url, **kw):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        prompts, sleeps = [], []
        with patch.object(oauth, "request_json", side_effect=fake):
            tokens = oauth.device_flow(
                self._device_cfg(), prompts.append, threading.Event(), sleep=sleeps.append
            )
        assert prompts[0]["user_code"] == "ABCD-EFGH"
        assert tokens["access_token"] == "at" and tokens["refresh_token"] == "rt"
        assert tokens["expires_at"] > time.time()
        assert sleeps == [1.0, 1.0, 6.0]  # slow_down adds five seconds

    def test_device_flow_denied(self):
        denied = HTTPRequestError("x", 400, json.dumps({"error": "access_denied"}))
        responses = [{"device_code": "dc", "user_code": "U", "interval": 0}, denied]

        def fake(method, url, **kw):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(oauth, "request_json", side_effect=fake):
            with pytest.raises(OAuthError, match="declined"):
                oauth.device_flow(
                    self._device_cfg(), lambda _i: None, threading.Event(), sleep=lambda _s: None
                )

    def _run_browser_flow(self, cfg, exchange_result, callback):
        """Run pkce_flow in a thread and answer its loopback redirect with *callback*."""
        prompt = {}
        got = threading.Event()

        def on_prompt(info):
            prompt.update(info)
            got.set()

        result = {}

        def run():
            try:
                result["tokens"] = oauth.pkce_flow(cfg, on_prompt, threading.Event(), timeout=10)
            except Exception as e:
                result["error"] = e

        with patch.object(oauth, "request_json", return_value=exchange_result) as req:
            t = threading.Thread(target=run)
            t.start()
            assert got.wait(5)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(prompt["auth_url"]).query)
            redirect = query.get("redirect_uri", query.get("callback_url"))[0]
            params = callback(query)
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            direct.open(f"{redirect}?{urllib.parse.urlencode(params)}", timeout=5)
            t.join(5)
        return result, req, query

    def test_pkce_flow_exchanges_code(self):
        cfg = OAuthConfig(
            flow="pkce",
            client_id="cid",
            authorize_url="https://idp.example/authorize",
            token_url="https://idp.example/token",
            scopes=["openid"],
        )
        result, req, query = self._run_browser_flow(
            cfg,
            {"access_token": "at", "expires_in": 60},
            lambda q: {"code": "the-code", "state": q["state"][0]},
        )
        assert result["tokens"]["access_token"] == "at"
        form = req.call_args.kwargs["form"]
        assert form["code"] == "the-code"
        assert form["grant_type"] == "authorization_code"
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(form["code_verifier"].encode()).digest()
        )
        assert query["code_challenge"][0] == challenge.rstrip(b"=").decode()

    def test_pkce_flow_rejects_wrong_state(self):
        cfg = OAuthConfig(
            flow="pkce",
            client_id="cid",
            authorize_url="https://idp.example/authorize",
            token_url="https://idp.example/token",
        )
        result, req, _q = self._run_browser_flow(
            cfg, {"access_token": "at"}, lambda q: {"code": "c", "state": "forged"}
        )
        assert isinstance(result["error"], OAuthError)
        req.assert_not_called()

    def test_openrouter_flow_returns_api_key(self):
        result, req, query = self._run_browser_flow(
            OAuthConfig(flow="openrouter"), {"key": "sk-or-1"}, lambda q: {"code": "c"}
        )
        assert result["tokens"] == {"api_key": "sk-or-1"}
        assert req.call_args.args[1] == oauth.OPENROUTER_KEY_URL
        assert req.call_args.kwargs["json_body"]["code"] == "c"
        assert query["code_challenge_method"] == ["S256"]

    def test_run_flow_requires_client_id(self):
        with pytest.raises(OAuthError, match="client ID"):
            oauth.run_flow(
                OAuthConfig(flow="pkce", issuer="https://accounts.google.com"),
                lambda _i: None,
                threading.Event(),
            )


# ---------------------------------------------------------------------------
# Brain service wiring
# ---------------------------------------------------------------------------


def _brain(registry):
    service = BrainService.__new__(BrainService)
    service.providers = registry
    service._config_lock = threading.RLock()
    service.config = {"speed_model": "a", "general_model": "b", "deep_model": "c"}
    service.save_config = MagicMock()
    service._streams_lock = threading.Lock()
    service._active_streams = {}
    service._token_buffer = MagicMock()
    service._oauth_lock = threading.Lock()
    service._oauth_sessions = {}
    return service


class TestBrainWiring:
    def test_validate_model_name_accepts_refs(self):
        assert BrainService._validate_model_name("@openai/gpt-4.1") is True
        assert BrainService._validate_model_name("@openrouter/meta-llama/llama-3-70b") is True
        assert BrainService._validate_model_name("@Bad/x") is False
        assert BrainService._validate_model_name("@openai/") is False
        assert BrainService._validate_model_name("@openai/../x") is False
        assert BrainService._validate_model_name("@openai/a b") is False

    def test_local_name(self):
        assert BrainService._local_name("@ollama/llama3") == "llama3"
        assert BrainService._local_name("@openai/gpt-4.1") == "@openai/gpt-4.1"
        assert BrainService._is_cloud("@openai/gpt-4.1")
        assert not BrainService._is_cloud("llama3")

    def test_set_model_all_tiers(self, registry):
        service = _brain(registry)
        with patch("services.axon_brain.brain_service.AIRouter") as router:
            result = json.loads(BrainService.SetModel(service, "all", "@anthropic/claude-x"))
        assert result["ok"]
        assert set(result["models"].values()) == {"@anthropic/claude-x"}
        service.save_config.assert_called_once()
        router.assert_called_once_with(service.config)

    def test_set_model_rejects_unknown_provider_and_tier(self, registry):
        service = _brain(registry)
        assert not json.loads(BrainService.SetModel(service, "general", "@nope/x"))["ok"]
        assert not json.loads(BrainService.SetModel(service, "turbo", "llama3"))["ok"]

    def test_complete_routes_cloud_models(self, registry):
        service = _brain(registry)
        registry.chat = MagicMock(return_value=iter(["Co", "de\x00"]))
        out = service._complete("@openai/gpt-4.1", "sys", "classify", temperature=0.1)
        assert out == "Code"
        msgs = registry.chat.call_args.args[1]
        assert msgs == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "classify"},
        ]

    def test_stream_reports_provider_errors(self, registry):
        service = _brain(registry)
        service._active_streams["tx"] = threading.Event()

        def boom():
            yield "partial"
            raise ProviderError("OpenAI rejected the credentials.")

        with patch("services.axon_brain.brain_service.GLib") as glib:
            service._run_stream("tx", boom(), "Chat")
        args = glib.idle_add.call_args.args
        assert args[1:] == ("tx", False, "OpenAI rejected the credentials.")
        assert "tx" not in service._active_streams

    def test_stream_saves_reply(self, registry):
        service = _brain(registry)
        saved = []
        with patch("services.axon_brain.brain_service.GLib"):
            service._run_stream("tx", iter(["a", "b"]), "Chat", on_done=saved.append)
        assert saved == ["ab"]

    def test_sign_in_stores_tokens(self, registry, creds):
        service = _brain(registry)
        registry.upsert({"id": "openrouter", "label": "OpenRouter"})
        with (
            patch.object(oauth, "run_flow", return_value={"api_key": "sk-or"}),
            patch("services.axon_brain.brain_service.GLib"),
            patch("services.axon_brain.brain_service.threading.Thread") as thread,
        ):
            assert json.loads(BrainService.StartSignIn(service, "openrouter"))["ok"]
            target = thread.call_args.kwargs["target"]
            target(*thread.call_args.kwargs["args"])
        assert creds.get("openrouter") == {"api_key": "sk-or"}
        assert json.loads(BrainService.GetSignInStatus(service, "openrouter")) == {"state": "done"}

    def test_sign_in_needs_configuration(self, registry):
        service = _brain(registry)
        result = json.loads(BrainService.StartSignIn(service, "openai"))
        assert not result["ok"]
