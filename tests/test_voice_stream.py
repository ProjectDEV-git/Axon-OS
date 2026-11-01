"""Tests for sentence streaming of spoken agent replies."""

import queue
import threading

from services.axon_voice.speech_stream import SentenceBuffer, speakable

# Imported at module level: some test modules stub `dbus` during collection.
from services.axon_voice.voice_service import VoiceService


class TestSentenceBuffer:
    def test_releases_whole_sentences(self):
        buf = SentenceBuffer()
        out = []
        for tok in "The weather is sunny today. It will rain tomorrow evening. Bye".split(" "):
            out += buf.feed(tok + " ")
        assert out == ["The weather is sunny today.", "It will rain tomorrow evening."]
        assert buf.flush() == ["Bye"]
        assert buf.flush() == []

    def test_short_fragment_waits(self):
        buf = SentenceBuffer()
        assert buf.feed("Dr. ") == []
        assert buf.feed("Smith is in the office until five. ") == [
            "Dr. Smith is in the office until five."
        ]

    def test_long_run_on_split(self):
        out = SentenceBuffer().feed("word " * 200)
        assert out and all(len(s) <= 400 for s in out)

    def test_speakable(self):
        assert speakable("**Bold** and `code` [1] see [docs](http://x)") == "Bold and code see docs"


def _svc():
    svc = VoiceService.__new__(VoiceService)
    svc._speaking = threading.Event()
    svc._speech_cancel = threading.Event()
    said = []
    svc._speak = lambda text, wait=False: said.append(text)
    return svc, said


class TestSpeakStream:
    def test_speaks_matching_tx_only(self):
        svc, said = _svc()
        q = queue.Queue()
        for item in [
            ("token", "other", "Ignore this whole sentence please. "),
            ("token", "tx", "Your next meeting is at three. "),
            ("token", "tx", "Bring the slides"),
            ("done", "tx", True),
        ]:
            q.put(item)
        result = svc._speak_stream(q, "tx")
        assert said == ["Your next meeting is at three.", "Bring the slides"]
        assert result == "Your next meeting is at three. Bring the slides"
        assert not svc._speaking.is_set()

    def test_cancel_stops(self):
        svc, said = _svc()
        svc._speech_cancel.set()
        q = queue.Queue()
        q.put(("token", "tx", "Hello there, this is a long sentence. "))
        # _speak_stream clears cancel at start, so set it from the first speak
        svc._speak = lambda text, wait=False: (said.append(text), svc._speech_cancel.set())
        q.put(("token", "tx", "Second sentence that should not be said. "))
        q.put(("done", "tx", True))
        svc._speak_stream(q, "tx")
        assert said == ["Hello there, this is a long sentence."]

    def test_failure_without_speech_returns_none(self):
        svc, _ = _svc()
        q = queue.Queue()
        q.put(("done", "tx", False))
        assert svc._speak_stream(q, "tx") is None
