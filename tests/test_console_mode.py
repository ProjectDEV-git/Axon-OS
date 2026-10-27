"""Console session, FPS overlay defaults and gaming shortcuts."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "packaging" / "axon-console"
SESSION = CONSOLE / "files" / "usr" / "bin" / "axon-console-session"
SELECT = CONSOLE / "files" / "usr" / "bin" / "steamos-session-select"

# Every key MangoHud 0.6.9.1 (Ubuntu 24.04) documents in its own example config; checked on
# 2026-10-27 against /usr/share/doc/mangohud/MangoHud.conf.example.gz.
MANGOHUD_KEYS = {
    "legacy_layout", "horizontal", "position", "font_size", "background_alpha", "round_corners",
    "fps", "frametime", "cpu_stats", "cpu_temp", "gpu_stats", "gpu_temp", "toggle_hud",
    "toggle_hud_position",
}  # fmt: skip


# -- Axon Console session ---------------------------------------------------------------------


def stub(directory: Path, name: str, body: str = "") -> None:
    path = directory / name
    path.write_text(f'#!/bin/sh\necho "{name} $*" >> "$STUB_LOG"\n{body}\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def box(tmp_path):
    """A fake home and a PATH with stub gamescope, steam and axon-game-mode."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "home").mkdir()
    for name in ("gamescope", "steam", "axon-game-mode", "loginctl", "pkill"):
        stub(bin_dir, name)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "STUB_LOG": str(tmp_path / "calls.log"),
    }
    return tmp_path, bin_dir, env


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def run(script: Path, env: dict, *args: str):
    return subprocess.run(
        ["sh", str(script), *args], env=env, capture_output=True, text=True, timeout=30
    )


@pytest.mark.unit
def test_session_starts_game_mode_then_steam_big_picture_in_gamescope(box):
    tmp, _, env = box
    assert run(SESSION, env).returncode == 0
    log = calls(tmp)
    assert log[0].startswith("axon-game-mode on --app-id console --pid ")
    assert log[1] == "gamescope -e -f -- steam -gamepadui"
    assert log[-1].startswith("axon-game-mode off --pid ")
    assert log[0].split()[-1] == log[-1].split()[-1], "the same session pid is released"


@pytest.mark.unit
def test_game_mode_is_released_even_when_gamescope_fails(box):
    tmp, bin_dir, env = box
    stub(bin_dir, "gamescope", "exit 3")
    result = run(SESSION, env)
    assert result.returncode == 3
    assert calls(tmp)[-1].startswith("axon-game-mode off --pid ")


