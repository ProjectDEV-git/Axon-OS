"""Intent Bar intents for Game Center: install heroic, open game center, update proton."""

import pytest

from services.suggestions import (
    GAME_CENTER,
    build_suggestions,
    default_index,
    find_catalog_item,
    game_center_argv,
    load_catalog_entries,
)


@pytest.fixture(scope="module")
def catalog():
    entries = load_catalog_entries()
    assert entries is not None
    return entries


def rows(query, catalog):
    return [r for r in build_suggestions(query, catalog=catalog) if r.category == GAME_CENTER]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "item"),
    [
        ("install heroic", "heroic"),
        ("Install Heroic Games Launcher", "heroic"),
        ("install epic games", "heroic"),
        ("install minecraft", "prism"),
        ("install proton ge", "protonplus"),
        ("install obs", "obs"),
        ("install codecs", "media-codecs"),
    ],
)
def test_install_intents_find_apps_by_name_or_alias(query, item, catalog):
    (row,) = rows(query, catalog)
    assert row.action == {"type": "game_center", "op": "install", "ids": [item]}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "preset"),
    [
        ("install the gaming starter pack", "starter"),
        ("install starter", "starter"),
        ("install streamer pack", "streamer"),
        ("install retro pack", "retro"),
    ],
)
def test_install_intents_find_presets(query, preset, catalog):
    (row,) = rows(query, catalog)
    assert row.action == {"type": "game_center", "op": "preset", "preset": preset}


@pytest.mark.unit
def test_retro_alone_means_retroarch_not_the_preset(catalog):
    (row,) = rows("install retro", catalog)
    assert row.action["op"] == "install" and row.action["ids"] == ["retroarch"]


@pytest.mark.unit
@pytest.mark.parametrize("query", ["open game center", "game center", "gaming apps"])
def test_open_intent(query, catalog):
    (row,) = rows(query, catalog)
    assert row.action == {"type": "game_center", "op": "open", "page": ""}


@pytest.mark.unit
def test_update_proton_opens_the_compatibility_page(catalog):
    (row,) = rows("update proton", catalog)
    assert row.action == {"type": "game_center", "op": "open", "page": "compatibility"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "query", ["install", "install zzzz", "update firefox", "heroic", "uninstall heroic"]
)
def test_other_queries_have_no_game_center_rows(query, catalog):
    assert rows(query, catalog) == []


@pytest.mark.unit
def test_without_a_catalog_nothing_is_offered():
    assert [r for r in build_suggestions("install heroic") if r.category == GAME_CENTER] == []


@pytest.mark.unit
def test_enter_runs_the_install_row(catalog):
    found = build_suggestions("install heroic", catalog=catalog)
    assert found[default_index(found)].category == GAME_CENTER


@pytest.mark.unit
def test_find_catalog_item_edge_cases():
    items = [("a", "Alpha", ["first"])]
    assert find_catalog_item("", items) is None
    assert find_catalog_item("fi", items) is None  # too short for a prefix match
    assert find_catalog_item("first", items) == ("a", "Alpha")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("action", "argv"),
    [
        ({"op": "open", "page": ""}, ["axon-game-center"]),
        ({"op": "open", "page": "drivers"}, ["axon-game-center", "--page", "drivers"]),
        (
            {"op": "install", "ids": ["heroic", "pcsx2"]},
            ["axon-game-center", "--install", "heroic", "pcsx2"],
        ),
        ({"op": "preset", "preset": "starter"}, ["axon-game-center", "--preset", "starter"]),
    ],
)
def test_argv_for_valid_actions(action, argv):
    assert game_center_argv(action) == argv


@pytest.mark.unit
@pytest.mark.parametrize(
    "action",
    [
        {"op": "open", "page": "../../etc"},
        {"op": "install", "ids": ["heroic; rm -rf /"]},
        {"op": "install", "ids": ["--background"]},
        {"op": "install", "ids": []},
        {"op": "preset", "preset": "-x"},
        {"op": "shell"},
        {},
    ],
)
def test_argv_rejects_anything_unexpected(action):
    assert game_center_argv(action) is None
