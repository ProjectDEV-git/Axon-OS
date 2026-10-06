"""Tests for the Axon Installer engine (pure-logic parts, no root needed)."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "axon-installer"))

import install_engine


def _valid_config():
    return {
        "target_disk": "/dev/sda",
        "install_mode": "erase",
        "user": {
            "full_name": "Ada Lovelace",
            "username": "ada",
            "password": "hunter22",
            "hostname": "axon",
        },
        "ai": {
            "install_ollama": True,
            "ollama_model": "llama3.2:3b",
            "providers": [{"id": "anthropic", "api_key": "sk-test"}],
        },
    }


def test_valid_config_passes():
    with patch("os.path.exists", return_value=True), patch("os.stat") as mock_stat:
        import stat as _stat
        mock_stat.return_value.st_mode = _stat.S_IFBLK | 0o640
        assert install_engine.validate_config(_valid_config()) == []


def test_rejects_bad_disk():
    cfg = _valid_config()
    cfg["target_disk"] = "sda"
    assert any("target_disk" in p for p in install_engine.validate_config(cfg))


def test_rejects_bad_install_mode():
    cfg = _valid_config()
    cfg["install_mode"] = "format-c"
    assert any("install_mode" in p for p in install_engine.validate_config(cfg))


def test_rejects_invalid_username():
    for bad in ("Ada", "1ada", "", "ada lovelace", "a" * 40):
        cfg = _valid_config()
        cfg["user"]["username"] = bad
        assert any("username" in p for p in install_engine.validate_config(cfg)), bad


def test_rejects_short_password():
    cfg = _valid_config()
    cfg["user"]["password"] = "abc"
    assert any("password" in p for p in install_engine.validate_config(cfg))


def test_rejects_invalid_hostname():
    cfg = _valid_config()
    cfg["user"]["hostname"] = "-bad-host-"
    assert any("hostname" in p for p in install_engine.validate_config(cfg))


def test_rejects_unknown_provider():
    cfg = _valid_config()
    cfg["ai"]["providers"] = [{"id": "skynet", "api_key": "x"}]
    assert any("provider" in p for p in install_engine.validate_config(cfg))


def test_rejects_provider_without_key():
    cfg = _valid_config()
    cfg["ai"]["providers"] = [{"id": "openai", "api_key": "  "}]
    assert any("api_key" in p for p in install_engine.validate_config(cfg))


def test_ollama_provider_needs_no_key():
    cfg = _valid_config()
    cfg["ai"]["providers"] = [{"id": "ollama"}]
    with patch("os.path.exists", return_value=True), patch("os.stat") as mock_stat:
        import stat as _stat
        mock_stat.return_value.st_mode = _stat.S_IFBLK | 0o640
        assert install_engine.validate_config(cfg) == []


def test_part_node_naming():
    assert install_engine.part_node("/dev/sda", 3) == "/dev/sda3"
    assert install_engine.part_node("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
    assert install_engine.part_node("/dev/mmcblk0", 1) == "/dev/mmcblk0p1"


def test_rejects_newline_in_password():
    cfg = _valid_config()
    cfg["user"]["password"] = "hunter22\nroot:owned"
    assert any("newline" in p for p in install_engine.validate_config(cfg))


def test_is_live_session_reads_cmdline(tmp_path):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("BOOT_IMAGE=/casper/vmlinuz boot=casper quiet splash ---\n")
    assert install_engine.is_live_session(str(cmdline)) is True
    cmdline.write_text("BOOT_IMAGE=/boot/vmlinuz root=UUID=x ro quiet splash\n")
    assert install_engine.is_live_session(str(cmdline)) is False
    assert install_engine.is_live_session(str(tmp_path / "missing")) is False


def test_engine_refuses_outside_live_session(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    with (
        patch("sys.argv", ["install_engine.py", str(cfg_path)]),
        patch("os.geteuid", return_value=0),
        patch.object(install_engine, "is_live_session", return_value=False),
        patch.object(install_engine, "install") as mock_install,
        pytest.raises(SystemExit),
    ):
        install_engine.main()
    mock_install.assert_not_called()
    assert cfg_path.exists()  # nothing was read or deleted


def test_strip_live_artifacts_removes_root_helper(tmp_path):
    target = tmp_path / "target"
    for rel in (
        install_engine.ENGINE_WRAPPER,
        install_engine.ENGINE_POLICY,
        "/etc/sudoers.d/casper",
    ):
        f = target / rel.lstrip("/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    app_dir = target / install_engine.INSTALLER_APP_DIR.lstrip("/")
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("x")
    (target / "etc" / "machine-id").write_text("abc")
    with (
        patch.object(install_engine, "TARGET", str(target)),
        patch.object(install_engine, "run_chroot"),
    ):
        install_engine.strip_live_artifacts()
    assert not (target / install_engine.ENGINE_WRAPPER.lstrip("/")).exists()
    assert not (target / install_engine.ENGINE_POLICY.lstrip("/")).exists()
    assert not app_dir.exists()
