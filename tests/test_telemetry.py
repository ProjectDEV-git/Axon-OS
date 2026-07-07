"""Tests for telemetry — event tracking, opt-in/out, daily aggregation."""

import json
from unittest.mock import patch

from services.telemetry import Telemetry


class TestTelemetryOptInOut:
    def test_disabled_by_default(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            assert t.is_enabled is False

    def test_opt_in_creates_marker(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            t.opt_in()
            assert t.is_enabled is True
            assert (tmp_path / "opt_in").exists()

    def test_opt_out_removes_marker(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.opt_out()
            assert t.is_enabled is False
            assert not (tmp_path / "opt_in").exists()

    def test_opt_out_idempotent(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            t.opt_out()  # should not raise
            assert t.is_enabled is False


class TestTelemetryEvents:
    def test_track_event_when_enabled(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_event("app_launch", {"app": "files"})
            events_file = tmp_path / "events.jsonl"
            assert events_file.exists()
            lines = events_file.read_text().strip().split("\n")
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["event"] == "app_launch"
            assert entry["data"]["app"] == "files"

    def test_track_event_when_disabled(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            t.track_event("app_launch")
            assert not (tmp_path / "events.jsonl").exists()

    def test_track_crash(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_crash("axon-brain", "ConnectionError", "traceback here")
            crashes_file = tmp_path / "crashes.jsonl"
            assert crashes_file.exists()
            entry = json.loads(crashes_file.read_text().strip())
            assert entry["service"] == "axon-brain"
            assert entry["error"] == "ConnectionError"

    def test_track_service_start(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_service_start("axon-search")
            events_file = tmp_path / "events.jsonl"
            entry = json.loads(events_file.read_text().strip())
            assert entry["event"] == "service_start"
            assert entry["data"]["service"] == "axon-search"

    def test_track_service_error(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_service_error("axon-voice", "timeout")
            events_file = tmp_path / "events.jsonl"
            entry = json.loads(events_file.read_text().strip())
            assert entry["event"] == "service_error"


class TestTelemetrySummary:
    def test_summary_when_disabled(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=False)
            summary = t.get_summary()
            assert summary["enabled"] is False

    def test_summary_when_enabled(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_event("test_event")
            summary = t.get_summary()
            assert summary["enabled"] is True
            assert "events" in summary
            assert summary["events"].get("test_event", 0) >= 1

    def test_daily_aggregation(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            t = Telemetry(enabled=True)
            t.track_event("event_a")
            t.track_event("event_a")
            t.track_event("event_b")
            summary = t.get_summary()
            assert summary["events"]["event_a"] == 2
            assert summary["events"]["event_b"] == 1


class TestTelemetrySingleton:
    def test_get_telemetry_returns_instance(self, tmp_path):
        with patch("services.telemetry.TELEMETRY_DIR", tmp_path):
            with patch("services.telemetry._instance", None):
                from services.telemetry import get_telemetry

                t = get_telemetry()
                assert isinstance(t, Telemetry)
