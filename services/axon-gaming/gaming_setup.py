"""Choices from the welcome app's gaming step, turned into an install command."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from catalog import Catalog

CODECS_ID = "media-codecs"
NONE_PRESET = "none"


def preset_ids(catalog: Catalog, preset: str) -> list[str]:
    """Item ids a preset ("starter", "streamer", "retro" or "none") selects."""
    if preset == NONE_PRESET or preset not in catalog.presets:
        return []
    return list(catalog.presets[preset].items)


def selected_ids(catalog: Catalog, chosen: set[str]) -> list[str]:
    """Chosen ids that exist and can be installed here, in catalog order."""
    return [i for i in catalog.items if i in chosen and catalog.items[i].kind != "component"]


def game_center_command() -> list[str]:
    """How to start Game Center: the installed command, else the source tree's main.py."""
    installed = shutil.which("axon-game-center")
    if installed:
        return [installed]
    main = Path(__file__).resolve().parents[2] / "apps" / "axon-game-center" / "main.py"
    return [sys.executable, str(main)]


def install_argv(ids: list[str], driver: str | None = None) -> list[str] | None:
    """The background install command, or None when there is nothing to install."""
    if not ids and not driver:
        return None
    argv = [*game_center_command(), "--background"]
    if ids:
        argv += ["--install", *ids]
    if driver:
        argv += ["--driver", driver]
    return argv
