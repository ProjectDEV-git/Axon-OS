"""Advanced Voice Pipeline — multi-engine STT, streaming transcription, wake word.

Provides a D-Bus interface (org.axonos.AdvancedVoice) with:
  - Multiple STT engine support (whisper, vosk, speech-to-text)
  - Streaming partial transcription
  - Wake word detection ("Hey Axon")
  - Audio level monitoring
  - Language detection and selection
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axon_logger import configure_app_logger

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import WHISPER_DIR

log = configure_app_logger("axon-advanced-voice", level=logging.INFO)

# Available STT engines
ENGINES = {
    "whisper": {"name": "Whisper (faster-whisper)", "priority": 1},
    "vosk": {"name": "Vosk", "priority": 2},
    "speech-dispatcher": {"name": "Speech Dispatcher", "priority": 3},
}

# Wake word patterns
WAKE_WORDS = ["hey axon", "ok axon", "axon", "hey computer"]

# Supported languages
LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
}


class AdvancedVoiceService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        try:
            self.bus_name = dbus.service.BusName("org.axonos.AdvancedVoice", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            log.error("org.axonos.AdvancedVoice service is already running.")
            sys.exit(1)
        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/AdvancedVoice")

        self._whisper_model = None
        self._vosk_model = None
        self._recorder = None
        self._wav_path = None
        self._busy = False
        self._listening = False
        self._language = os.environ.get("AXON_VOICE_LANG", "en")
        self._engine = os.environ.get("AXON_VOICE_ENGINE", "whisper")
        self._wake_enabled = False
        self._wake_thread = None
        self._wake_stop = threading.Event()
        self._audio_level = 0.0
        self._lock = threading.Lock()
        self._partial_transcript = ""
        log.info("AdvancedVoice registered at /org/axonos/AdvancedVoice")

    # ------------------------------------------------------------------
    # D-Bus API
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.AdvancedVoice", out_signature="s")
    def GetStatus(self):
        """Return current voice pipeline status."""
        available_engines = []
        if shutil.which("whisper") or self._whisper_model is not None:
            available_engines.append("whisper")
        if self._check_vosk():
            available_engines.append("vosk")
        if shutil.which("spd-say"):
            available_engines.append("speech-dispatcher")

        return json.dumps(
            {
                "listening": self._listening,
                "busy": self._busy,
                "engine": self._engine,
                "available_engines": available_engines,
                "language": self._language,
                "language_name": LANGUAGES.get(self._language, self._language),
                "wake_enabled": self._wake_enabled,
                "audio_level": round(self._audio_level, 3),
            }
        )

    @dbus.service.method("org.axonos.AdvancedVoice", out_signature="s")
    def ListEngines(self):
        """List available STT engines."""
        return json.dumps(ENGINES)

    @dbus.service.method("org.axonos.AdvancedVoice", out_signature="s")
    def ListLanguages(self):
        """List supported languages."""
        return json.dumps(LANGUAGES)

    @dbus.service.method("org.axonos.AdvancedVoice", in_signature="s", out_signature="b")
    def SetLanguage(self, lang_code):
        """Set transcription language (e.g. 'en', 'es')."""
        if lang_code in LANGUAGES:
            self._language = lang_code
            log.info("Language set to %s (%s)", lang_code, LANGUAGES[lang_code])
            return True
        return False

    @dbus.service.method("org.axonos.AdvancedVoice", in_signature="s", out_signature="b")
    def SetEngine(self, engine_name):
        """Set STT engine ('whisper', 'vosk')."""
        if engine_name in ENGINES:
            self._engine = engine_name
            log.info("Engine set to %s", engine_name)
            return True
        return False

    @dbus.service.method("org.axonos.AdvancedVoice", out_signature="b")
    def StartListening(self):
        """Start recording audio."""
        with self._lock:
            if self._listening or self._busy:
                return False
            self._busy = True
            self._listening = True
            self._partial_transcript = ""
        self.StateChanged("listening")
        GLib.idle_add(self._start_recording)
        return True

    @dbus.service.method("org.axonos.AdvancedVoice", out_signature="s")
    def StopAndTranscribe(self):
        """Stop recording and return transcription."""
        with self._lock:
            if not self._listening:
                return json.dumps({"error": "not listening"})
            self._listening = False
            self._busy = True
        self.StateChanged("transcribing")
        GLib.idle_add(self._stop_and_transcribe)
        return json.dumps({"status": "transcribing"})

    @dbus.service.method("org.axonos.AdvancedVoice", in_signature="s", out_signature="s")
    def TranscribeFile(self, file_path):
        """Transcribe an audio file directly."""
        if not file_path or not Path(file_path).exists():
            return json.dumps({"error": "file not found"})
        return self._transcribe_file(file_path)

    @dbus.service.method("org.axonos.AdvancedVoice", in_signature="b", out_signature="b")
    def EnableWakeWord(self, enable):
        """Enable or disable wake word detection."""
        self._wake_enabled = enable
        if enable and not self._wake_thread:
            self._wake_stop.clear()
            self._wake_thread = threading.Thread(target=self._wake_loop, daemon=True)
            self._wake_thread.start()
        elif not enable:
            self._wake_stop.set()
        return True

    @dbus.service.method("org.axonos.AdvancedVoice", out_signature="s")
    def GetPartialTranscript(self):
        """Get current partial (streaming) transcript."""
        return self._partial_transcript

    @dbus.service.method("org.axonos.AdvancedVoice", in_signature="s", out_signature="b")
    def Speak(self, text):
        """Text-to-speech using spd-say."""
        if not text or not shutil.which("spd-say"):
            return False
        try:
            subprocess.Popen(
                ["spd-say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    @dbus.service.signal("org.axonos.AdvancedVoice", signature="s")
    def StateChanged(self, state):
        """Emitted with: listening | transcribing | idle | error | wake-detected."""

    @dbus.service.signal("org.axonos.AdvancedVoice", signature="s")
    def TranscriptReady(self, text):
        """Emitted with final transcription result."""

    @dbus.service.signal("org.axonos.AdvancedVoice", signature="s")
    def PartialTranscript(self, text):
        """Emitted with streaming partial transcript."""

    @dbus.service.signal("org.axonos.AdvancedVoice", signature="d")
    def AudioLevel(self, level):
        """Emitted with current audio level (0.0-1.0)."""

    @dbus.service.signal("org.axonos.AdvancedVoice", signature="s")
    def WakeWordDetected(self, wake_word):
        """Emitted when a wake word is detected."""

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _recorder_command(self, wav_path):
        if shutil.which("parecord"):
            return [
                "parecord",
                "--rate=16000",
                "--channels=1",
                "--format=s16le",
                "--file-format=wav",
                wav_path,
            ]
        if shutil.which("arecord"):
            return [
                "arecord",
                "-q",
                "-f",
                "S16_LE",
                "-r",
                "16000",
                "-c",
                "1",
                wav_path,
            ]
        return None

    def _start_recording(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            self._wav_path = tmp.name
        cmd = self._recorder_command(self._wav_path)
        if not cmd:
            self._busy = False
            self._listening = False
            self.StateChanged("error")
            return
        try:
            self._recorder = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # Audio level monitor
            threading.Thread(target=self._monitor_audio, daemon=True).start()
        except Exception as e:
            log.error("Recording failed: %s", e)
            self._busy = False
            self._listening = False
            self.StateChanged("error")

    def _stop_and_transcribe(self):
        with self._lock:
            rec = self._recorder
            self._recorder = None
            wav_path = self._wav_path
            self._wav_path = None
        if rec:
            try:
                rec.terminate()
                rec.wait(timeout=5)
            except Exception:
                rec.kill()

        if wav_path and Path(wav_path).exists():
            result = self._transcribe_file(wav_path)
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            self._busy = False
            self.TranscriptReady(result)
        else:
            self._busy = False
            self.StateChanged("idle")

    def _monitor_audio(self):
        """Monitor audio levels during recording."""
        while self._listening and self._recorder:
            wav_path = self._wav_path
            if not wav_path or not Path(wav_path).exists():
                break
            try:
                import wave

                with wave.open(wav_path, "rb") as wf:
                    frames = wf.readframes(min(1600, wf.getnframes()))
                    if frames:
                        import struct

                        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                        rms = (sum(s**2 for s in samples) / max(len(samples), 1)) ** 0.5
                        self._audio_level = min(rms / 32768.0, 1.0)
                        self.AudioLevel(self._audio_level)
            except Exception:
                pass
            time.sleep(0.1)

    # ------------------------------------------------------------------
    # Transcription engines
    # ------------------------------------------------------------------

    def _transcribe_file(self, file_path: str) -> str:
        """Transcribe an audio file using the configured engine."""
        if self._engine == "whisper":
            return self._transcribe_whisper(file_path)
        elif self._engine == "vosk":
            return self._transcribe_vosk(file_path)
        else:
            return self._transcribe_whisper(file_path)

    def _transcribe_whisper(self, file_path: str) -> str:
        """Transcribe using faster-whisper (local model)."""
        try:
            from faster_whisper import WhisperModel

            if self._whisper_model is None:
                model_size = os.environ.get("AXON_WHISPER_MODEL", "base.en")
                self._whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = self._whisper_model.transcribe(file_path, language=self._language)
            text = " ".join(segment.text for segment in segments).strip()
            log.info(
                "Whisper transcribed (%s, %.1fs): %s",
                info.language,
                info.duration,
                text[:80],
            )
            return text
        except ImportError:
            return self._transcribe_cli(file_path)
        except Exception as e:
            log.error("Whisper transcription failed: %s", e)
            return self._transcribe_cli(file_path)

    def _transcribe_vosk(self, file_path: str) -> str:
        """Transcribe using Vosk (offline)."""
        try:
            import wave  # noqa: I001
            from vosk import Model, KaldiRecognizer

            model_path = str(WHISPER_DIR / "vosk" / self._language)
            if not Path(model_path).exists():
                log.warning("Vosk model not found at %s, falling back", model_path)
                return self._transcribe_whisper(file_path)

            model = Model(model_path)
            with wave.open(file_path, "rb") as wf:
                rec = KaldiRecognizer(model, wf.getframerate())
                rec.SetWords(True)

                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    rec.AcceptWaveform(data)

            result = json.loads(rec.FinalResult())
            return str(result.get("text", ""))
        except ImportError:
            log.warning("Vosk not installed, falling back to whisper")
            return self._transcribe_whisper(file_path)
        except Exception as e:
            log.error("Vosk transcription failed: %s", e)
            return self._transcribe_whisper(file_path)

    def _transcribe_cli(self, file_path: str) -> str:
        """Fallback CLI transcription using whisper command."""
        try:
            result = subprocess.run(
                ["whisper", file_path, "--language", self._language, "--output_format", "txt"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            txt_file = Path(file_path).with_suffix(".txt")
            if txt_file.exists():
                return txt_file.read_text().strip()
            return result.stdout.strip()
        except Exception as e:
            log.error("CLI transcription failed: %s", e)
            return ""

    def _check_vosk(self) -> bool:
        """Check if Vosk model is available."""
        try:
            model_path = str(WHISPER_DIR / "vosk" / self._language)
            return Path(model_path).exists()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Wake word detection
    # ------------------------------------------------------------------

    def _wake_loop(self):
        """Ambient listening loop with wake word detection."""
        log.info("Wake word detection started")
        while not self._wake_stop.is_set():
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    wake_path = tmp.name

                cmd = self._recorder_command(wake_path)
                if not cmd:
                    break

                # Record short clip (2 seconds)
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()

                # Quick transcription
                if Path(wake_path).exists():
                    text = self._transcribe_file(wake_path).lower()
                    for wake_word in WAKE_WORDS:
                        if wake_word in text:
                            self.WakeWordDetected(wake_word)
                            self.StateChanged("wake-detected")
                            log.info("Wake word detected: %s", wake_word)
                            break
                    try:
                        os.unlink(wake_path)
                    except OSError:
                        pass

            except Exception as e:
                log.debug("Wake loop error: %s", e)
                time.sleep(1)

        log.info("Wake word detection stopped")


if __name__ == "__main__":
    loop = GLib.MainLoop()
    service = AdvancedVoiceService()
    try:
        loop.run()
    except KeyboardInterrupt:
        loop.quit()
