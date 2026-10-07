"""Tests for the built-in diagnostics (services/diagnostics.py, axon-diagnose)."""

from __future__ import annotations

import json
import urllib.error
from unittest import mock

import pytest

from services import diagnostics as diag
from services.diagnostics import FAIL, INFO, OK, WARN, CheckResult, Report


def _fake_run(responses: dict[str, tuple[int, str] | None]):
    """Return a ``_run`` replacement keyed on the first two words of the command."""

    def run(cmd, timeout=10):
        for key, value in responses.items():
            if " ".join(cmd).startswith(key):
                return value
        return None

    return run


def test_report_status_is_worst_result():
    report = Report(
        "now",
        {},
        [
            CheckResult("A", "a", OK, ""),
            CheckResult("A", "b", WARN, ""),
            CheckResult("B", "c", INFO, ""),
        ],
    )
    assert report.status == WARN
    report.results.append(CheckResult("B", "d", FAIL, ""))
    assert report.status == FAIL
    assert report.counts() == {OK: 1, INFO: 1, WARN: 1, FAIL: 1}


def test_report_text_groups_categories_and_shows_fixes_for_problems_only():
    report = Report(
        "2026-10-07",
        {"OS": "Axon OS"},
        [
            CheckResult("AI", "Ollama", FAIL, "down", ["detail"], hint="start it"),
            CheckResult("AI", "Models", OK, "2 installed", hint="never shown"),
        ],
    )
    text = report.to_text()
    assert "OS: Axon OS" in text
    assert "== AI ==" in text
    assert "[FAIL] Ollama: down" in text
    assert "Fix: start it" in text
    assert "never shown" not in text
    assert "\033[" not in text


def test_report_json_round_trips():
    report = Report("now", {"Kernel": "6.8"}, [CheckResult("AI", "Ollama", OK, "up")])
    data = json.loads(report.to_json())
    assert data["status"] == OK
    assert data["results"][0]["name"] == "Ollama"


def test_axon_services_states():
    show = (
        "Id=axon-brain.service\nLoadState=loaded\nActiveState=active\n\n"
        "Id=axon-search.service\nLoadState=loaded\nActiveState=failed\n\n"
        "Id=axon-voice.service\nLoadState=not-found\nActiveState=inactive"
    )
    with mock.patch.object(diag, "_run", _fake_run({"systemctl --user show": (0, show)})):
        results = {r.name: r for r in diag.check_axon_services()}
    assert results["axon-brain"].status == OK
    assert results["axon-search"].status == FAIL
    assert results["axon-voice"].summary == "not installed"
    assert results["axon-context"].status == WARN


def test_axon_services_without_user_manager():
    with mock.patch.object(diag, "_run", return_value=None):
        (result,) = diag.check_axon_services()
    assert result.status == WARN


def test_ollama_lists_installed_models():
    def get(path, timeout=3):
        if path == "/api/version":
            return {"version": "0.3.12"}
        return {"models": [{"name": "qwen2.5:7b", "size": 4_700_000_000}, {"name": "llama3.2"}]}

    with mock.patch.object(diag, "_ollama_get", side_effect=get):
        server, models = diag.check_ollama()
    assert server.status == OK
    assert "0.3.12" in server.summary
    assert models.summary == "2 installed"
    assert models.details[0] == "llama3.2"
    assert models.details[1].startswith("qwen2.5:7b (4.4 GB")


def test_ollama_without_models_warns():
    with mock.patch.object(diag, "_ollama_get", side_effect=[{"version": "1"}, {"models": []}]):
        _, models = diag.check_ollama()
    assert models.status == WARN


@pytest.mark.parametrize(("installed", "hint"), [(True, "systemctl"), (False, "axon-ollama-setup")])
def test_ollama_down(installed, hint):
    with (
        mock.patch.object(diag, "_ollama_get", side_effect=urllib.error.URLError("refused")),
        mock.patch.object(
            diag.shutil, "which", return_value="/usr/bin/ollama" if installed else None
        ),
    ):
        (result,) = diag.check_ollama()
    assert result.status == FAIL
    assert hint in result.hint


@pytest.mark.parametrize(
    ("available_kb", "expected"), [(8_000_000, OK), (1_000_000, WARN), (300_000, FAIL)]
)
def test_memory_thresholds(tmp_path, available_kb, expected):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        f"MemTotal:       10000000 kB\nMemAvailable:   {available_kb} kB\nSwapTotal: 0 kB\n"
    )
    (result,) = diag.check_memory(meminfo)
    assert result.status == expected
    assert result.details == ["Swap: none"]


