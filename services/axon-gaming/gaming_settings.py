"""User settings for game mode, stored as JSON in ``~/.config/axon/gaming.json``."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, bool] = {
    # Unload AI models and refuse GPU work while a game runs
    "pause_ai": True,
    # Answer AI requests with the user's cloud provider instead of pausing them
    "use_cloud": False,
    # Stop the file indexer while a game runs
    "pause_indexing": True,
}


def load(path: Path) -> dict[str, bool]:
    """Read the settings, falling back to defaults for anything missing or invalid."""
    settings = dict(DEFAULTS)
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(data, dict):
        for key in DEFAULTS:
            if isinstance(data.get(key), bool):
                settings[key] = data[key]
    return settings


def save(path: Path, settings: dict[str, bool]) -> None:
    """Write the settings atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".gaming-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({k: bool(settings[k]) for k in DEFAULTS}, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
