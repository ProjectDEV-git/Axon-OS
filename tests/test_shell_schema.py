"""The committed gschemas.compiled must match the schema XML."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 - parses a file from this repo
from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "shell" / "axon-shell" / "schemas"
SCHEMA_ID = "org.gnome.shell.extensions.axon-shell"


def _xml_keys() -> set[str]:
    root = ET.parse(SCHEMA_DIR / f"{SCHEMA_ID}.gschema.xml").getroot()  # nosec B314
    return {key.get("name", "") for key in root.iter("key")}


def test_compiled_schema_has_every_key() -> None:
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError) as exc:
        pytest.skip(f"Gio not available: {exc}")
    source = Gio.SettingsSchemaSource.new_from_directory(str(SCHEMA_DIR), None, False)
    schema = source.lookup(SCHEMA_ID, False)
    assert schema is not None, "gschemas.compiled is missing the schema"
    assert set(schema.list_keys()) == _xml_keys(), "run glib-compile-schemas on the schemas dir"
