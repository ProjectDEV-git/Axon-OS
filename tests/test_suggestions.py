"""Tests for categorised Intent Bar suggestions."""

from __future__ import annotations

from services.service_utils import validate_command
from services.suggestions import (
    APPS,
    ASK,
    CATEGORIES,
    COMMANDS,
    FILES,
    SETTINGS,
    SPACES,
    SUGGESTED_COMMANDS,
    build_suggestions,
    describe,
    match_score,
)

APPS_LIST = [
    ("firefox.desktop", "Firefox", "web browser internet"),
    ("org.gnome.Nautilus.desktop", "Files", "folder explorer"),
    ("code.desktop", "Visual Studio Code", "editor ide"),
]
SPACES_LIST = ["Code", "Web", "Chat", "Files"]


def cats(rows):
    return [r.category for r in rows]


def test_match_score() -> None:
    assert match_score("fir", "Firefox") == 3
    assert match_score("stud", "Visual Studio Code") == 2
    assert match_score("efo", "Firefox") == 1
    assert match_score("what is firefox", "Firefox") == 0
    assert match_score("", "x") == 0


def test_categories_are_grouped_in_order() -> None:
    rows = build_suggestions("fi", APPS_LIST, SPACES_LIST)
    order = [c for c in CATEGORIES if c in cats(rows)]
    assert cats(rows) == sorted(cats(rows), key=order.index)
    assert cats(rows)[-1] == ASK
    assert rows[0].category == APPS
    assert {r.title for r in rows if r.category == APPS} == {"Files", "Firefox"}


def test_settings_spaces_and_commands() -> None:
    rows = build_suggestions("wi", APPS_LIST, SPACES_LIST)
    assert any(r.category == SETTINGS and r.title == "Wi-Fi" for r in rows)
    rows = build_suggestions("web", APPS_LIST, SPACES_LIST)
    space = next(r for r in rows if r.category == SPACES)
    assert space.action == {"type": "space", "index": 1, "name": "Web"}
    assert space.subtitle == "Super+2"
    rows = build_suggestions("disk", APPS_LIST, SPACES_LIST)
    assert any(r.category == COMMANDS and r.title == "df -h" for r in rows)


def test_questions_dont_become_app_launches() -> None:
    rows = build_suggestions("what is firefox", APPS_LIST, SPACES_LIST)
    assert APPS not in cats(rows)
    assert cats(rows)[-1] == ASK


def test_find_prefix_puts_files_first() -> None:
    rows = build_suggestions("find tax pdf", APPS_LIST, SPACES_LIST)
    assert rows[0].category == FILES and rows[0].action["query"] == "tax pdf"


def test_empty_query() -> None:
    assert build_suggestions("   ", APPS_LIST) == []


def test_limit_per_category() -> None:
    many = [(f"a{i}.desktop", f"Alpha {i}", "") for i in range(10)]
    rows = build_suggestions("alpha", many)
    assert cats(rows).count(APPS) == 3


def test_suggested_commands_pass_the_allowlist() -> None:
    for command, _desc, _words in SUGGESTED_COMMANDS:
        assert validate_command(command) is not None, command


def test_describe_and_accessible_label() -> None:
    rows = build_suggestions("df", APPS_LIST, SPACES_LIST)
    cmd = next(r for r in rows if r.category == COMMANDS)
    assert describe(cmd) == "Run: df -h (asks first)"
    assert cmd.accessible_label.startswith("Commands: df -h.")
    assert describe(rows[-1]) == "Ask Axon AI"


def test_default_selection() -> None:
    from services.suggestions import default_index

    rows = build_suggestions("fire", APPS_LIST, SPACES_LIST)
    assert rows[default_index(rows)].title == "Firefox"
    rows = build_suggestions("tell me a joke", APPS_LIST, SPACES_LIST)
    assert rows[default_index(rows)].category == ASK
    rows = build_suggestions("find taxes", APPS_LIST, SPACES_LIST)
    assert rows[default_index(rows)].category == FILES
    rows = build_suggestions("disk", APPS_LIST, SPACES_LIST)
    assert rows[default_index(rows)].category == COMMANDS
