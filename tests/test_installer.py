"""Tests for the Axon Installer engine (pure-logic parts, no root needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "axon-installer"))

import install_engine


def _valid_config():
    return {
        "target_disk": "/dev/sda",
        "install_mode": "erase",
        "user": {
            "full_name": "Ada Lovelace",
            "username": "ada",
            "password": "hunter2",
            "hostname": "axon",
        },
        "ai": {
            "install_ollama": True,
            "ollama_model": "llama3.2:3b",
            "providers": [{"id": "anthropic", "api_key": "sk-test"}],
        },
    }


def test_valid_config_passes():
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
    assert install_engine.validate_config(cfg) == []


def test_part_node_naming():
    assert install_engine.part_node("/dev/sda", 3) == "/dev/sda3"
    assert install_engine.part_node("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
    assert install_engine.part_node("/dev/mmcblk0", 1) == "/dev/mmcblk0p1"
