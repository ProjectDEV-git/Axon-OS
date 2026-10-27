"""'Run anyway': the pause message is shared by Brain and every client."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from game_gate import GAME_MODE_MESSAGE as BRAIN_MESSAGE

from services.ui_logic import GAME_MODE_MESSAGE, is_game_mode_error, split_error

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.unit
def test_clients_and_brain_agree_on_the_message():
    assert GAME_MODE_MESSAGE == BRAIN_MESSAGE


@pytest.mark.unit
def test_recognised_inside_error_markers():
    _, error = split_error(f"[Error: {GAME_MODE_MESSAGE}]")
    assert error is not None and is_game_mode_error(error)
    _, error = split_error("[error] Ollama is not running")
    assert error is not None and not is_game_mode_error(error)


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_message_matches_python(tmp_path):
    src = (ROOT / "shell" / "axon-shell" / "ui-logic.js").read_text()
    (tmp_path / "ui-logic.mjs").write_text(src)
    out = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import('./ui-logic.mjs').then(m => console.log(JSON.stringify(m.GAME_MODE_MESSAGE)))"],
        cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    assert json.loads(out.stdout) == GAME_MODE_MESSAGE
