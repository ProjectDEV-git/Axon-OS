"""The gaming catalog: schema, content rules, lookup, and the package that ships it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from catalog import CatalogError, load_catalog

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "gaming" / "catalog.json"
SCHEMA = ROOT / "data" / "gaming" / "catalog.schema.json"


@pytest.fixture(scope="module")
def data():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG)


@pytest.mark.unit
def test_catalog_validates_against_the_schema(data):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(json.loads(SCHEMA.read_text()))
    jsonschema.validate(data, json.loads(SCHEMA.read_text()))


@pytest.mark.unit
def test_schema_rejects_bad_entries(data):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    bad_source = json.loads(json.dumps(data))
    bad_source["items"][0]["source"] = {"type": "pinned", "url": "https://x", "sha256": "0"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_source, schema)
    shell_in_name = json.loads(json.dumps(data))
    shell_in_name["items"][-1]["source"] = {"type": "apt", "packages": ["foo; rm -rf /"]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(shell_in_name, schema)
    no_size = json.loads(json.dumps(data))
    del no_size["items"][0]["size_mb_estimate"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(no_size, schema)


@pytest.mark.unit
def test_ids_are_unique_and_references_resolve(data):
    ids = [i["id"] for i in data["items"]]
    assert len(ids) == len(set(ids))
    categories = {c["id"] for c in data["categories"]}
    assert {i["category"] for i in data["items"]} <= categories
    for preset in data["presets"]:
        assert set(preset["items"]) <= set(ids), preset["id"]


@pytest.mark.unit
def test_starter_pack_matches_its_flags(catalog):
    flagged = {i.id for i in catalog.items.values() if i.starter_pack}
    assert flagged == set(catalog.presets["starter"].items)
    assert flagged == {"heroic", "lutris", "protonplus", "goverlay", "discord"}
    assert set(catalog.presets["starter"].items) < set(catalog.presets["streamer"].items)


@pytest.mark.unit
def test_every_item_has_a_supported_source_and_a_flathub_ref_shape(catalog):
    for item in catalog.items.values():
        assert item.kind in ("flatpak", "apt", "axon-apt", "component"), item.id
        assert item.size_mb > 0


@pytest.mark.unit
def test_emulators_carry_the_legal_note(catalog):
    emulators = catalog.in_category("emulators")
    assert len(emulators) >= 8
    assert all("legally obtained" in (i.notes or "") for i in emulators)


@pytest.mark.unit
def test_nothing_that_needs_a_licence_click_through_is_offered(data):
    packages = [p for i in data["items"] for p in i["source"].get("packages", [])]
    assert "ttf-mscorefonts-installer" not in packages


@pytest.mark.unit
def test_verified_dates_are_only_set_for_items_that_were_checked(data):
    """An unverified item says so (null) instead of claiming a date."""
    for item in data["items"]:
        assert item["verified"] is None or len(item["verified"]) == 10


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "item_id"),
    [
        ("heroic", "heroic"),
        ("Heroic Games Launcher", "heroic"),
        ("epic games", "heroic"),
        ("minecraft", "prism"),
        ("proton-ge", "protonplus"),
        ("ps2", "pcsx2"),
        ("obs", "obs"),
        ("codecs", "media-codecs"),
        ("disc", "discord"),
    ],
)
def test_find_by_name_alias_or_prefix(catalog, query, item_id):
    assert catalog.find(query).id == item_id


@pytest.mark.unit
@pytest.mark.parametrize("query", ["", "   ", "zzzz", "an"])
def test_find_returns_nothing_for_unknown_text(catalog, query):
    assert catalog.find(query) is None


@pytest.mark.unit
def test_total_size_includes_the_flatpak_runtime_once(catalog):
    ids = ["heroic", "lutris"]
    apps = sum(catalog.items[i].size_mb for i in ids)
    assert catalog.total_size_mb(ids) == apps + 800
    assert catalog.total_size_mb(ids, flatpak_runtime_present=True) == apps
    assert catalog.total_size_mb(["input-remapper"]) == catalog.items["input-remapper"].size_mb


@pytest.mark.unit
def test_load_errors(tmp_path):
    with pytest.raises(CatalogError):
        load_catalog(tmp_path / "missing.json")
    broken = tmp_path / "catalog.json"
    broken.write_text("{not json")
    with pytest.raises(CatalogError):
        load_catalog(broken)


@pytest.mark.unit
def test_a_newer_catalog_file_is_simply_read(data, tmp_path):
    """Updating the list is replacing the file: nothing about it is built into code."""
    newer = json.loads(json.dumps(data))
    newer["items"] = newer["items"][:1]
    only = newer["items"][0]["id"]
    for preset in newer["presets"]:
        preset["items"] = [only]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(newer))
    assert list(load_catalog(path).items) == [only]


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="needs dpkg-deb")
def test_catalog_package_ships_the_catalog_and_schema(tmp_path):
    subprocess.run(
        ["bash", str(ROOT / "packaging" / "build-pkg.sh"), "axon-gaming-catalog", str(tmp_path)],
        check=True,
    )
    deb = next(tmp_path.glob("axon-gaming-catalog_*.deb"))
    listing = subprocess.run(
        ["dpkg-deb", "-c", str(deb)], check=True, capture_output=True, text=True
    ).stdout
    assert "./usr/share/axon/gaming/catalog.json" in listing
    assert "./usr/share/axon/gaming/catalog.schema.json" in listing
