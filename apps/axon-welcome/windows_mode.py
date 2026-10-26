"""How Windows programs (.exe/.msi) open: the choice made in the Welcome app.

The choice is one word in ``~/.config/axon-os/windows.conf``, read by
``axon-apex-run`` on every launch (double-click and ``./program.exe`` alike):

* ``apex``      APEX-WIN Tier 0: Wine with full access, no sandbox (default)
* ``sandboxed`` APEX-WIN Tier 2: bubblewrap namespaces with an isolated home
* ``off``       Windows programs do not run
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

MODES = ("apex", "sandboxed", "off")
DEFAULT_MODE = "apex"
LAUNCHER = "axon-apex-run"


def config_path() -> Path:
    """Return the per-user file that stores the choice."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "axon-os" / "windows.conf"


def load_mode() -> str:
    """Return the saved choice, or :data:`DEFAULT_MODE` if unset or unreadable."""
    try:
        mode = config_path().read_text().strip()
    except OSError:
        return DEFAULT_MODE
    return mode if mode in MODES else DEFAULT_MODE


def save_mode(mode: str) -> None:
    """Save *mode* atomically so the launcher never reads a half-written file.

    Raises:
        ValueError: if *mode* is not one of :data:`MODES`.
        OSError: if the file cannot be written.
    """
    if mode not in MODES:
        raise ValueError(f"unknown Windows program mode: {mode!r}")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(mode + "\n")
    tmp.replace(path)


def apex_installed() -> bool:
    """True if the APEX-WIN launcher is on this system."""
    return shutil.which(LAUNCHER) is not None or Path("/usr/bin", LAUNCHER).exists()
