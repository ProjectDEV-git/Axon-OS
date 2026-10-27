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


class TestModelOrigin:
    PROVIDERS = [  # noqa: RUF012
        {"id": "ollama", "label": "Ollama", "base_url": "http://localhost:11434"},
        {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1"},
        {"id": "lmstudio", "label": "LM Studio", "base_url": "http://127.0.0.1:1234/v1"},
        {"id": "nas", "label": "NAS", "base_url": "http://192.168.1.20:11434"},
    ]

    def test_bare_name_is_local_ollama(self) -> None:
        from services.ui_logic import LOCAL, model_origin

        o = model_origin("llama3.2:3b", self.PROVIDERS)
        assert o.kind == LOCAL and o.label == "Local · llama3.2:3b"
        assert not o.leaves_computer

    def test_cloud_ref(self) -> None:
        from services.ui_logic import CLOUD, model_origin

        o = model_origin("@openai/gpt-4.1", self.PROVIDERS)
        assert o.kind == CLOUD and o.label == "Cloud · OpenAI · gpt-4.1"
        assert o.leaves_computer

    def test_cloud_without_provider_list_is_never_local(self) -> None:
        from services.ui_logic import CLOUD, model_origin

        o = model_origin("@anthropic/claude-sonnet")
        assert o.kind == CLOUD and o.provider == "Anthropic"
        assert model_origin("@custom/x").kind == CLOUD

    def test_local_server_and_lan(self) -> None:
        from services.ui_logic import LOCAL, REMOTE, model_origin

        assert model_origin("@lmstudio/qwen", self.PROVIDERS).label == "Local · LM Studio · qwen"
        assert model_origin("@lmstudio/qwen", self.PROVIDERS).kind == LOCAL
        assert model_origin("@nas/llama3", self.PROVIDERS).kind == REMOTE

    def test_remote_ollama(self) -> None:
        from services.ui_logic import REMOTE, model_origin

        providers = [{"id": "ollama", "label": "Ollama", "base_url": "http://gpu-box.local:11434"}]
        assert model_origin("llama3", providers).kind == REMOTE

    def test_openrouter_model_with_slash(self) -> None:
        from services.ui_logic import model_origin

        o = model_origin("@openrouter/anthropic/claude-sonnet-4.5")
        assert o.provider == "OpenRouter" and o.model == "anthropic/claude-sonnet-4.5"

    @pytest.mark.parametrize(
        ("url", "kind"),
        [
            ("http://localhost:11434", "local"),
            ("http://[::1]:8080", "local"),
            ("http://10.0.0.5", "remote"),
            ("http://host.docker.internal:11434", "remote"),
            ("https://api.anthropic.com", "cloud"),
            ("https://8.8.8.8", "cloud"),
        ],
    )
    def test_host_kind(self, url: str, kind: str) -> None:
        from services.ui_logic import host_kind

        assert host_kind(url) == kind

    def test_auto_routing_uses_least_private_tier(self) -> None:
        from services.ui_logic import CLOUD, LOCAL, routing_origin

        local_tiers = {"speed_model": "llama3.2:1b", "general_model": "llama3.2:3b"}
        o = routing_origin("", local_tiers, self.PROVIDERS)
        assert o.kind == LOCAL and o.model == "llama3.2:3b"
        mixed = dict(local_tiers, deep_model="@openai/gpt-4.1")
        o = routing_origin("", mixed, self.PROVIDERS)
        assert o.kind == CLOUD and o.provider == "OpenAI"
        assert routing_origin("@openai/gpt-4.1", local_tiers, self.PROVIDERS).kind == CLOUD
        unknown = routing_origin("", {}, None)
        assert unknown.label == "Model: unknown" and unknown.leaves_computer


def test_origin_tooltip() -> None:
    from services.ui_logic import CLOUD, LOCAL, UNKNOWN, ModelOrigin, origin_tooltip

    assert "this computer" in origin_tooltip(ModelOrigin(LOCAL, "Ollama", "x"))
    assert "OpenAI over the internet" in origin_tooltip(ModelOrigin(CLOUD, "OpenAI", "x"))
    assert "may leave" in origin_tooltip(ModelOrigin(UNKNOWN, "", ""))


def test_format_command_output() -> None:
    from services.ui_logic import format_command_output

    assert format_command_output("") == "(no output)"
    assert format_command_output("a\nb\n") == "a\nb"
    long = "\n".join(str(i) for i in range(100))
    out = format_command_output(long, max_lines=5)
    assert out.splitlines()[:5] == ["0", "1", "2", "3", "4"] and out.endswith("(output shortened)")
    assert format_command_output("x" * 50, max_chars=10).startswith("x" * 10)


def test_visible_conversations() -> None:
    from services.ui_logic import visible_conversations

    convs = [
        {"id": "a", "updated_at": "2026-01-01", "archived": 0},
        {"id": "b", "updated_at": "2026-03-01", "archived": 1},
        {"id": "c", "updated_at": "2026-02-01"},
    ]
    assert [c["id"] for c in visible_conversations(convs)] == ["c", "a"]
    assert [c["id"] for c in visible_conversations(convs, True)] == ["b", "c", "a"]


def test_latency_and_meta() -> None:
    from services.ui_logic import answer_meta, format_latency

    assert format_latency(0.85) == "850 ms"
    assert format_latency(2.44) == "2.4 s"
    assert format_latency(125) == "2 min 5 s"
    assert answer_meta("llama3.2:3b", 2.4, 0.6) == "llama3.2:3b · 2.4 s · first words after 600 ms"
    assert answer_meta("", 0.5) == "Automatic model · 500 ms"


def test_announcement() -> None:
    from services.ui_logic import announcement

    assert announcement("answer", "Four.\n\nMore text") == "Axon answered: Four. More text"
    assert announcement("answer", "") == "Axon answered."
    assert announcement("error", "Ollama is not running.").startswith("Axon couldn't finish:")
    assert announcement("command", "/dev/sda1 100G") == "Command finished."
    assert announcement("command", "Not run.") == "Command not run."
    assert announcement("cancelled") == "Cancelled."
    assert len(announcement("answer", "x" * 500)) < 140
