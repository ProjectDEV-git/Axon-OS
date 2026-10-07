#!/usr/bin/env python3
"""Axon OS built-in diagnostics.

Runs a set of read-only health checks (Axon services, Ollama and its models,
disk, memory, GPU, network, updates and failed systemd units) and builds a
report that can be printed, saved, or pasted into a bug report.

The same checks back two front ends: the ``axon-diagnose`` command line tool
(this file run as a script) and the Diagnostics window in Axon Settings.

Nothing here changes the system, needs root, or sends data anywhere except
the local Ollama server and one TCP connection to test the network. The report
leaves out the user name, host name and home path so it is safe to share.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import OLLAMA_BASE_URL

OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"

_STATUS_ORDER = {OK: 0, INFO: 0, WARN: 1, FAIL: 2}
_STATUS_LABEL = {OK: "OK", INFO: "INFO", WARN: "WARN", FAIL: "FAIL"}

# systemd user units shipped by the axon-os package (services/*/axon-*.service).
AXON_USER_UNITS = (
    "axon-brain",
    "axon-context",
    "axon-file-indexer",
    "axon-gui-agent",
    "axon-sandbox",
    "axon-search",
    "axon-voice",
)

UPDATE_TIMER = "axon-update-auto.timer"
UPDATE_SERVICE = "axon-update-auto.service"
AXON_APT_SOURCE = Path("/etc/apt/sources.list.d/axon-os.sources")
NETWORK_PROBE_HOST = "projectdev-git.github.io"

# Free-space thresholds, in bytes, for warnings and failures.
DISK_WARN_BYTES = 10 * 1024**3
DISK_FAIL_BYTES = 2 * 1024**3


@dataclass
class CheckResult:
    """The outcome of one diagnostic check.

    Attributes:
        category: Group the check belongs to, e.g. "AI" or "System".
        name: Short check name shown to the user.
        status: One of ``ok``, ``info``, ``warn`` or ``fail``.
        summary: One-line result.
        details: Extra lines shown under the summary.
        hint: What the user can do about a warning or failure.
    """

    category: str
    name: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)
    hint: str = ""


@dataclass
class Report:
    """A complete diagnostics run."""

    created: str
    system: dict[str, str]
    results: list[CheckResult]

    @property
    def status(self) -> str:
        """Worst status across all checks."""
        worst = OK
        for result in self.results:
            if _STATUS_ORDER.get(result.status, 0) > _STATUS_ORDER[worst]:
                worst = result.status
        return worst

    def counts(self) -> dict[str, int]:
        """Number of checks per status."""
        counts = {OK: 0, INFO: 0, WARN: 0, FAIL: 0}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    def to_dict(self) -> dict:
        """Report as plain data, for JSON export."""
        return {
            "created": self.created,
            "status": self.status,
            "system": self.system,
            "results": [asdict(r) for r in self.results],
        }

    def to_json(self) -> str:
        """Report as indented JSON."""
        return json.dumps(self.to_dict(), indent=2)

    def to_text(self, color: bool = False) -> str:
        """Report as plain text, ready to paste into a bug report."""
        colors = {OK: "\033[32m", INFO: "\033[36m", WARN: "\033[33m", FAIL: "\033[31m"}
        reset = "\033[0m"

        def tag(status: str) -> str:
            label = f"[{_STATUS_LABEL.get(status, status.upper()):>4}]"
            return f"{colors[status]}{label}{reset}" if color and status in colors else label

        lines = ["Axon OS diagnostics report", f"Created: {self.created}"]
        lines += [f"{key}: {value}" for key, value in self.system.items()]
        counts = self.counts()
        lines.append(f"Result: {counts[OK]} ok, {counts[WARN]} warnings, {counts[FAIL]} failures")
        category = None
        for result in self.results:
            if result.category != category:
                category = result.category
                lines += ["", f"== {category} =="]
            lines.append(f"{tag(result.status)} {result.name}: {result.summary}")
            lines += [f"       {d}" for d in result.details]
            if result.hint and result.status in (WARN, FAIL):
                lines.append(f"       Fix: {result.hint}")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 10) -> tuple[int, str] | None:
    """Run a command and return ``(returncode, stdout)``.

    Returns None when the program is not installed or does not finish in time.
    """
    if shutil.which(cmd[0]) is None:
        return None
    try:
        proc = subprocess.run(  # nosec B603 - fixed argument lists, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode, proc.stdout.strip()


def _human_bytes(size: float) -> str:
    """Format a byte count as e.g. ``4.2 GB``."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    """Parse /proc/meminfo into bytes per field."""
    info: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
    except OSError:
        pass
    return info


def _ollama_get(path: str, timeout: float = 3) -> dict:
    """GET a JSON endpoint from the local Ollama server."""
    url = f"{OLLAMA_BASE_URL}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 - fixed local URL
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"unexpected reply from {url}")
    return data


def _unit_states(units: list[str], user: bool) -> dict[str, tuple[str, str]] | None:
    """Return ``(LoadState, ActiveState)`` per unit, or None when systemd is unreachable."""
    cmd = ["systemctl"] + (["--user"] if user else [])
    out = _run([*cmd, "show", "-p", "Id", "-p", "LoadState", "-p", "ActiveState", "--", *units])
    if out is None or (out[0] != 0 and not out[1]):
        return None
    states: dict[str, tuple[str, str]] = {}
    for block in out[1].split("\n\n"):
        props = dict(line.partition("=")[::2] for line in block.splitlines())
        unit = props.get("Id", "").removesuffix(".service")
        if unit:
            states[unit] = (props.get("LoadState", ""), props.get("ActiveState", "unknown"))
    return states


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_axon_services() -> list[CheckResult]:
    """Check that each Axon background service is running."""
    states = _unit_states([f"{u}.service" for u in AXON_USER_UNITS], user=True)
    if states is None:
        return [
            CheckResult(
                "Axon services",
                "Background services",
                WARN,
                "Could not reach the user service manager",
                hint=(
                    "Run axon-diagnose as your normal user, not with sudo."
                    if os.geteuid() == 0
                    else "Run axon-diagnose from your desktop session."
                ),
            )
        ]
    results = []
    for unit in AXON_USER_UNITS:
        load, state = states.get(unit, ("", "unknown"))
        if load == "not-found":
            status, summary = WARN, "not installed"
        elif state == "active":
            status, summary = OK, "running"
        elif state == "failed":
            status, summary = FAIL, "failed"
        elif state in ("activating", "reloading"):
            status, summary = INFO, "starting"
        else:
            status, summary = WARN, f"not running ({state})"
        results.append(
            CheckResult(
                "Axon services",
                unit,
                status,
                summary,
                hint=(
                    f"Restart it with: systemctl --user restart {unit}. "
                    f"See why with: journalctl --user -u {unit} -n 50"
                ),
            )
        )
    return results


def check_ollama() -> list[CheckResult]:
    """Check the Ollama server and list the models it has installed."""
    try:
        version = _ollama_get("/api/version").get("version", "unknown")
        tags = _ollama_get("/api/tags")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        installed = shutil.which("ollama") is not None
        return [
            CheckResult(
                "AI",
                "Ollama",
                FAIL,
                "Ollama is not responding" if installed else "Ollama is not installed",
                details=[f"{OLLAMA_BASE_URL}: {getattr(exc, 'reason', exc)}"],
                hint=(
                    "Start it with: sudo systemctl start ollama"
                    if installed
                    else "Install it with: axon-ollama-setup"
                ),
            )
        ]

    results = [CheckResult("AI", "Ollama", OK, f"running, version {version}")]
    models = tags.get("models") or []
    if not models:
        results.append(
            CheckResult(
                "AI",
                "Installed models",
                WARN,
                "No models installed",
                hint="Download one with: ollama pull llama3.2",
            )
        )
        return results
    details = []
    for model in sorted(models, key=lambda m: m.get("name", "")):
        size = model.get("size")
        size_text = f" ({_human_bytes(size)})" if isinstance(size, int) else ""
        details.append(f"{model.get('name', '?')}{size_text}")
    results.append(
        CheckResult("AI", "Installed models", OK, f"{len(models)} installed", details=details)
    )
    return results


def check_disk() -> list[CheckResult]:
    """Check free space on the system and home file systems."""
    results = []
    seen: set[int] = set()
    for label, path in (("System disk", Path("/")), ("Home folder", Path.home())):
        try:
            dev = path.stat().st_dev
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        pct = usage.used / usage.total * 100 if usage.total else 0
        summary = (
            f"{_human_bytes(usage.free)} free of {_human_bytes(usage.total)} ({pct:.0f}% used)"
        )
        if usage.free < DISK_FAIL_BYTES:
            status = FAIL
        elif usage.free < DISK_WARN_BYTES:
            status = WARN
        else:
            status = OK
        results.append(
            CheckResult(
                "System",
                label,
                status,
                summary,
                hint="Free up space: remove unused AI models (ollama rm NAME) or old files.",
            )
        )
    return results


def check_memory(meminfo_path: Path = Path("/proc/meminfo")) -> list[CheckResult]:
    """Check total and available memory and swap."""
    info = _read_meminfo(meminfo_path)
    total = info.get("MemTotal", 0)
    if not total:
        return [CheckResult("System", "Memory", INFO, "Could not read memory information")]
    available = info.get("MemAvailable", 0)
    swap = info.get("SwapTotal", 0)
    details = [f"Swap: {_human_bytes(swap)}" if swap else "Swap: none"]
    pct_free = available / total * 100
    if pct_free < 5:
        status = FAIL
    elif pct_free < 15:
        status = WARN
    else:
        status = OK
    return [
        CheckResult(
            "System",
            "Memory",
            status,
            f"{_human_bytes(available)} available of {_human_bytes(total)}",
            details=details,
            hint="Close apps you are not using, or pick a smaller AI model.",
        )
    ]


def check_gpu() -> list[CheckResult]:
    """Describe the graphics hardware and, for NVIDIA, its driver and memory."""
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    if out is not None and out[0] == 0 and out[1]:
        details = []
        for line in out[1].splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 4:
                name, driver, used, total = parts
                details.append(f"{name}, driver {driver}, {used} / {total} MiB in use")
        return [CheckResult("System", "Graphics", OK, "NVIDIA GPU available", details=details)]

    out = _run(["lspci"])
    gpus = []
    if out is not None and out[0] == 0:
        for line in out[1].splitlines():
            if "VGA compatible controller" in line or "3D controller" in line:
                gpus.append(line.split(": ", 1)[-1])
    if not gpus:
        return [CheckResult("System", "Graphics", INFO, "No graphics card details found")]
    summary = "AI models run on the CPU"
    if any("NVIDIA" in g for g in gpus):
        return [
            CheckResult(
                "System",
                "Graphics",
                WARN,
                "NVIDIA GPU found but its driver is not loaded",
                details=gpus,
                hint="Install the recommended driver with: sudo ubuntu-drivers install",
            )
        ]
    return [CheckResult("System", "Graphics", INFO, summary, details=gpus)]


def check_network(host: str = NETWORK_PROBE_HOST, timeout: float = 4) -> list[CheckResult]:
    """Check name resolution and an HTTPS connection to the update server."""
    try:
        addr = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)[0][4][0]
    except OSError:
        return [
            CheckResult(
                "Network",
                "Internet",
                FAIL,
                f"Cannot look up {host}",
                hint="Check that you are connected to Wi-Fi or a cable, then try again.",
            )
        ]
    start = time.monotonic()
    try:
        with socket.create_connection((host, 443), timeout=timeout):
            pass
    except OSError as exc:
        return [
            CheckResult(
                "Network",
                "Internet",
                FAIL,
                f"Cannot connect to {host}",
                details=[f"{addr}: {exc}"],
                hint="A firewall or proxy may be blocking HTTPS.",
            )
        ]
    ms = (time.monotonic() - start) * 1000
    return [CheckResult("Network", "Internet", OK, f"connected ({ms:.0f} ms to {host})")]


def check_updates() -> list[CheckResult]:
    """Check the installed Axon version, the update source and the update timer."""
    results = []
    out = _run(["dpkg-query", "-W", "-f=${Version}", "axon-os"])
    if out is not None and out[0] == 0 and out[1]:
        results.append(CheckResult("Updates", "Axon package", OK, f"axon-os {out[1]}"))
    else:
        results.append(
            CheckResult(
                "Updates",
                "Axon package",
                WARN,
                "The axon-os package is not installed",
                hint="Install it once from the latest release so updates can reach this system.",
            )
        )

    if AXON_APT_SOURCE.exists():
        results.append(CheckResult("Updates", "Update source", OK, "Axon apt repository set up"))
    else:
        results.append(
            CheckResult(
                "Updates",
                "Update source",
                WARN,
                "Axon apt repository is missing",
                hint="Reinstall the axon-os package to restore it.",
            )
        )

    out = _run(
        [
            "systemctl",
            "show",
            "-p",
            "UnitFileState",
            "-p",
            "ActiveState",
            "-p",
            "LastTriggerUSec",
            UPDATE_TIMER,
        ]
    )
    props = dict(line.partition("=")[::2] for line in out[1].splitlines()) if out else {}
    if props.get("UnitFileState") == "enabled":
        last = props.get("LastTriggerUSec") or "never"
        results.append(
            CheckResult("Updates", "Automatic updates", OK, "on", details=[f"Last run: {last}"])
        )
    elif props:
        results.append(
            CheckResult(
                "Updates",
                "Automatic updates",
                WARN,
                "off",
                hint=f"Turn them on with: sudo systemctl enable --now {UPDATE_TIMER}",
            )
        )

    out = _run(["systemctl", "show", "-p", "Result", "-p", "ExecMainStatus", UPDATE_SERVICE])
    if out is not None and out[0] == 0:
        props = dict(line.partition("=")[::2] for line in out[1].splitlines())
        if props.get("Result") not in (None, "", "success"):
            results.append(
                CheckResult(
                    "Updates",
                    "Last update",
                    FAIL,
                    f"failed ({props['Result']}, exit code {props.get('ExecMainStatus', '?')})",
                    hint=f"See what went wrong with: journalctl -u {UPDATE_SERVICE} -n 50",
                )
            )

    out = _run(["apt-get", "-s", "-q", "dist-upgrade"], timeout=30)
    if out is not None and out[0] == 0:
        pending = sum(1 for line in out[1].splitlines() if line.startswith("Inst "))
        results.append(
            CheckResult(
                "Updates",
                "Pending updates",
                INFO,
                f"{pending} package update(s) waiting" if pending else "up to date",
                details=["Based on the last package list refresh"],
            )
        )
    return results


def _failed_units(user: bool) -> list[str] | None:
    cmd = ["systemctl"] + (["--user"] if user else [])
    out = _run([*cmd, "list-units", "--failed", "--no-legend", "--plain", "--no-pager"])
    if out is None or out[0] != 0:
        return None
    return [line.split()[0] for line in out[1].splitlines() if line.strip()]


def check_failed_units() -> list[CheckResult]:
    """List systemd units that are in the failed state."""
    results = []
    for user, label, journal in (
        (False, "System services", "journalctl -u"),
        (True, "User services", "journalctl --user -u"),
    ):
        failed = _failed_units(user)
        if failed is None:
            continue
        if failed:
            results.append(
                CheckResult(
                    "Failed services",
                    label,
                    FAIL,
                    f"{len(failed)} failed",
                    details=failed,
                    hint=f"See why with: {journal} NAME -n 50",
                )
            )
        else:
            results.append(CheckResult("Failed services", label, OK, "none failed"))
    return results


CHECKS: dict[str, Callable[[], list[CheckResult]]] = {
    "services": check_axon_services,
    "ai": check_ollama,
    "disk": check_disk,
    "memory": check_memory,
    "gpu": check_gpu,
    "network": check_network,
    "updates": check_updates,
    "units": check_failed_units,
}


def system_summary() -> dict[str, str]:
    """Basic, non-identifying facts about the system."""
    info = {"Kernel": platform.release(), "Architecture": platform.machine()}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                info = {"OS": line.split("=", 1)[1].strip('"'), **info}
    except OSError:
        pass
    try:
        seconds = float(Path("/proc/uptime").read_text().split()[0])
        hours, rem = divmod(int(seconds), 3600)
        info["Uptime"] = f"{hours}h {rem // 60}m"
    except (OSError, ValueError, IndexError):
        pass
    return info


def run_diagnostics(only: list[str] | None = None) -> Report:
    """Run the selected checks (all by default) in parallel and build a report.

    Args:
        only: Keys of :data:`CHECKS` to run. None runs every check.

    Returns:
        The report, with results in the order of :data:`CHECKS`.
    """
    names = [n for n in CHECKS if only is None or n in only]

    def safe(name: str) -> list[CheckResult]:
        try:
            return CHECKS[name]()
        except Exception as exc:  # one broken check must not hide the others
            return [CheckResult("Diagnostics", name, WARN, f"check crashed: {exc}")]

    with ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
        batches = list(pool.map(safe, names))
    results = [r for batch in batches for r in batch]
    return Report(
        created=time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        system=system_summary(),
        results=results,
    )


def main(argv: list[str] | None = None) -> int:
    """Command line entry point for ``axon-diagnose``."""
    parser = argparse.ArgumentParser(
        prog="axon-diagnose",
        description="Check the health of Axon OS and print a report you can share.",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("-o", "--output", metavar="FILE", help="also save the report to FILE")
    parser.add_argument(
        "--only",
        metavar="CHECK",
        action="append",
        choices=list(CHECKS),
        help=f"run only this check (repeatable): {', '.join(CHECKS)}",
    )
    args = parser.parse_args(argv)

    report = run_diagnostics(args.only)
    text = report.to_json() + "\n" if args.json else report.to_text()
    if args.output:
        Path(args.output).write_text(text)
    if args.json:
        sys.stdout.write(text)
    else:
        sys.stdout.write(report.to_text(color=sys.stdout.isatty()))
    return 1 if report.status == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
