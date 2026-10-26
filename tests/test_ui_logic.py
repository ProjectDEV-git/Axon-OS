"""Tests for the pure UI helpers in services/ui_logic.py."""

from __future__ import annotations

import pytest

from services.ui_logic import (
    HOLD,
    SHOW,
    classify_stream_prefix,
    describe_action,
    parse_action,
    strip_code_fence,
)


class TestClassifyStreamPrefix:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "{",
            '  {"action": "op',
            "`",
            "``",
            "```",
            "```j",
            "```json",
            "```JSON\n",
            "```json\n  {",
            "```\n",
            "```\n{",
        ],
    )
    def test_holds_possible_actions(self, text: str) -> None:
        assert classify_stream_prefix(text) == HOLD

    @pytest.mark.parametrize(
        "text",
        [
            "Sure",
            "  The answer is 4",
            "```python",
            "```python\nprint(1)",
            "```bash\n",
            "```json\nnot an object",
            "```\nplain code",
            "[1, 2]",
        ],
    )
    def test_shows_plain_text(self, text: str) -> None:
        assert classify_stream_prefix(text) == SHOW


class TestParseAction:
    def test_plain_json(self) -> None:
        assert parse_action('{"action": "open_app", "app": "firefox"}') == {
            "action": "open_app",
            "app": "firefox",
        }

    def test_fenced_json(self) -> None:
        reply = '```json\n{"action": "run_command", "command": "ls -la"}\n```'
        assert parse_action(reply) == {"action": "run_command", "command": "ls -la"}

    def test_bare_fence(self) -> None:
        assert parse_action('```\n{"action": "open_app", "app": "x"}\n```') is not None

    @pytest.mark.parametrize(
        "reply",
        [
            "Hello there",
            '{"app": "firefox"}',
            '{"action": 3}',
            "{not json",
            "[1, 2]",
            '```python\nprint("hi")\n```',
            "",
        ],
    )
    def test_rejects_non_actions(self, reply: str) -> None:
        assert parse_action(reply) is None


def test_strip_code_fence_leaves_plain_text() -> None:
    assert strip_code_fence("  hello  ") == "hello"
    assert strip_code_fence("```\nx\n```") == "x"


class TestDescribeAction:
    def test_open_app(self) -> None:
        assert describe_action({"action": "open_app", "app": "Firefox"}) == "Opening Firefox…"

    def test_run_command(self) -> None:
        assert describe_action({"action": "run_command", "command": "ls"}) == "Run: ls"

    def test_unknown(self) -> None:
        assert "can't" in describe_action({"action": "format_disk"})

    def test_missing_fields(self) -> None:
        assert describe_action({"action": "open_app"}) == "Opening app…"
        assert describe_action({"action": "run_command"}) == "Run command"


class TestRequestTracker:
    def test_lifecycle(self) -> None:
        from services.ui_logic import RequestTracker

        t = RequestTracker()
        assert not t.busy
        rid = t.start()
        assert t.busy and t.is_current(rid)
        assert t.finish(rid)
        assert not t.busy and not t.is_current(rid)

    def test_cancel_makes_request_stale(self) -> None:
        from services.ui_logic import RequestTracker

        t = RequestTracker()
        rid = t.start()
        assert t.cancel()
        assert not t.busy
        assert not t.is_current(rid)
        assert not t.finish(rid)  # late result is dropped
        assert not t.cancel()  # nothing running

    def test_newer_request_wins(self) -> None:
        from services.ui_logic import RequestTracker

        t = RequestTracker()
        first = t.start()
        second = t.start()
        assert not t.is_current(first)
        assert t.is_current(second)
        assert not t.finish(first)
        assert t.busy


def test_escape_action() -> None:
    from services.ui_logic import escape_action

    assert escape_action(True) == "cancel"
    assert escape_action(False) == "close"


class TestSplitError:
    def test_no_error(self) -> None:
        from services.ui_logic import split_error

        assert split_error(" Hello ") == ("Hello", None)
        assert split_error("Logs often say [error] mid-line") == (
            "Logs often say [error] mid-line",
            None,
        )

    def test_error_only(self) -> None:
        from services.ui_logic import split_error

        assert split_error("[error] Ollama is not running.") == ("", "Ollama is not running.")
        assert split_error("\n[Error: timed out]") == ("", "timed out")

    def test_partial_answer_then_error(self) -> None:
        from services.ui_logic import split_error

        assert split_error("Part one\n[error] connection lost") == ("Part one", "connection lost")

    def test_empty_marker(self) -> None:
        from services.ui_logic import split_error

        assert split_error("[error]") == ("", "Something went wrong.")


@pytest.mark.parametrize(
    ("preferred", "available", "expected"),
    [
        (860, 1080, 860),  # fits
        (860, 768, 720),  # 1366x768 laptop: shrink
        (860, 640, 592),  # 1280x800 at 125% scale is 640 logical px tall
        (860, 300, 320),  # never below the minimum
        (860, 0, 860),  # unknown monitor
        (200, 1080, 200),  # a small preferred size is kept
    ],
)
def test_fit_to_screen(preferred: int, available: int, expected: int) -> None:
    from services.ui_logic import fit_to_screen

    assert fit_to_screen(preferred, available) == expected


def test_stage_for_stream() -> None:
    from services.ui_logic import STAGE_PLANNING, stage_for_stream

    assert stage_for_stream('{"action"') == STAGE_PLANNING
    assert stage_for_stream("Sure, here") is None


class TestHistory:
    def test_push_moves_to_front_and_caps(self) -> None:
        from services.ui_logic import push_history

        h = push_history(["b", "a"], "a")
        assert h == ["a", "b"]
        assert push_history(h, "  ") == h
        assert len(push_history([str(i) for i in range(30)], "new", limit=20)) == 20

    def test_recent_skips_examples_and_dupes(self) -> None:
        from services.ui_logic import EXAMPLE_PROMPTS, recent_for_empty_state

        h = ["x", EXAMPLE_PROMPTS[0], "x", "y", "z", "w"]
        assert recent_for_empty_state(h) == ["x", "y", "z"]

    def test_save_and_load(self, tmp_path) -> None:
        from services.ui_logic import load_history, save_history

        path = tmp_path / "sub" / "history.json"
        save_history(path, ["one", "two"])
        assert load_history(path) == ["one", "two"]
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_load_bad_files(self, tmp_path) -> None:
        from services.ui_logic import load_history

        assert load_history(tmp_path / "missing.json") == []
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert load_history(bad) == []
        bad.write_text('{"a": 1}')
        assert load_history(bad) == []
        bad.write_text('["ok", 3, "  "]')
        assert load_history(bad) == ["ok"]


@pytest.mark.parametrize(
    ("monitor", "expected"), [(1024, 480), (1366, 615), (1920, 820), (3840, 820), (0, 660)]
)
def test_intent_bar_width(monitor: int, expected: int) -> None:
    from services.ui_logic import intent_bar_width

    assert intent_bar_width(monitor) == expected
