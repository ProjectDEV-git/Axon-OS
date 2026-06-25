"""Tests for model_marketplace — validation, catalog, and helpers."""

from unittest.mock import MagicMock, patch

from services.axon_brain.model_marketplace import (
    DEFAULT_CATALOG,
    _http_get,
    _http_post,
    _validate_model_name,
)


class TestValidateModelName:
    def test_valid_names(self):
        assert _validate_model_name("llama3.2:3b") is True
        assert _validate_model_name("mistral:7b") is True
        assert _validate_model_name("nomic-embed-text") is True
        assert _validate_model_name("qwen2.5:7b") is True

    def test_invalid_names(self):
        assert _validate_model_name("") is False
        assert _validate_model_name(None) is False
        assert _validate_model_name(123) is False
        assert _validate_model_name("a" * 300) is False
        assert _validate_model_name("../etc/passwd") is False
        assert _validate_model_name("model;rm -rf /") is False

    def test_edge_cases(self):
        assert _validate_model_name("a") is True
        assert _validate_model_name("model/name:tag") is True


class TestDefaultCatalog:
    def test_catalog_is_non_empty(self):
        assert len(DEFAULT_CATALOG) > 0

    def test_catalog_entries_have_required_keys(self):
        for model in DEFAULT_CATALOG:
            assert "name" in model
            assert "family" in model
            assert "description" in model
            assert "use_case" in model

    def test_catalog_has_embedding_model(self):
        names = [m["name"] for m in DEFAULT_CATALOG]
        assert "nomic-embed-text" in names

    def test_use_cases_are_valid(self):
        valid_use_cases = {"speed", "general", "code", "embedding"}
        for model in DEFAULT_CATALOG:
            assert model["use_case"] in valid_use_cases


class TestHttpHelpers:
    def test_http_get_success(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_open.return_value = mock_resp
            result = _http_get("http://localhost:11434/api/tags")
        assert result is not None

    def test_http_get_failure(self):
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = _http_get("http://localhost:11434/api/tags")
        assert result is None

    def test_http_post_success(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_open.return_value = mock_resp
            result = _http_post("http://localhost:11434/api/pull", {"name": "llama3.2:3b"})
        assert result is not None

    def test_http_post_failure(self):
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            result = _http_post("http://localhost:11434/api/pull", {"name": "test"})
        assert result is None
