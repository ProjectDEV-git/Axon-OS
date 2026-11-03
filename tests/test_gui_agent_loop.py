"""Tests for the GuiAgent multi-round loop, new ops and undo."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from services.axon_gui_agent import plan as plan_mod

# Imported at module level: other test modules replace `dbus` with a stub when
# they are collected, which would break a later import of the service.
from services.axon_gui_agent.gui_agent_service import GuiAgentService


class TestPlanOps:
    def test_get_and_uri_allowed(self):
        ops, errs = plan_mod.validate_plan(
            json.dumps(
                [
                    {"type": "gsettings_get", "schema": "org.gnome.desktop.interface", "key": "k"},
                    {"type": "open_uri", "uri": "https://axonos.org"},
                ]
            )
        )
        assert len(ops) == 2 and errs == []

    def test_blocked(self):
        bad = [
            {"type": "open_uri", "uri": "file:///etc/shadow"},
            {"type": "open_uri", "uri": "https://x y"},
            {"type": "gsettings_get", "schema": "org.gnome.desktop.lockdown", "key": "k"},
        ]
        ops, errs = plan_mod.validate_plan(json.dumps(bad))
        assert ops == [] and len(errs) == 3

    def test_done_flag(self):
        assert plan_mod.parse_round("[]")[2] is True
        assert plan_mod.parse_round('{"operations": [], "done": false}')[2] is False
        assert plan_mod.parse_round("nonsense")[2] is True

    def test_safe_gvariant(self):
        assert plan_mod.is_safe_gvariant_text("'prefer-dark'")
        assert not plan_mod.is_safe_gvariant_text("'a'; rm -rf ~")
        assert not plan_mod.is_safe_gvariant_text("")


def _service(replies, settings):
    svc = GuiAgentService.__new__(GuiAgentService)
    svc._undo_lock = threading.Lock()
    svc._last_undo = []
    prompts = []
    it = iter(replies)
    svc._ask_brain = lambda p: (prompts.append(p), next(it, "[]"))[1]
    svc._notify = lambda *a: None

    def gsettings(*args):
        if args[0] == "get":
            v = settings.get((args[1], args[2]))
            return SimpleNamespace(returncode=0 if v else 1, stdout=v or "", stderr="")
        settings[(args[1], args[2])] = args[3]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    svc._gsettings = gsettings
    return svc, prompts


SCHEMA = "org.gnome.desktop.interface"


class TestLoop:
    def test_read_then_set_then_undo(self):
        settings = {(SCHEMA, "color-scheme"): "'default'"}
        replies = [
            json.dumps(
                {
                    "operations": [
                        {"type": "gsettings_get", "schema": SCHEMA, "key": "color-scheme"}
                    ],
                    "done": False,
                }
            ),
            json.dumps(
                [
                    {
                        "type": "gsettings_set",
                        "schema": SCHEMA,
                        "key": "color-scheme",
                        "value": "prefer-dark",
                    }
                ]
            ),
        ]
        svc, prompts = _service(replies, settings)
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            report = svc._run("toggle dark mode")
        assert report["rounds"] == 2 and report["ok"] and report["undoable"] == 1
        assert "read org.gnome.desktop.interface color-scheme = 'default'" in prompts[1]
        out = json.loads(svc.UndoLast())
        assert out["ok"] and settings[(SCHEMA, "color-scheme")] == "'default'"
        assert json.loads(svc.UndoLast())["restored"] == []

    def test_brain_down(self):
        svc, _ = _service([], {})
        svc._ask_brain = lambda p: None
        assert svc._run("x")["error"] == "AI brain unavailable"

    def test_round_limit(self):
        loop = json.dumps({"operations": [{"type": "notify", "message": "hi"}], "done": False})
        svc, prompts = _service([loop] * 10, {})
        report = svc._run("x")
        assert report["rounds"] == svc.MAX_ROUNDS == len(prompts)
