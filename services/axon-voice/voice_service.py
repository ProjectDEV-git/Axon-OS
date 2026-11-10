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
import queue
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

_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)
from _log_helper import resolve_logger as configure_app_logger
from constants import MAX_RECORD_SECONDS, WHISPER_DIR
from service_utils import confirm_and_exec

_this = str(Path(__file__).resolve().parent)
if _this not in sys.path:
    sys.path.insert(0, _this)
from intent_router import clean_transcript, parse_intent_response
from service_base import ServiceBase
from speech_stream import SentenceBuffer, speakable
from vad_helper import is_speech_wav

log = configure_app_logger("axon-voice", level=logging.INFO)

WHISPER_MODEL = os.environ.get("AXON_WHISPER_MODEL", "base.en")

# Allowlist of safe characters for AI-generated app names (no paths, no metacharacters)
_SAFE_APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _validate_app_name(name: str) -> str | None:
    """Return sanitized app name, or None if the name is unsafe."""
    name = name.strip()
    if not name or len(name) > 128:
        return None
    if not _SAFE_APP_RE.match(name):
        return None
    return name


class VoiceService(ServiceBase):
    BUS_NAME = "org.axonos.Voice"
    OBJECT_PATH = "/org/axonos/Voice"
    SERVICE_NAME = "axon-voice"

    def _setup(self):
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
        # Spoken agent replies: set while speaking; Toggle sets cancel to interrupt
        self._speaking = threading.Event()
        self._speech_cancel = threading.Event()
        self._voice_conv = ""

    def _cleanup(self):
        """Kill recorder subprocess on shutdown."""
        if self._recorder is not None:
            try:
                self._recorder.kill()
                self._recorder.wait(timeout=2)
            except Exception:
                pass

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
                if self._speaking.is_set():
                    self._speech_cancel.set()  # barge-in: stop the spoken reply
                return False
            self._busy = True  # claim immediately inside lock
        GLib.idle_add(self._start_recording)
        return True

    @dbus.service.method("org.axonos.Voice", in_signature="", out_signature="b")
    def IsListening(self):
        # FIX: Read _recorder under lock to avoid data race with
        # _start_recording/_stop_and_process running on the GLib thread.
        with self._lock:
            return self._recorder is not None

    @dbus.service.method("org.axonos.Voice", in_signature="", out_signature="b")
    def StartAmbient(self):
        """Begin ambient listening (VAD-based wake capture)."""
        # FIX: Read and update _ambient_thread under lock to avoid race conditions
        # with concurrent Toggle/StartAmbient calls from D-Bus clients.
        with self._lock:
            if self._ambient_thread and self._ambient_thread.is_alive():
                return False
            self._ambient_stop.clear()
            self._ambient_thread = threading.Thread(target=self._ambient_loop, daemon=True)
            self._ambient_thread.start()
        return True

    @dbus.service.method("org.axonos.Voice", in_signature="", out_signature="b")
    def StopAmbient(self):
        """Stop ambient listening."""
        # FIX: Check _ambient_thread under lock to avoid race with StartAmbient.
        with self._lock:
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
        os.chmod(wav_path, 0o600)
        cmd = self._recorder_command(wav_path)
        if cmd is None:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            self._recording_failed(
                "No microphone recorder found (install pulseaudio-utils or alsa-utils)."
            )
            return False
        try:
            self._recorder = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            self._recording_failed(f"Could not record: {exc}")
            return False
        self._wav_path = wav_path
        self._show_overlay("Listening... press Super+V again to stop")
        self.StateChanged("listening")
        # Hard cap so a forgotten toggle doesn't record forever.
        self._record_timeout_id = GLib.timeout_add_seconds(
            MAX_RECORD_SECONDS, self._stop_and_process
        )
        return False

    def _recording_failed(self, message):
        """Report a recording that never started and release the busy claim.

        Toggle sets ``_busy`` before recording starts; leaving it set would
        make every later Toggle a no-op until the service is restarted.
        """
        with self._lock:
            self._busy = False
        self._notify("Axon Voice", message)
        self.StateChanged("error")

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
            rec.wait()
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
        try:
            self._route(text)
        except Exception as exc:
            # _finish must always run: it is what clears the busy state
            log.warning("Voice routing failed: %s", exc)
            GLib.idle_add(self._finish, "", "Something went wrong handling that request.")

    def _route(self, text):
        """Act on a transcript: open an app, run a confirmed command, or answer."""
        reply = self._classify(text)
        kind, payload = parse_intent_response(reply)
        if kind == "open_app":
            safe_name = _validate_app_name(payload)
            if safe_name:
                # Desktop IDs only: never run an arbitrary binary named by the model
                if shutil.which("gtk-launch"):
                    subprocess.Popen(
                        ["gtk-launch", safe_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    GLib.idle_add(self._finish, f"Opening {safe_name}", "")
                else:
                    GLib.idle_add(self._finish, "", "Cannot open apps: gtk-launch is missing")
            else:
                GLib.idle_add(self._finish, "", f"Refused to launch unsafe app name: {payload!r}")
        elif kind == "run_command":
            # Speech (including ambient audio) must never run a command unattended
            if confirm_and_exec(payload):
                GLib.idle_add(self._finish, f"Running: {payload}", "")
            else:
                GLib.idle_add(self._finish, "", f"Did not run: {payload}")
        else:
            spoken = self._ask_agent(text)
            if spoken is None:  # agent unavailable: speak the intent answer
                spoken = speakable(payload) if payload else "I don't have an answer for that."
                self._speak(spoken)
            GLib.idle_add(self._finish, spoken, "")

    AGENT_TIMEOUT = 120
    VOICE_PROMPT = (
        "The user is talking to you by voice and hears your reply through text-to-speech. "
        "Answer in one to three short sentences of plain speech: no markdown, lists or code."
    )

    def _ask_agent(self, text):
        """Ask the Brain agent and speak its reply sentence by sentence.

        Returns the full spoken text, or None when the agent is unavailable
        (the caller then falls back to the ClassifyIntent answer).
        """
        try:
            obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            brain = dbus.Interface(obj, "org.axonos.Brain")
            if not self._voice_conv:
                self._voice_conv = str(
                    brain.CreateConversation(self.VOICE_PROMPT, "Voice", timeout=10)
                )
        except dbus.exceptions.DBusException as exc:
            log.debug("Voice agent unavailable: %s", exc)
            return None

        events: queue.Queue = queue.Queue()
        receivers = [
            self.session_bus.add_signal_receiver(
                lambda tx, tok: events.put(("token", str(tx), str(tok))),
                signal_name="TokenGenerated",
                dbus_interface="org.axonos.Brain",
            ),
            self.session_bus.add_signal_receiver(
                lambda tx, ok, err: events.put(("done", str(tx), bool(ok))),
                signal_name="GenerationCompleted",
                dbus_interface="org.axonos.Brain",
            ),
        ]
        try:
            try:
                tx = str(brain.SendMessageAgent(self._voice_conv, text, "", "", timeout=10))
            except dbus.exceptions.DBusException as exc:
                log.debug("SendMessageAgent failed: %s", exc)
                return None
            if tx.startswith("{"):  # JSON error instead of a transaction id
                return None
            return self._speak_stream(events, tx, brain)
        finally:
            for r in receivers:
                r.remove()

    def _speak_stream(self, events, tx, brain=None):
        """Speak tokens for transaction *tx* from *events* as sentences complete."""
        buf = SentenceBuffer()
        spoken: list[str] = []
        ok = True
        self._speech_cancel.clear()
        self._speaking.set()
        try:
            while True:
                if self._speech_cancel.is_set():
                    if brain is not None:
                        try:
                            brain.CancelStream(tx, timeout=5)
                        except dbus.exceptions.DBusException:
                            pass
                    break
                try:
                    kind, etx, value = events.get(timeout=self.AGENT_TIMEOUT)
                except queue.Empty:
                    ok = False
                    break
                if etx != tx:
                    continue
                if kind == "done":
                    ok = bool(value)
                    for s in buf.flush():
                        self._speak_one(s, spoken)
                    break
                for s in buf.feed(value):
                    self._speak_one(s, spoken)
                    if self._speech_cancel.is_set():
                        break
        finally:
            self._speaking.clear()
        if not spoken and not ok:
            return None
        return " ".join(spoken)

    def _speak_one(self, sentence, spoken):
        if self._speech_cancel.is_set():
            return
        spoken.append(sentence)
        self._speak(sentence, wait=True)

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

    def _speak(self, text, wait=False):
        """Say *text*. With ``wait`` the call returns when playback ends."""
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
                    proc = subprocess.Popen(
                        ["piper", "-t", text[:1000]],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._wait_tts(proc, wait)
                    return
                except Exception as e:
                    log.debug("piper TTS failed: %s", e)
                    continue
            if eng in ("espeak", "espeak-ng") and shutil.which(eng):
                try:
                    proc = subprocess.Popen(
                        [eng, text[:1000]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    self._wait_tts(proc, wait)
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
                    if wait:
                        self._cleanup_after_tts(proc, tmp)
                    else:
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
                    proc = subprocess.Popen(
                        ["spd-say", "--wait-mode", "yes" if wait else "no", text[:500]],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._wait_tts(proc, wait)
                    return
                except Exception as e:
                    log.debug("spd-say TTS failed: %s", e)
                    continue
        log.warning("No TTS engine available (tried: %s)", ", ".join(candidates))
        self._notify(
            "Axon Voice", "No text-to-speech engine found. Install piper, espeak, or spd-say."
        )

    def _wait_tts(self, proc, wait):
        """Reap a TTS process; with *wait*, block until it ends or speech is cancelled."""
        if not wait:
            threading.Thread(target=proc.wait, daemon=True).start()
            return
        while True:
            try:
                proc.wait(timeout=0.2)
                return
            except subprocess.TimeoutExpired:
                if self._speech_cancel.is_set():
                    proc.terminate()
                    threading.Thread(target=proc.wait, daemon=True).start()
                    return

    def _notify(self, title, body):
        if shutil.which("notify-send"):
            proc = subprocess.Popen(
                ["notify-send", "-i", "audio-input-microphone", title, body[:400]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            threading.Thread(target=proc.wait, daemon=True).start()

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
    VoiceService.main()
