"""Game mode settings file."""

import json

import gaming_settings
import pytest


@pytest.mark.unit
def test_defaults_when_file_is_missing(tmp_path):
    assert gaming_settings.load(tmp_path / "gaming.json") == {
        "pause_ai": True,
        "use_cloud": False,
        "pause_indexing": True,
    }


@pytest.mark.unit
def test_round_trip(tmp_path):
    path = tmp_path / "config" / "gaming.json"
    settings = gaming_settings.load(path)
    settings["use_cloud"] = True
    gaming_settings.save(path, settings)
    assert gaming_settings.load(path)["use_cloud"] is True
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.unit
@pytest.mark.parametrize("content", ["not json", "[]", '{"pause_ai": "no", "other": true}'])
def test_invalid_values_fall_back_to_defaults(tmp_path, content):
    path = tmp_path / "gaming.json"
    path.write_text(content)
    assert gaming_settings.load(path) == gaming_settings.DEFAULTS


@pytest.mark.unit
def test_valid_keys_are_kept_and_unknown_ones_dropped(tmp_path):
    path = tmp_path / "gaming.json"
    path.write_text(json.dumps({"pause_ai": False, "bogus": True}))
    assert gaming_settings.load(path) == {**gaming_settings.DEFAULTS, "pause_ai": False}
