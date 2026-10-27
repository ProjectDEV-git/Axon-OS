"""Intent Bar game mode intents ("game mode on", "is game mode on")."""

import pytest

from services.suggestions import (
    GAME_MODE,
    build_suggestions,
    default_index,
    describe,
    game_mode_ops,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "ops"),
    [
        ("game mode on", ["on"]),
        ("Turn game mode OFF", ["off"]),
        ("stop game mode", ["off"]),
        ("is game mode on", ["status"]),
        ("game mode?", ["status"]),
        ("game mode", ["status", "on", "off"]),
        ("gamemode", ["status", "on", "off"]),
        ("game", []),
        ("mode on", []),
        ("dark mode", []),
        ("", []),
    ],
)
def test_game_mode_ops(query, ops):
    assert game_mode_ops(query) == ops


@pytest.mark.unit
def test_rows_carry_a_fixed_action_and_a_preview():
    rows = [r for r in build_suggestions("game mode on") if r.category == GAME_MODE]
    assert [r.action for r in rows] == [{"type": "game_mode", "op": "on"}]
    assert describe(rows[0]) == "Turn game mode on"


@pytest.mark.unit
def test_enter_runs_the_game_mode_row_not_the_ai():
    rows = build_suggestions("game mode off")
    assert rows[default_index(rows)].category == GAME_MODE


@pytest.mark.unit
def test_other_queries_have_no_game_mode_rows():
    assert not [r for r in build_suggestions("firefox") if r.category == GAME_MODE]
