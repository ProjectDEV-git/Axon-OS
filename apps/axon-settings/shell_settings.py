"""Read and write the Axon Shell extension's GSettings from the GTK apps.

The extension ships its schema in its own ``schemas/`` directory instead of
the system schema path, so it has to be loaded from there.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

SCHEMA_ID = "org.gnome.shell.extensions.axon-shell"
EXTENSION_UUID = "axon-shell@axon-os"
NEVER_ROUTE = "__never__"  # keep in sync with shell/axon-shell/ui-logic.js


def schema_dirs(home: Path | None = None, data_dirs: str | None = None) -> list[Path]:
    """Directories that may hold the extension's compiled schema, best first."""
    home = home or Path.home()
    if data_dirs is None:
        data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    dirs = [home / ".local/share/gnome-shell/extensions" / EXTENSION_UUID / "schemas"]
    for base in data_dirs.split(":"):
        if base:
            dirs.append(Path(base) / "gnome-shell/extensions" / EXTENSION_UUID / "schemas")
    # Source checkout: apps/axon-settings/ → shell/axon-shell/schemas
    dirs.append(Path(__file__).resolve().parents[2] / "shell/axon-shell/schemas")
    return dirs


def load_shell_settings(dirs: list[Path] | None = None) -> Gio.Settings | None:
    """Return the extension's settings, or None if its schema isn't installed."""
    default = Gio.SettingsSchemaSource.get_default()
    schema = default.lookup(SCHEMA_ID, True) if default is not None else None
    if schema is None:
        for directory in dirs if dirs is not None else schema_dirs():
            if not (directory / "gschemas.compiled").is_file():
                continue
            try:
                source = Gio.SettingsSchemaSource.new_from_directory(str(directory), default, False)
            except Exception:
                continue
            schema = source.lookup(SCHEMA_ID, False)
            if schema is not None:
                break
    if schema is None:
        return None
    return Gio.Settings.new_full(schema, None, None)


def describe_rule(key: str, value: str) -> tuple[str, str]:
    """Title and subtitle for a per-app Space rule row."""
    if value == NEVER_ROUTE:
        return key, "Never moved"
    return key, f"Always goes to {value}"
