"""Tests for how the Brain service talks to a local Ollama.

Covers the failures behind "Ollama is not loading": configured models that
were never pulled, retries on errors that cannot succeed, and timeouts shorter
than a cold model load.
"""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from services.axon_brain import brain_service as bs
from services.axon_brain.brain_service import BrainService


def _http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://localhost:11434/api/chat", code, "err", {}, io.BytesIO(json.dumps(body).encode())
    )


def _service(installed, config=None) -> BrainService:
    service = BrainService.__new__(BrainService)
    service.config = config or {
        "speed_model": "llama3.2:1b",
        "general_model": "qwen2.5:7b",
        "deep_model": "qwen2.5:14b",
    }
    service._installed_models = MagicMock(return_value=installed)
    return service


class TestRetryPolicy:
    def test_connection_refused_is_retried(self):
        assert bs._is_retryable(urllib.error.URLError(ConnectionRefusedError()))

    def test_http_error_is_not_retried(self):
        assert not bs._is_retryable(_http_error(404, {"error": "model not found"}))

    def test_timeout_is_not_retried(self):
        assert not bs._is_retryable(urllib.error.URLError(TimeoutError()))
        assert not bs._is_retryable(TimeoutError())

    def test_404_fails_without_sleeping(self):
        service = BrainService.__new__(BrainService)
        opener = MagicMock()
        opener.open.side_effect = _http_error(404, {"error": "model not found"})
        with patch.object(bs, "_LOCAL_OPENER", opener), patch.object(bs.time, "sleep") as sleep:
            with pytest.raises(urllib.error.HTTPError):
                service._http_post("http://localhost:11434/api/chat", {})
        assert opener.open.call_count == 1
        sleep.assert_not_called()

    def test_refused_connection_retries_then_succeeds(self):
        service = BrainService.__new__(BrainService)
        opener = MagicMock()
        ok = MagicMock()
        opener.open.side_effect = [urllib.error.URLError(ConnectionRefusedError()), ok]
        with patch.object(bs, "_LOCAL_OPENER", opener), patch.object(bs.time, "sleep"):
            assert service._http_get("http://localhost:11434/api/tags") is ok
        assert opener.open.call_count == 2


class TestErrorMessages:
    def test_missing_model_tells_user_how_to_pull(self):
        msg = bs._ollama_error_message(_http_error(404, {"error": "not found"}), "qwen2.5:7b")
        assert "ollama pull qwen2.5:7b" in msg

    def test_server_error_detail_is_shown(self):
        msg = bs._ollama_error_message(_http_error(500, {"error": "out of memory"}))
        assert "out of memory" in msg

    def test_not_running(self):
        msg = bs._ollama_error_message(urllib.error.URLError(ConnectionRefusedError()))
        assert "not running" in msg

    def test_timeout(self):
        assert "loading" in bs._ollama_error_message(urllib.error.URLError(TimeoutError()))


class TestModelResolution:
    def test_installed_model_is_kept(self):
        assert _service(["qwen2.5:7b"])._resolve_local_model("qwen2.5:7b") == "qwen2.5:7b"

    def test_latest_tag_matches_bare_name(self):
        assert _service(["mistral:latest"])._resolve_local_model("mistral") == "mistral"

    def test_missing_model_falls_back_to_installer_choice(self):
        service = _service(["llama3.2:3b", "nomic-embed-text:latest"])
        with patch.object(bs, "_read_installer_model", return_value="llama3.2:3b"):
            assert service._resolve_local_model("qwen2.5:7b") == "llama3.2:3b"

    def test_missing_model_falls_back_to_another_tier(self):
        service = _service(["llama3.2:1b"])
        with patch.object(bs, "_read_installer_model", return_value=""):
            assert service._resolve_local_model("qwen2.5:14b") == "llama3.2:1b"

    def test_embedding_models_are_never_chat_fallbacks(self):
        service = _service(["nomic-embed-text:latest", "phi3:mini"])
        with patch.object(bs, "_read_installer_model", return_value=""):
            assert service._resolve_local_model("qwen2.5:7b") == "phi3:mini"

    def test_only_embedding_model_keeps_original(self):
        service = _service(["nomic-embed-text:latest"])
        with patch.object(bs, "_read_installer_model", return_value=""):
            assert service._resolve_local_model("qwen2.5:7b") == "qwen2.5:7b"

    def test_unreachable_ollama_keeps_original(self):
        assert _service(None)._resolve_local_model("qwen2.5:7b") == "qwen2.5:7b"


