#!/usr/bin/env python3
import os
import sys
import json
import threading
import subprocess
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

# Ensure we can load axon_logger
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from axon_logger import configure_app_logger
    logger = configure_app_logger(__name__)
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("axon-voice")

class VoiceService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        
        try:
            self.bus_name = dbus.service.BusName('org.axonos.Voice', bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            logger.error("org.axonos.Voice service is already running.")
            sys.exit(1)
            
        dbus.service.Object.__init__(self, self.session_bus, '/org/axonos/Voice')
        
        self.recording_proc = None
        self.wav_path = "/tmp/axon_voice_input.wav"
        self.model = None
        
        # Initialize whisper model in background thread
        threading.Thread(target=self._init_model, daemon=True).start()
        logger.info("Axon Voice Service registered successfully at /org/axonos/Voice")

    def _init_model(self):
        try:
            from faster_whisper import WhisperModel
            logger.info("Initializing local faster-whisper model (tiny.en)...")
            model_dir = Path.home() / ".axon" / "models" / "whisper"
            model_dir.mkdir(parents=True, exist_ok=True)
            self.model = WhisperModel("tiny.en", device="cpu", compute_type="int8", download_root=str(model_dir))
            logger.info("faster-whisper model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper model: {e}")

    @dbus.service.method('org.axonos.Voice', in_signature='', out_signature='b')
    def StartRecording(self):
        if self.recording_proc is not None:
            logger.warning("Recording already in progress.")
            return False
            
        logger.info("Starting audio recording via arecord...")
        # Remove old recording if it exists
        if os.path.exists(self.wav_path):
            try:
                os.remove(self.wav_path)
            except OSError:
                pass
                
        cmd = ["arecord", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1", self.wav_path]
        try:
            self.recording_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.RecordingStarted()
            return True
        except Exception as e:
            logger.error(f"Failed to launch arecord: {e}")
            return False

    @dbus.service.method('org.axonos.Voice', in_signature='', out_signature='b')
    def StopRecording(self):
        if self.recording_proc is None:
            logger.warning("No recording active to stop.")
            return False
            
        logger.info("Stopping audio recording...")
        self.recording_proc.terminate()
        try:
            self.recording_proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self.recording_proc.kill()
            self.recording_proc.wait()
            
        self.recording_proc = None
        
        # Transcribe asynchronously in background thread
        threading.Thread(target=self._transcribe_and_process, daemon=True).start()
        return True

    @dbus.service.method('org.axonos.Voice', in_signature='', out_signature='b')
    def IsRecording(self):
        return self.recording_proc is not None

    def _transcribe_and_process(self):
        try:
            if self.model is None:
                logger.warning("Whisper model is not ready yet. Waiting for initialization...")
                for _ in range(15):
                    if self.model is not None:
                        break
                    time.sleep(1.0)
                if self.model is None:
                    logger.error("Whisper model initialization timed out.")
                    self.TranscriptionCompleted("", "{}")
                    return

            if not os.path.exists(self.wav_path) or os.path.getsize(self.wav_path) < 1000:
                logger.warning("Recording wav file is empty or missing.")
                self.TranscriptionCompleted("", "{}")
                return

            logger.info("Starting speech-to-text transcription...")
            segments, info = self.model.transcribe(self.wav_path, beam_size=5)
            transcription = " ".join([segment.text for segment in segments]).strip()
            logger.info(f"Transcribed Text: '{transcription}'")

            intent_json = "{}"
            if transcription:
                try:
                    logger.info("Sending transcription to org.axonos.Brain.ClassifyIntent...")
                    brain_obj = self.session_bus.get_object('org.axonos.Brain', '/org/axonos/Brain')
                    # Standard fallback check: dbus interface binding
                    brain_interface = dbus.Interface(brain_obj, 'org.axonos.Brain')
                    intent_json = brain_interface.ClassifyIntent(transcription)
                    logger.info(f"Intent response: {intent_json}")
                except Exception as e:
                    logger.error(f"Failed to query Brain service: {e}")
                    # Local fallback intent classification if Brain service is offline
                    if "open" in transcription.lower() or "launch" in transcription.lower():
                        app_name = transcription.lower().replace("open", "").replace("launch", "").strip()
                        intent_json = json.dumps({"action": "open_app", "app": app_name})
                    elif "run" in transcription.lower():
                        cmd = transcription.lower().replace("run", "").strip()
                        intent_json = json.dumps({"action": "run_command", "command": cmd})

            self.TranscriptionCompleted(transcription, intent_json)
        except Exception as e:
            logger.exception("Error in transcription thread:")
            self.TranscriptionCompleted("", "{}")

    # ------------------------------------------------------------------
    # D-Bus Signals
    # ------------------------------------------------------------------
    @dbus.service.signal('org.axonos.Voice', signature='')
    def RecordingStarted(self):
        pass

    @dbus.service.signal('org.axonos.Voice', signature='ss')
    def TranscriptionCompleted(self, transcription, intent_json):
        pass

if __name__ == '__main__':
    loop = GLib.MainLoop()
    service = VoiceService()
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Stopping Axon Voice Service...")
        loop.quit()
