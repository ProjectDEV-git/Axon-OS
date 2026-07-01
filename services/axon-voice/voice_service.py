#!/usr/bin/env python3
"""Axon Voice — local speech-to-intent daemon (org.axonos.Voice).

Push-to-talk flow: a Super+V keybinding runs `axon-voice-toggle`, which
calls Toggle() over D-Bus (the bus auto-starts this service). The first
Toggle starts recording the microphone and shows a glowing wave overlay;
the second stops recording, transcribes the audio locally with
faster-whisper, and routes the text to org.axonos.Brain.ClassifyIntent.
App-launch / shell-command intents are executed immediately; plain answers
are spoken back through speech-dispatcher (spd-say) and shown as a
desktop notification. Everything runs on-device.
"""

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from constants import MAX_RECORD_SECONDS, WHISPER_DIR
from service_utils import safe_exec

from axon_logger import configure_app_logger

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intent_router import clean_transcript, parse_intent_response
from vad_helper import is_speech_wav

log = configure_app_logger("axon-voice", level=logging.INFO)

WHISPER_MODEL = os.environ.get("AXON_WHISPER_MODEL", "base.en")

# Allowlist of safe characters for AI-generated app names (no paths, no metacharacters)
_SAFE_APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_app_name(name: str) -> str | None:
    """Return sanitized app name, or None if the name is unsafe."""
    name = name.strip()
    if not name or len(name) > 128:
        return None
    if not _SAFE_APP_RE.match(name):
        return None
    return name


