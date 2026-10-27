"""Tests for command risk levels and the "Always allow" list."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.command_risk import (
    CHANGES_FILES,
    DELETES_FILES,
    NEEDS_ROOT,
    READ_ONLY,
    assess,
    can_always_allow,
)
from services.service_utils import ALLOWED_COMMANDS


@pytest.mark.parametrize(
    ("command", "level"),
    [
        ("ls -la", READ_ONLY),
        ("cat /etc/hostname", READ_ONLY),
        ("df -h", READ_ONLY),
        ("echo hi", READ_ONLY),
        ("paplay /usr/share/sounds/x.oga", READ_ONLY),
        ("notify-send Hi", READ_ONLY),
        ("sort names.txt", READ_ONLY),
        ("sort -o out.txt names.txt", CHANGES_FILES),
        ("sort --output=out.txt names.txt", CHANGES_FILES),
        ("sort -oout.txt names.txt", CHANGES_FILES),
        ("uniq in.txt", READ_ONLY),
        ("uniq in.txt out.txt", CHANGES_FILES),
        ("cp a b", CHANGES_FILES),
        ("rm -rf build", DELETES_FILES),
        ("sudo apt update", NEEDS_ROOT),
        ("pkexec ls", NEEDS_ROOT),
        ("mystery-tool --go", CHANGES_FILES),
        ("", CHANGES_FILES),
        ("echo 'unterminated", CHANGES_FILES),
    ],
)
def test_levels(command: str, level: str) -> None:
    assert assess(command).level == level


def test_effects_name_the_files() -> None:
    assert assess("cat /etc/hostname").effects == ["Reads: /etc/hostname"]
    assert assess("sort -o out.txt in.txt").effects == ["Writes: out.txt"]
    assert assess("rm a b").effects == ["Deletes: a, b"]
    assert assess("uptime").effects == ["Shows information."]


def test_every_allowlisted_binary_is_known() -> None:
    # A new allowlist entry needs a risk decision, not the cautious default
    for binary in ALLOWED_COMMANDS:
        assert "doesn't know" not in " ".join(assess(binary).effects), binary


def test_always_allow_only_lowest_risk() -> None:
    assert can_always_allow(assess("ls"))
    for cmd in ("sort -o x y", "rm x", "sudo ls", "mystery"):
        assert not can_always_allow(assess(cmd))


def test_labels() -> None:
    risk = assess("sudo ls")
    assert risk.needs_root and risk.label == "Needs admin rights"
    assert "administrator" in risk.description


class TestApprovals:
    def test_add_list_remove_clear(self, tmp_path: Path) -> None:
        from services.command_approvals import (
            add_approval,
            clear_approvals,
            is_approved,
            load_approvals,
            remove_approval,
        )

        path = tmp_path / "approved.json"
        assert load_approvals(path) == []
        assert add_approval("df -h", path)
        assert add_approval("uptime", path)
        assert load_approvals(path) == ["uptime", "df -h"]
        assert is_approved("df -h", path)
        assert not is_approved("df  -h", path)  # exact string only
        assert (path.stat().st_mode & 0o777) == 0o600
        remove_approval("df -h", path)
        assert load_approvals(path) == ["uptime"]
        clear_approvals(path)
        assert load_approvals(path) == []

    def test_refuses_risky_commands(self, tmp_path: Path) -> None:
        from services.command_approvals import add_approval, load_approvals

        path = tmp_path / "approved.json"
        for cmd in ("sort -o out in", "rm x", "sudo ls", "", "mystery"):
            assert not add_approval(cmd, path)
        assert load_approvals(path) == []

    def test_hand_edited_file_cannot_approve_risky_commands(self, tmp_path: Path) -> None:
        from services.command_approvals import is_approved

        path = tmp_path / "approved.json"
        path.write_text('["sort -o /etc/x y", "rm -rf ~", "ls"]')
        assert not is_approved("sort -o /etc/x y", path)
        assert not is_approved("rm -rf ~", path)
        assert is_approved("ls", path)

    def test_bad_files(self, tmp_path: Path) -> None:
        from services.command_approvals import load_approvals

        path = tmp_path / "approved.json"
        path.write_text("{")
        assert load_approvals(path) == []
        path.write_text('{"ls": true}')
        assert load_approvals(path) == []
