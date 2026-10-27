"""Copy rules that must hold everywhere in the UI.

Cloud models (@provider/model) are supported, so no screen may promise that
AI is local-only or that nothing leaves the computer: the origin indicator
(services/ui_logic.py ModelOrigin) is the only place that may say "Local",
because it checks the selected model first.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [
    *sorted((ROOT / "apps").rglob("*.py")),
    *sorted((ROOT / "shell" / "axon-shell").glob("*.js")),
    *sorted((ROOT / "installer").rglob("*.py")),
    *sorted((ROOT / "installer").rglob("*.qml")),
]
UNCHECKED_PROMISES = re.compile(
    r"zero cloud|entirely local|fully private|100% local|zero data leaves|"
    r"no cloud connection|never leaves your|all ai runs locally|local[- ]only|data stays yours",
    re.IGNORECASE,
)
PICTOGRAPHS = re.compile("[\U0001f300-\U0001faff]")  # emoji pictographs


def test_no_screen_promises_ai_never_leaves_the_computer() -> None:
    hits = [
        f"{p.relative_to(ROOT)}:{i}: {line.strip()[:90]}"
        for p in SOURCES
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1)
        if UNCHECKED_PROMISES.search(line) and not line.lstrip().startswith(("#", "//", "*"))
    ]
    assert not hits, "check the selected model instead of promising:\n" + "\n".join(hits)


def test_no_emoji_pictographs_in_ui_strings() -> None:
    hits = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in SOURCES
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1)
        if PICTOGRAPHS.search(line)
    ]
    assert not hits, "use symbolic icons, not emoji: " + ", ".join(hits)
