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
