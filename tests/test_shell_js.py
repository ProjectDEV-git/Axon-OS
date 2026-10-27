"""Check the GNOME Shell extension's JavaScript with node.

GNOME Shell itself can't run in CI, so this covers what can be checked
without it: every extension file must parse as an ES module, and the pure
logic modules (no ``gi://`` imports) are unit tested by ``tests/js/*.test.mjs``.

Files are copied to a temp dir as ``.mjs`` so any node version treats them as
ES modules. Skipped when node is not installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHELL_DIR = ROOT / "shell" / "axon-shell"
JS_TESTS = ROOT / "tests" / "js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

_LOCAL_IMPORT = re.compile(r"""(from\s+['"]\./[\w.-]+)\.js(['"])""")


def _copy_as_mjs(dest: Path) -> list[Path]:
    copied = []
    for src in sorted(SHELL_DIR.glob("*.js")):
        target = dest / (src.stem + ".mjs")
        target.write_text(_LOCAL_IMPORT.sub(r"\1.mjs\2", src.read_text(encoding="utf-8")))
        copied.append(target)
    return copied


def test_extension_files_parse(tmp_path: Path) -> None:
    for path in _copy_as_mjs(tmp_path):
        result = subprocess.run(
            [str(NODE), "--check", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


def test_pure_logic_unit_tests(tmp_path: Path) -> None:
    _copy_as_mjs(tmp_path)
    tests = []
    for src in sorted(JS_TESTS.glob("*.test.mjs")):
        shutil.copy(src, tmp_path / src.name)
        tests.append(str(tmp_path / src.name))
    assert tests, "no JS tests found"
    result = subprocess.run(
        [str(NODE), "--test", *tests], capture_output=True, text=True, check=False, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_suggestion_data_and_ranking_match_python(tmp_path: Path) -> None:
    import json

    from services import suggestions

    _copy_as_mjs(tmp_path)
    apps = [["firefox.desktop", "Firefox", "web"], ["code.desktop", "Visual Studio Code", "ide"]]
    spaces = ["Code", "Web", "Files"]
    queries = [
        "fi",
        "wi",
        "disk",
        "code",
        "find tax",
        "what is firefox",
        "web",
        "game mode",
        "game mode on",
        "turn game mode off",
        "is game mode on",
        "gamemode?",
        "game",
        "mode on",
        "install heroic",
        "install epic games",
        "install the gaming starter pack",
        "install retro",
        "install retro pack",
        "install minecraft",
        "open game center",
        "game center",
        "update proton",
        "install",
        "install zzzz",
        "update firefox",
    ]
    catalog = suggestions.load_catalog_entries()
    assert catalog is not None
    js_catalog = {"items": catalog[0], "presets": catalog[1]}
    script = (
        "import('./ui-logic.mjs').then(m => console.log(JSON.stringify({"
        "settings: m.SETTINGS_PANELS, commands: m.SUGGESTED_COMMANDS,"
        "categories: m.SUGGESTION_CATEGORIES, gameRows: m.GAME_MODE_ROWS,"
        f"rows: {json.dumps(queries)}.map(q => m.buildSuggestions(q, {json.dumps(apps)}, "
        f"{json.dumps(spaces)}, 3, {json.dumps(js_catalog)}).map(r => [r.category, r.title, r.subtitle, r.action]))"
        "})))"
    )
    result = subprocess.run(
        [str(NODE), "--input-type=module", "-e", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    js = json.loads(result.stdout)
    assert [tuple(x) for x in js["settings"]] == suggestions.SETTINGS_PANELS
    assert [tuple(x) for x in js["commands"]] == suggestions.SUGGESTED_COMMANDS
    assert js["categories"] == list(suggestions.CATEGORIES)
    assert {k: tuple(v) for k, v in js["gameRows"].items()} == suggestions.GAME_MODE_ROWS
    for query, js_rows in zip(queries, js["rows"], strict=True):
        py_rows = [
            [r.category, r.title, r.subtitle, r.action]
            for r in suggestions.build_suggestions(
                query, [tuple(a) for a in apps], spaces, catalog=catalog
            )
        ]
        assert js_rows == py_rows, query


def test_shell_motion_never_eases_in_or_bounces() -> None:
    """Emil Kowalski's rule: no ease-in on UI; bounce only after a flick (none here)."""
    import re

    for path in sorted(SHELL_DIR.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        bad = re.findall(r"AnimationMode\.(EASE_IN_\w+|\w*BOUNCE|\w*BACK|\w*ELASTIC)", text)
        assert not bad, f"{path.name}: {bad}"
        # Every Shell animation length comes from MOTION in ui-logic.js
        raw = re.findall(r"duration:\s*motion\((\d+)\)", text)
        assert not raw, f"{path.name}: use MOTION.* instead of {raw}"


def test_calculator_matches_python(tmp_path: Path) -> None:
    import json

    from services.calculator import calculate
    from tests.test_calculator import CASES, NOT_MATH

    _copy_as_mjs(tmp_path)
    queries = [q for q, _ in CASES] + NOT_MATH + ["7*(3+4)/2", "100-1-1", "2^0.5"]
    script = (
        "import('./ui-logic.mjs').then(m => console.log(JSON.stringify("
        f"{json.dumps(queries)}.map(q => m.calculate(q)))))"
    )
    result = subprocess.run(
        [str(NODE), "--input-type=module", "-e", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    js = json.loads(result.stdout)
    assert js == [calculate(q) for q in queries]


def test_st_widgets_get_spacing_from_css() -> None:
    """St.BoxLayout has no "spacing" property: passing one throws in GNOME 46 and
    stopped the whole extension from enabling. Spacing goes in CSS or `style`."""
    import re

    for path in sorted(SHELL_DIR.glob("*.js")):
        bad = re.findall(r"^\s*spacing:\s*\d+\s*,?$|[{,]\s*spacing:\s*\d+", path.read_text(), re.M)
        assert not bad, f"{path.name}: {bad}"