@pytest.mark.unit
def test_gamescope_options_come_from_the_environment_or_the_config_file(box):
    tmp, _, env = box
    run(SESSION, {**env, "AXON_GAMESCOPE_ARGS": "-W 1920 -H 1080 -r 60"})
    assert "gamescope -e -f -W 1920 -H 1080 -r 60 -- steam -gamepadui" in calls(tmp)
    (tmp / "calls.log").unlink()
    conf = tmp / "home" / ".config" / "axon-os"
    conf.mkdir(parents=True)
    (conf / "console.conf").write_text("-W 1280 -H 800\nignored second line\n")
    run(SESSION, env)
    assert "gamescope -e -f -W 1280 -H 800 -- steam -gamepadui" in calls(tmp)


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["gamescope", "steam"])
def test_session_logs_and_exits_when_something_it_needs_is_missing(box, missing):
    tmp, bin_dir, env = box
    (bin_dir / missing).unlink()
    # Real tools the script needs, but only those: a developer's own gamescope must not leak in
    for tool in ("mkdir", "date", "head"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    env = {**env, "PATH": str(bin_dir)}
    result = subprocess.run(
        ["/bin/sh", str(SESSION)], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 1
    log = (tmp / "home" / ".local" / "state" / "axon-console.log").read_text()
    assert f"{missing} is not installed" in log
    assert not any(c.startswith("axon-game-mode") for c in calls(tmp)), "no game mode for nothing"


@pytest.mark.unit
def test_session_still_runs_without_the_game_mode_command(box):
    tmp, bin_dir, env = box
    (bin_dir / "axon-game-mode").unlink()
    assert run(SESSION, env).returncode == 0
    assert "gamescope -e -f -- steam -gamepadui" in calls(tmp)


@pytest.mark.unit
def test_return_to_desktop_ends_the_login_session(box):
    tmp, _, env = box
    assert run(SELECT, {**env, "XDG_SESSION_ID": "7"}, "desktop").returncode == 0
    assert calls(tmp) == ["loginctl terminate-session 7"]


@pytest.mark.unit
def test_session_select_without_a_session_id_stops_gamescope_and_ignores_other_targets(box):
    tmp, _, env = box
    run(SELECT, env, "gnome")
    assert calls(tmp) == ["pkill -x gamescope"]
    (tmp / "calls.log").unlink()
    run(SELECT, env, "gamescope")
    run(SELECT, env)
    assert calls(tmp) == []


@pytest.mark.unit
def test_console_package_declares_its_login_session():
    entry = (
        CONSOLE / "files" / "usr" / "share" / "wayland-sessions" / "axon-console.desktop"
    ).read_text()
    assert "Name=Axon Console" in entry
    assert "Exec=/usr/bin/axon-console-session" in entry
    depends = re.search(r"^Depends:\s*(.+)$", (CONSOLE / "control").read_text(), re.M).group(1)
    for needed in ("gamescope", "steam-installer", "axon-game-mode"):
        assert needed in depends
    for script in (SESSION, SELECT):
        assert os.access(script, os.X_OK)


@pytest.mark.unit
def test_image_build_leaves_the_console_out_until_gamescope_exists():
    text = (ROOT / "build" / "config" / "chroot-setup.sh").read_text()
    assert "apt-cache policy gamescope" in text
    assert "axon-console_*.deb" in text


# -- FPS overlay defaults ---------------------------------------------------------------------------


def mangohud_settings() -> dict[str, str]:
    out = {}
    for line in (ROOT / "data" / "mangohud" / "MangoHud.conf").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


@pytest.mark.unit
def test_overlay_config_only_uses_keys_mangohud_documents():
    unknown = set(mangohud_settings()) - MANGOHUD_KEYS
    assert not unknown, f"unknown MangoHud keys: {unknown}"


@pytest.mark.unit
def test_overlay_shows_fps_frametime_load_and_temperatures_in_a_corner():
    keys = mangohud_settings()
    for wanted in ("fps", "frametime", "cpu_stats", "cpu_temp", "gpu_stats", "gpu_temp"):
        assert wanted in keys
    assert keys["position"] == "top-left"
    assert keys["toggle_hud"] == "Shift_R+F12"
    assert "no_display" not in keys, "visible whenever MANGOHUD=1 turns the overlay on"


@pytest.mark.unit
def test_overlay_is_installed_where_mangohud_looks():
    deb = (ROOT / "packaging" / "build-deb.sh").read_text()
    assert "/etc/skel/.config/MangoHud/MangoHud.conf" in deb
    assert "/usr/share/axon/mangohud/MangoHud.conf" in deb


# -- Gaming shortcuts --------------------------------------------------------------------------------


def shortcut_groups():
    source = (ROOT / "apps" / "axon-shortcuts" / "main.py").read_text()
    start = source.index("SHORTCUTS = [")
    end = source.index("\n]\n", start)
    namespace = {"N_": lambda s: s}
    exec(source[start : end + 3], namespace)
    return namespace["SHORTCUTS"]


@pytest.mark.unit
def test_gaming_shortcuts_are_listed_and_match_the_overlay_config():
    groups = {g["title"]: g["bindings"] for g in shortcut_groups()}
    keys = dict(groups["Gaming"])
    assert "Right Shift + F12" in keys and "Right Shift + F11" in keys
    assert mangohud_settings()["toggle_hud"] == "Shift_R+F12"
    assert mangohud_settings()["toggle_hud_position"] == "Shift_R+F11"


@pytest.mark.unit
def test_no_shortcut_is_listed_twice_across_axon_shell_groups():
    seen: dict[str, str] = {}
    for group in shortcut_groups():
        if group["title"] == "Axon Terminal":  # app-local keys may repeat shell ones
            continue
        for combo, _text in group["bindings"]:
            assert combo not in seen, f"{combo!r} in {group['title']} and {seen[combo]}"
            seen[combo] = group["title"]


@pytest.mark.unit
def test_gaming_shortcuts_do_not_collide_with_the_shell_keybindings():
    schema = "\n".join(
        p.read_text() for p in (ROOT / "shell" / "axon-shell" / "schemas").glob("*.xml")
    )
    assert "F12" not in schema and "F11" not in schema
