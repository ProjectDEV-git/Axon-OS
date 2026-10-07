"""Tests for the root update pipeline in system/axon-updater.py."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("gi")

UPDATER = Path(__file__).resolve().parent.parent / "system" / "axon-updater.py"


@pytest.fixture
def updater(monkeypatch):
    try:
        spec = importlib.util.spec_from_file_location("axon_updater", UPDATER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, ValueError) as exc:
        pytest.skip(f"GTK 4 / libadwaita not available: {exc}")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    return module


def test_progress_lines_reach_the_gui(updater, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(
        updater, "_run_cmd_logged", lambda cmd, extra_env=None: ran.append(cmd) or True
    )

    assert updater.run_headless_update(report_progress=True) == 0

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(updater.PROGRESS_PREFIX)
    ]
    fractions = [float(line[len(updater.PROGRESS_PREFIX) :].split(":", 2)[0]) for line in lines]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert updater.APT_UPGRADE_CMD in ran


def test_apt_failure_is_reported(updater, monkeypatch, capsys):
    monkeypatch.setattr(
        updater, "_run_cmd_logged", lambda cmd, extra_env=None: cmd != ["apt-get", "update"]
    )

    assert updater.run_headless_update(report_progress=True) == 1
    assert f"{updater.ERROR_PREFIX}Failed to update package lists." in capsys.readouterr().out


def test_no_progress_lines_for_the_timer(updater, monkeypatch, capsys):
    monkeypatch.setattr(updater, "_run_cmd_logged", lambda cmd, extra_env=None: True)

    assert updater.run_headless_update() == 0
    assert updater.PROGRESS_PREFIX not in capsys.readouterr().out


def test_refuses_to_run_without_root(updater, monkeypatch):
    monkeypatch.setattr(updater.os, "geteuid", lambda: 1000)
    assert updater.run_headless_update() == 1