@pytest.mark.parametrize(("free_gb", "expected"), [(50, OK), (5, WARN), (1, FAIL)])
def test_disk_thresholds(free_gb, expected):
    usage = mock.Mock(total=100 * 1024**3, used=(100 - free_gb) * 1024**3, free=free_gb * 1024**3)
    with mock.patch.object(diag.shutil, "disk_usage", return_value=usage):
        results = diag.check_disk()
    assert results
    assert results[0].status == expected


def test_gpu_nvidia_without_driver_warns():
    lspci = "01:00.0 VGA compatible controller: NVIDIA Corporation AD107 [GeForce RTX 4060]"
    with mock.patch.object(diag, "_run", _fake_run({"lspci": (0, lspci)})):
        (result,) = diag.check_gpu()
    assert result.status == WARN
    assert "ubuntu-drivers" in result.hint


def test_gpu_nvidia_smi():
    out = "NVIDIA GeForce RTX 4060, 550.120, 512, 8188"
    with mock.patch.object(diag, "_run", _fake_run({"nvidia-smi": (0, out)})):
        (result,) = diag.check_gpu()
    assert result.status == OK
    assert "driver 550.120" in result.details[0]


def test_network_offline():
    with mock.patch.object(diag.socket, "getaddrinfo", side_effect=OSError("no dns")):
        (result,) = diag.check_network()
    assert result.status == FAIL


def test_updates_healthy(tmp_path):
    source = tmp_path / "axon-os.sources"
    source.write_text("Types: deb\n")
    responses = {
        "dpkg-query": (0, "1.1.0"),
        "systemctl show -p UnitFileState": (
            0,
            "UnitFileState=enabled\nActiveState=active\nLastTriggerUSec=Tue 2026-10-06 09:00:00 UTC",
        ),
        "systemctl show -p Result": (0, "Result=success\nExecMainStatus=0"),
        "apt-get -s": (0, "Inst libfoo [1] (2 Ubuntu)\nConf libfoo (2 Ubuntu)"),
    }
    with (
        mock.patch.object(diag, "_run", _fake_run(responses)),
        mock.patch.object(diag, "AXON_APT_SOURCE", source),
    ):
        results = {r.name: r for r in diag.check_updates()}
    assert results["Axon package"].summary == "axon-os 1.1.0"
    assert results["Update source"].status == OK
    assert results["Automatic updates"].status == OK
    assert "Last update" not in results
    assert results["Pending updates"].summary == "1 package update(s) waiting"


def test_updates_failed_run_and_timer_off(tmp_path):
    responses = {
        "dpkg-query": (1, ""),
        "systemctl show -p UnitFileState": (0, "UnitFileState=disabled\nActiveState=inactive"),
        "systemctl show -p Result": (0, "Result=exit-code\nExecMainStatus=100"),
    }
    with (
        mock.patch.object(diag, "_run", _fake_run(responses)),
        mock.patch.object(diag, "AXON_APT_SOURCE", tmp_path / "missing"),
    ):
        results = {r.name: r for r in diag.check_updates()}
    assert results["Axon package"].status == WARN
    assert results["Update source"].status == WARN
    assert results["Automatic updates"].status == WARN
    assert results["Last update"].status == FAIL
    assert "100" in results["Last update"].summary


def test_failed_units():
    responses = {
        "systemctl list-units": (0, "foo.service loaded failed failed Foo"),
        "systemctl --user list-units": (0, ""),
    }
    with mock.patch.object(diag, "_run", _fake_run(responses)):
        system, user = diag.check_failed_units()
    assert system.status == FAIL
    assert system.details == ["foo.service"]
    assert user.status == OK


def test_crashing_check_does_not_hide_others():
    def boom():
        raise RuntimeError("bad")

    fine = mock.Mock(return_value=[CheckResult("X", "fine", OK, "ok")])
    with mock.patch.dict(diag.CHECKS, {"boom": boom, "fine": fine}, clear=True):
        report = diag.run_diagnostics()
    assert [r.name for r in report.results] == ["boom", "fine"]
    assert report.results[0].status == WARN


def test_cli_json_output_and_exit_code(tmp_path, capsys):
    out_file = tmp_path / "report.json"
    failing = mock.Mock(return_value=[CheckResult("AI", "Ollama", FAIL, "down")])
    with mock.patch.dict(diag.CHECKS, {"ai": failing}, clear=True):
        code = diag.main(["--json", "-o", str(out_file)])
    assert code == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == FAIL
    assert json.loads(out_file.read_text())["results"][0]["summary"] == "down"
