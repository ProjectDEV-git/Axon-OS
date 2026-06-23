"""Tests for Phase 4 modules: marketplace, router, telemetry, search, voice."""

import json
from unittest.mock import patch

# ---------------------------------------------------------------------------
# AI Router tests
# ---------------------------------------------------------------------------


class TestAIRouter:
    def test_import(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter({"speed_model": "a", "general_model": "b", "deep_model": "c"})
        assert router is not None

    def test_classify_short_as_speed(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter()
        assert router.classify_task("yes") == "speed"
        assert router.classify_task("open files") == "speed"
        assert router.classify_task("hi") == "speed"

    def test_classify_code_as_deep(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter()
        assert router.classify_task("Write a Python function to parse CSV files") == "deep"
        assert router.classify_task("Fix this code bug in my script") == "deep"

    def test_classify_question_as_general(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter()
        assert router.classify_task("Explain how Linux kernel scheduling works") == "general"
        assert router.classify_task("Tell me about quantum computing") == "general"

    def test_classify_search_as_embedding(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter()
        assert router.classify_task("search files") == "embedding"
        assert router.classify_task("find similar documents") == "embedding"

    def test_select_model_explicit(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter()
        model, reason = router.select_model("hello", explicit_model="my-model")
        assert model == "my-model"
        assert reason == "user-selected"

    def test_select_model_auto(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter({
            "speed_model": "fast",
            "general_model": "gen",
            "deep_model": "deep",
        })
        model, _reason = router.select_model("hi")
        assert model == "fast"

        model, _reason = router.select_model("Explain quantum mechanics in detail")
        assert model in ("gen", "deep")

    def test_get_routing_info(self):
        from services.axon_brain.ai_router import AIRouter

        router = AIRouter()
        info = router.get_routing_info()
        assert "speed_model" in info
        assert "general_model" in info
        assert "deep_model" in info
        assert "embedding_model" in info


# ---------------------------------------------------------------------------
# Telemetry tests
# ---------------------------------------------------------------------------


class TestTelemetry:
    def test_opt_in_out(self, tmp_path):
        from services.telemetry import Telemetry

        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            assert not t.is_enabled
            t.opt_in()
            assert t.is_enabled
            t.opt_out()
            assert not t.is_enabled

    def test_track_event_disabled(self, tmp_path):
        from services.telemetry import Telemetry

        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            t.track_event("test_event")
            # Should not create any files
            assert not (tmp_path / "events.jsonl").exists()

    def test_track_event_enabled(self, tmp_path):
        from services.telemetry import Telemetry

        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_event("app_launch", {"app": "files"})
            events_file = tmp_path / "events.jsonl"
            assert events_file.exists()
            lines = events_file.read_text().strip().splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["event"] == "app_launch"
            assert entry["data"]["app"] == "files"

    def test_track_crash(self, tmp_path):
        from services.telemetry import Telemetry

        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_crash("axon-brain", "ConnectionError", "traceback here")
            crashes_file = tmp_path / "crashes.jsonl"
            assert crashes_file.exists()
            entry = json.loads(crashes_file.read_text().strip())
            assert entry["service"] == "axon-brain"
            assert entry["error"] == "ConnectionError"

    def test_get_summary(self, tmp_path):
        from services.telemetry import Telemetry

        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_event("test")
            summary = t.get_summary()
            assert summary["enabled"] is True
            assert "events" in summary

    def test_summary_disabled(self, tmp_path):
        from services.telemetry import Telemetry

        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            summary = t.get_summary()
            assert summary["enabled"] is False


# ---------------------------------------------------------------------------
# Model Marketplace tests (unit tests, no Ollama needed)
# ---------------------------------------------------------------------------


class TestModelMarketplace:
    def test_catalog_loads(self):
        from services.axon_brain.model_marketplace import DEFAULT_CATALOG

        assert len(DEFAULT_CATALOG) >= 5
        for model in DEFAULT_CATALOG:
            assert "name" in model
            assert "description" in model
            assert "use_case" in model
            assert "tags" in model

    def test_search_catalog(self):
        from services.axon_brain.model_marketplace import DEFAULT_CATALOG

        results = [
            m for m in DEFAULT_CATALOG
            if "code" in m["name"].lower() or "code" in " ".join(m.get("tags", []))
        ]
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# Global Search tests
# ---------------------------------------------------------------------------


class TestGlobalSearch:
    def test_import(self):
        from services.axon_search.global_search_service import GlobalSearchService

        assert GlobalSearchService is not None


# ---------------------------------------------------------------------------
# Advanced Voice tests
# ---------------------------------------------------------------------------


class TestAdvancedVoice:
    def test_import(self):
        from services.axon_voice.advanced_voice_service import (
            ENGINES,
            LANGUAGES,
            WAKE_WORDS,
        )

        assert "whisper" in ENGINES
        assert "vosk" in ENGINES
        assert "en" in LANGUAGES
        assert "hey axon" in WAKE_WORDS
