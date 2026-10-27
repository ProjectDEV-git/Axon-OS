"""Tests for window → Space classification with confidence."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from services.axon_brain.window_routing import (
    EXACT_ANSWER_CONFIDENCE,
    KNOWN_APP_CONFIDENCE,
    LOOSE_ANSWER_CONFIDENCE,
    classify_known_app,
    score_model_answer,
)


@pytest.mark.parametrize(
    ("wm_class", "space"),
    [
        ("Code", "Code"),
        ("firefox", "Web"),
        ("org.gnome.Nautilus", "Files"),
        ("gnome-terminal-server", "Terminal"),
        ("Slack", "Chat"),
        ("libreoffice-writer", "Work"),
        ("obsidian", "Notes"),
    ],
)
def test_known_apps(wm_class: str, space: str) -> None:
    assert classify_known_app(wm_class) == space


@pytest.mark.parametrize("wm_class", ["", "None", "some-unknown-app"])
def test_unknown_apps(wm_class: str) -> None:
    assert classify_known_app(wm_class) is None


def test_exact_answer_is_confident() -> None:
    assert score_model_answer(" 'Web'. ") == ("Web", EXACT_ANSWER_CONFIDENCE)


def test_loose_answer_is_unsure() -> None:
    assert score_model_answer("I think it belongs in Media.") == ("Media", LOOSE_ANSWER_CONFIDENCE)


@pytest.mark.parametrize("raw", ["", "Either Code or Web", "No idea"])
def test_no_or_ambiguous_answer(raw: str) -> None:
    assert score_model_answer(raw) == ("Default", 0.0)


class TestClassifyWindowDetailed:
    def _service(self, reply: str | Exception) -> object:
        from services.axon_brain.brain_service import BrainService

        service = BrainService.__new__(BrainService)
        service.router = MagicMock()
        service._complete = MagicMock(side_effect=[reply] if isinstance(reply, Exception) else None)
        if not isinstance(reply, Exception):
            service._complete.return_value = reply
        return service

    def test_known_app_skips_the_model(self) -> None:
        from services.axon_brain.brain_service import BrainService

        service = self._service("Chat")
        out = json.loads(BrainService.ClassifyWindowDetailed(service, "Inbox", "firefox"))
        assert out == {"space": "Web", "confidence": KNOWN_APP_CONFIDENCE, "source": "rule"}
        service._complete.assert_not_called()

    def test_model_answer(self) -> None:
        from services.axon_brain.brain_service import BrainService

        service = self._service("Notes")
        out = json.loads(BrainService.ClassifyWindowDetailed(service, "todo.md", "mystery"))
        assert out["space"] == "Notes" and out["source"] == "model"

    def test_model_failure(self) -> None:
        from services.axon_brain.brain_service import BrainService

        service = self._service(RuntimeError("offline"))
        out = json.loads(BrainService.ClassifyWindowDetailed(service, "x", "mystery"))
        assert out == {"space": "Default", "confidence": 0.0, "source": "none"}