class TestInstallerConfig:
    def test_reads_installer_default_model(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[ai]\ndefault_model = "llama3.2:3b"\n')
        with patch.object(bs, "INSTALLER_CONFIG_FILE", cfg):
            assert bs._read_installer_model() == "llama3.2:3b"

    def test_missing_or_bad_file_reads_empty(self, tmp_path):
        with patch.object(bs, "INSTALLER_CONFIG_FILE", tmp_path / "nope.toml"):
            assert bs._read_installer_model() == ""
        bad = tmp_path / "bad.toml"
        bad.write_text("not = [valid")
        with patch.object(bs, "INSTALLER_CONFIG_FILE", bad):
            assert bs._read_installer_model() == ""

    def test_first_run_uses_installer_model_for_every_tier(self, tmp_path):
        installer = tmp_path / "installer.toml"
        installer.write_text('[ai]\ndefault_model = "llama3.2:3b"\n')
        service = BrainService.__new__(BrainService)
        service._config_lock = bs.threading.RLock()
        with (
            patch.object(bs, "INSTALLER_CONFIG_FILE", installer),
            patch.object(bs, "CONFIG_FILE", tmp_path / "brain.toml"),
            patch.object(bs, "AXON_DIR", tmp_path),
        ):
            service.load_config()
        assert service.config == dict.fromkeys(bs.MODEL_TIERS, "llama3.2:3b")
        assert 'general_model = "llama3.2:3b"' in (tmp_path / "brain.toml").read_text()


class TestStreamErrors:
    def test_missing_model_surfaces_as_provider_error(self):
        service = _service(["llama3.2:3b"])
        service._resolve_local_model = lambda m: m
        service._http_post = MagicMock(side_effect=_http_error(404, {"error": "not found"}))
        tokens = service._ollama_stream("/api/chat", {"model": "qwen2.5:7b"}, lambda c: "")
        with pytest.raises(bs.ProviderError, match=r"ollama pull qwen2\.5:7b"):
            list(tokens)

    def test_in_stream_error_is_raised(self):
        service = _service(["llama3.2:3b"])
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.__iter__.return_value = iter([b'{"error": "model runner crashed"}\n'])
        service._http_post = MagicMock(return_value=resp)
        tokens = service._ollama_stream("/api/chat", {"model": "llama3.2:3b"}, lambda c: "")
        with pytest.raises(bs.ProviderError, match="runner crashed"):
            list(tokens)

    def test_stream_uses_long_first_byte_timeout(self):
        service = _service(["llama3.2:3b"])
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.__iter__.return_value = iter([b'{"message": {"content": "hi"}}\n'])
        service._http_post = MagicMock(return_value=resp)
        out = list(
            service._ollama_stream(
                "/api/chat", {"model": "llama3.2:3b"}, lambda c: c["message"]["content"]
            )
        )
        assert out == ["hi"]
        assert service._http_post.call_args.kwargs["timeout"] == bs._OLLAMA_LOAD_TIMEOUT


def test_ollama_service_stays_on_and_models_unload_when_idle():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "build/config/install-ollama.sh").read_text()
    assert "WantedBy=multi-user.target" in text and "systemctl enable --now ollama.service" in text
    assert 'Environment="OLLAMA_KEEP_ALIVE=5m"' in text
