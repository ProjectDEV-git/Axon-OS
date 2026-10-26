"""Tests for the Welcome app's Windows-programs choice and how the launcher obeys it."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "axon-apex-run"

_spec = importlib.util.spec_from_file_location(
    "windows_mode", ROOT / "apps" / "axon-welcome" / "windows_mode.py"
)
windows_mode = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(windows_mode)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config"


class TestWindowsMode:
    def test_default_is_full_access(self, config_home):
        assert windows_mode.load_mode() == "apex"

    @pytest.mark.parametrize("mode", windows_mode.MODES)
    def test_round_trip(self, config_home, mode):
        windows_mode.save_mode(mode)
        assert windows_mode.load_mode() == mode
        assert (config_home / "axon-os" / "windows.conf").read_text() == mode + "\n"

    def test_unknown_value_reads_as_default(self, config_home):
        path = config_home / "axon-os" / "windows.conf"
        path.parent.mkdir(parents=True)
        path.write_text("everything\n")
        assert windows_mode.load_mode() == "apex"

    def test_rejects_unknown_mode(self, config_home):
        with pytest.raises(ValueError):
            windows_mode.save_mode("yolo")
        assert not (config_home / "axon-os" / "windows.conf").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="the launcher refuses to run as root")
class TestLauncherObeysChoice:
    """Run the real launcher with stub runner/wine that print their arguments."""

    @pytest.fixture
    def env(self, tmp_path, config_home):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name in ("runner", "wine", "notify-send"):
            stub = bin_dir / name
            stub.write_text(f'#!/bin/sh\necho "{name.upper()}: $*"\n')
            stub.chmod(0o755)
        (tmp_path / "game.exe").write_bytes(b"MZ")
        (tmp_path / "setup.msi").write_bytes(b"\xd0\xcf")
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(config_home),
            "AXON_APEX_RUNNER": str(bin_dir / "runner"),
        }
        return tmp_path, env

    def _run(self, env, *args, **extra):
        return subprocess.run(
            ["sh", str(LAUNCHER), *args],
            env={**env, **extra},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_default_runs_full_access(self, env):
        tmp, e = env
        out = self._run(e, str(tmp / "game.exe"))
        assert out.returncode == 0
        assert "RUNNER: --tier 0 --no-gui" in out.stdout

    def test_sandboxed_uses_tier_2(self, env):
        tmp, e = env
        windows_mode.save_mode("sandboxed")
        out = self._run(e, str(tmp / "game.exe"))
        assert "RUNNER: --tier 2 --no-gui" in out.stdout

    def test_off_refuses_and_notifies(self, env):
        tmp, e = env
        windows_mode.save_mode("off")
        out = self._run(e, str(tmp / "game.exe"))
        assert out.returncode == 1
        assert "RUNNER" not in out.stdout
        assert "NOTIFY-SEND" in out.stdout
        assert "turned off" in out.stderr

    def test_env_override_beats_off(self, env):
        tmp, e = env
        windows_mode.save_mode("off")
        out = self._run(e, str(tmp / "game.exe"), AXON_APEX_TIER="0")
        assert "RUNNER: --tier 0" in out.stdout

    def test_msi_needs_full_access(self, env):
        tmp, e = env
        assert "WINE: msiexec /i" in self._run(e, str(tmp / "setup.msi")).stdout
        windows_mode.save_mode("sandboxed")
        out = self._run(e, str(tmp / "setup.msi"))
        assert out.returncode == 1
        assert "WINE:" not in out.stdout

    def test_no_plain_wine_fallback_when_sandboxed(self, env):
        tmp, e = env
        windows_mode.save_mode("sandboxed")
        out = self._run(e, str(tmp / "game.exe"), AXON_APEX_RUNNER="/nonexistent")
        assert out.returncode == 1
        assert "WINE:" not in out.stdout