class VoiceService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        try:
            self.bus_name = dbus.service.BusName("org.axonos.Voice", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            log.error("org.axonos.Voice service is already running.")
            sys.exit(1)
        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/Voice")

        self._lock = threading.Lock()
        self._recorder = None  # subprocess.Popen while recording
        self._wav_path = None
        self._record_timeout_id = 0
        self._busy = False
        self._whisper = None  # lazily loaded WhisperModel
        self._overlay = None  # lazily built GTK overlay
        # Ambient listening state
        self._ambient_thread: threading.Thread | None = None
        self._ambient_stop = threading.Event()
        # TTS engine cached choice (env var overrides)
        self._tts_engine = os.environ.get("AXON_TTS_ENGINE", "")
        log.info("Axon Voice D-Bus service registered at /org/axonos/Voice")

    # ------------------------------------------------------------------
    # D-Bus API
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.Voice", in_signature="", out_signature="b")
    def Toggle(self):
        """Start listening, or stop + transcribe. Returns new listening state."""
        with self._lock:
            if self._recorder is not None:
                GLib.idle_add(self._stop_and_process)
                return False
            if self._busy:
                return False
        GLib.idle_add(self._start_recording)
        return True

    @dbus.service.method("org.axonos.Voice", in_signature="", out_signature="b")
    def IsListening(self):
        return self._recorder is not None

    @dbus.service.method("org.axonos.Voice", in_signature="", out_signature="b")
    def StartAmbient(self):
        """Begin ambient listening (VAD-based wake capture)."""
        if self._ambient_thread and self._ambient_thread.is_alive():
            return False
        self._ambient_stop.clear()
        self._ambient_thread = threading.Thread(target=self._ambient_loop, daemon=True)
        self._ambient_thread.start()
        return True

    @dbus.service.method("org.axonos.Voice", in_signature="", out_signature="b")
    def StopAmbient(self):
        """Stop ambient listening."""
        if not self._ambient_thread:
            return False
        self._ambient_stop.set()
        return True

    @dbus.service.signal("org.axonos.Voice", signature="s")
    def StateChanged(self, state):
        """Emitted with: listening | transcribing | idle | error."""

    @dbus.service.signal("org.axonos.Voice", signature="s")
    def TranscriptReady(self, text):
        """Emitted with the cleaned transcript before intent routing."""

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _recorder_command(self, wav_path):
        """Best available CLI recorder: pipewire/pulse first, then ALSA."""
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
            return ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", wav_path]
        return None

    def _start_recording(self):
        fd, wav_path = tempfile.mkstemp(prefix="axon-voice-", suffix=".wav")
        os.close(fd)
        cmd = self._recorder_command(wav_path)
        if cmd is None:
            self._notify(
                "Axon Voice",
                "No microphone recorder found (install pulseaudio-utils or alsa-utils).",
            )
            self.StateChanged("error")
            return False
        try:
            self._recorder = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            self._notify("Axon Voice", f"Could not record: {exc}")
            self.StateChanged("error")
            return False
        self._wav_path = wav_path
        self._show_overlay("Listening... press Super+V again to stop")
        self.StateChanged("listening")
        # Hard cap so a forgotten toggle doesn't record forever.
        self._record_timeout_id = GLib.timeout_add_seconds(
            MAX_RECORD_SECONDS, self._stop_and_process
        )
        return False

    def _stop_and_process(self):
        with self._lock:
            if self._record_timeout_id:
                GLib.source_remove(self._record_timeout_id)
                self._record_timeout_id = 0
            rec, self._recorder = self._recorder, None
            wav, self._wav_path = self._wav_path, None
        if rec is None:
            return False
        rec.send_signal(signal.SIGINT)
        try:
            rec.wait(timeout=3)
        except subprocess.TimeoutExpired:
            rec.kill()
        with self._lock:
            self._busy = True
        self._set_overlay_status("Transcribing on-device...")
        self.StateChanged("transcribing")
        threading.Thread(target=self._transcribe_and_route, args=(wav,), daemon=True).start()
        return False

    # ------------------------------------------------------------------
    # Transcription + routing (worker thread)
    # ------------------------------------------------------------------

    def _load_whisper(self):
        if self._whisper is not None:
            return self._whisper
        from faster_whisper import WhisperModel  # lazy: heavy import

        WHISPER_DIR.mkdir(parents=True, exist_ok=True)
        self._whisper = WhisperModel(
            WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
            download_root=str(WHISPER_DIR),
        )
        return self._whisper

    def _transcribe_and_route(self, wav_path):
        text = ""
        error = ""
        try:
            model = self._load_whisper()
            segments, _info = model.transcribe(wav_path, beam_size=1)
            text = clean_transcript(" ".join(s.text for s in segments))
        except Exception as exc:
            error = f"{exc}"
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

        if error:
            GLib.idle_add(self._finish, "", f"Transcription failed: {error}")
            return
        if not text:
            GLib.idle_add(self._finish, "", "Didn't catch that — try again.")
            return

        GLib.idle_add(self.TranscriptReady, text)
        GLib.idle_add(self._set_overlay_status, f'"{text}"')

        reply = self._classify(text)
        kind, payload = parse_intent_response(reply)
        if kind == "open_app":
            safe_name = _validate_app_name(payload)
            if safe_name:
                launcher = ["gtk-launch", safe_name] if shutil.which("gtk-launch") else [safe_name]
                subprocess.Popen(launcher, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                GLib.idle_add(self._finish, f"Opening {safe_name}", "")
            else:
                GLib.idle_add(self._finish, "", f"Refused to launch unsafe app name: {payload!r}")
        elif kind == "run_command":
            safe_exec(payload)
            GLib.idle_add(self._finish, f"Running: {payload}", "")
        else:
            spoken = payload if payload else "I don't have an answer for that."
            self._speak(spoken)
            GLib.idle_add(self._finish, spoken, "")

    def _classify(self, text):
        try:
            obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            brain = dbus.Interface(obj, "org.axonos.Brain")
            return str(brain.ClassifyIntent(text, timeout=45))
        except dbus.exceptions.DBusException as exc:
            return f"The AI brain is not available right now ({exc.get_dbus_name()})."

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _speak(self, text):
        # Prefer configured engine, then fallbacks. AXON_TTS_ENGINE may be set
        # to one of: piper, espeak, pico2wave, spd-say
        engine = self._tts_engine or os.environ.get("AXON_TTS_ENGINE", "")
        candidates = []
        if engine:
            candidates.append(engine)
        candidates += ["piper", "espeak", "pico2wave", "spd-say"]

        for eng in candidates:
            if eng == "piper" and shutil.which("piper"):
                try:
                    subprocess.Popen(
                        ["piper", "-t", text[:1000]],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception as e:
                    log.debug("piper TTS failed: %s", e)
                    continue
            if eng in ("espeak", "espeak-ng") and shutil.which(eng):
                try:
                    subprocess.Popen(
                        [eng, text[:1000]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return
                except Exception as e:
                    log.debug("%s TTS failed: %s", eng, e)
                    continue
            if eng == "pico2wave" and shutil.which("pico2wave") and shutil.which("aplay"):
                try:
                    fd, tmp = tempfile.mkstemp(prefix="axon-tts-", suffix=".wav")
                    os.close(fd)
                    subprocess.check_call(["pico2wave", "-w", tmp, text[:1000]])
                    proc = subprocess.Popen(
                        ["aplay", tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    threading.Thread(
                        target=self._cleanup_after_tts, args=(proc, tmp), daemon=True
                    ).start()
                    return
                except Exception as e:
                    log.debug("pico2wave TTS failed: %s", e)
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    continue
            if eng == "spd-say" and shutil.which("spd-say"):
                try:
                    subprocess.Popen(
                        ["spd-say", "--wait-mode", "no", text[:500]],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception as e:
                    log.debug("spd-say TTS failed: %s", e)
                    continue
        log.warning("No TTS engine available (tried: %s)", ", ".join(candidates))
        self._notify(
            "Axon Voice", "No text-to-speech engine found. Install piper, espeak, or spd-say."
        )

    def _notify(self, title, body):
        if shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", "-i", "audio-input-microphone", title, body[:400]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _cleanup_after_tts(self, proc, tmp_path):
        """Wait for TTS playback to finish, then delete the temp file."""
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    def _finish(self, result, error):
        with self._lock:
            self._busy = False
        if error:
            self._notify("Axon Voice", error)
        elif result:
            self._notify("Axon Voice", result)
        self._hide_overlay()
        self.StateChanged("idle")
        return False

    # ------------------------------------------------------------------
    # Overlay (GTK, optional — daemon keeps working headless)
    # ------------------------------------------------------------------

    def _show_overlay(self, status):
        try:
            if self._overlay is None:
                import gi

                gi.require_version("Gtk", "4.0")
                from gi.repository import Gtk

                if not Gtk.init_check():
                    return
                from overlay import VoiceOverlay

                self._overlay = VoiceOverlay()
            self._overlay.show(status)
        except Exception as exc:
            log.warning("overlay unavailable: %s", exc)
            self._overlay = None

    def _set_overlay_status(self, status):
        if self._overlay is not None:
            self._overlay.set_status(status)
        return False

    def _hide_overlay(self):
        if self._overlay is not None:
            self._overlay.hide()

    # ------------------------------------------------------------------
    # Ambient loop (simple implementation)
    # ------------------------------------------------------------------

    def _ambient_loop(self):
        """Record short chunks and run VAD on them; on speech, transcribe.

        This implementation records 1s chunks using `arecord` and runs the
        VAD helper. When speech is detected the chunk is handed to the
        transcription worker. It is intentionally conservative and exits if
        `arecord` is not available.
        """
        if not shutil.which("arecord"):
            log.warning("ambient disabled: 'arecord' not found")
            return
        while not self._ambient_stop.is_set():
            fd, wav = tempfile.mkstemp(prefix="axon-amb-", suffix=".wav")
            os.close(fd)
            cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", "1", wav]
            try:
                subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                try:
                    os.unlink(wav)
                except OSError:
                    pass
                break
            try:
                if is_speech_wav(wav):
                    threading.Thread(
                        target=self._transcribe_and_route, args=(wav,), daemon=True
                    ).start()
                    # cooldown to avoid repeated immediate triggers
                    self._ambient_stop.wait(1.2)
                else:
                    try:
                        os.unlink(wav)
                    except OSError:
                        pass
            except Exception as e:
                log.debug("Ambient speech detection error: %s", e)
                try:
                    os.unlink(wav)
                except OSError:
                    pass
        log.info("ambient stopped")


if __name__ == "__main__":
    import signal

    loop = GLib.MainLoop()
    service = VoiceService()

    def _shutdown(signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        loop.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        loop.run()
    except KeyboardInterrupt:
        loop.quit()
