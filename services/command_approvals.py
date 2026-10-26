"""Exact commands the user chose to "Always allow" without asking.

Stored in ``~/.config/axon-os/approved-commands.json`` (mode 0600) as a list
of command strings. Matching is by the exact string. A stored command is
honoured only while it still passes the allowlist and still assesses as the
lowest risk level, so editing the file can't approve a riskier command.
The list can be viewed and cleared in Axon Settings → Preferences.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from command_risk import assess, can_always_allow

APPROVALS_PATH = Path.home() / ".config" / "axon-os" / "approved-commands.json"
_MAX_ENTRIES = 200


def load_approvals(path: Path | None = None) -> list[str]:
    """Return the saved commands; a missing or corrupt file gives []."""
    path = path or APPROVALS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, str) and c.strip()][:_MAX_ENTRIES]


def _write(commands: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(commands, fh, indent=2)


def add_approval(command: str, path: Path | None = None) -> bool:
    """Remember *command*; refuses anything above the lowest risk level."""
    if not command.strip() or not can_always_allow(assess(command)):
        return False
    path = path or APPROVALS_PATH
    commands = [c for c in load_approvals(path) if c != command]
    commands.insert(0, command)
    _write(commands[:_MAX_ENTRIES], path)
    return True


def remove_approval(command: str, path: Path | None = None) -> None:
    path = path or APPROVALS_PATH
    _write([c for c in load_approvals(path) if c != command], path)


def clear_approvals(path: Path | None = None) -> None:
    _write([], path or APPROVALS_PATH)


def is_approved(command: str, path: Path | None = None) -> bool:
    """True when *command* was always-allowed and is still lowest risk.

    Callers must still run the allowlist check first.
    """
    return command in load_approvals(path) and can_always_allow(assess(command))
