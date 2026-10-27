"""Install and remove catalog items (the catalog installer).

Flatpak apps install per user (no password). Apt packages go through PackageKit
(``pkcon``), which asks for authorisation with polkit. Items are queued per
backend: one Flatpak and one apt job run at a time, so two Flatpak installs never
fight over the same lock.

Every command is an argument list built from catalog data; nothing runs in a shell.
"""

from __future__ import annotations

import queue
import re
import socket
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from catalog import Item

FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
OFFLINE_MESSAGE = "Connect to the internet to install apps."
RUNTIME_NOTE = "This also downloads the shared runtime, used by many apps."

# Job states
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


@dataclass(frozen=True)
class Event:
    """Progress of one job, for the UI."""

    item_id: str
    action: str  # install, update or remove
    state: str
    message: str = ""
    fraction: float | None = None


class Runner(Protocol):
    def __call__(self, argv: list[str], on_line: Callable[[str], None]) -> int: ...


def run_command(argv: list[str], on_line: Callable[[str], None]) -> int:
    """Run ``argv``, feed its output to ``on_line``, return the exit code."""
    try:
        proc = subprocess.Popen(  # nosec B603 - argument list, never a shell
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        on_line(f"{argv[0]}: {exc}")
        return 127
    assert proc.stdout is not None
    # Both tools redraw progress with carriage returns
    for chunk in proc.stdout:
        for line in re.split(r"[\r\n]+", chunk):
            if line.strip():
                on_line(line.strip())
    return proc.wait()


def is_online(timeout: float = 3.0) -> bool:
    """True if Flathub is reachable. Blocking; call from a worker thread."""
    try:
        with socket.create_connection(("dl.flathub.org", 443), timeout=timeout):
            return True
    except OSError:
        return False


# -- Commands ---------------------------------------------------------------


def install_steps(item: Item) -> list[list[str]]:
    """The commands that install ``item``, in order."""
    steps: list[list[str]] = []
    needed = list(item.requires.get("apt", []))
    if needed:
        steps.append(["pkcon", "-y", "install", *needed])
    if item.kind == "flatpak":
        steps.append(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_URL])
        steps.append(
            [
                "flatpak",
                "install",
                "--user",
                "--noninteractive",
                "-y",
                item.source["remote"],
                item.source["ref"],
            ]
        )
    elif item.kind in ("apt", "axon-apt"):
        steps.append(["pkcon", "-y", "install", *item.source["packages"]])
    return steps


def remove_steps(item: Item, remove_unused: bool = False) -> list[list[str]]:
    """The commands that remove ``item`` (and, if asked, Flatpak runtimes nothing uses)."""
    if item.kind == "flatpak":
        steps = [["flatpak", "uninstall", "--user", "--noninteractive", "-y", item.source["ref"]]]
        if remove_unused:
            steps.append(["flatpak", "uninstall", "--user", "--unused", "--noninteractive", "-y"])
        return steps
    if item.kind in ("apt", "axon-apt"):
        return [["pkcon", "-y", "remove", *item.source["packages"]]]
    return []


def update_steps(item: Item) -> list[list[str]]:
    """The commands that update ``item`` to its newest version."""
    if item.kind == "flatpak":
        return [["flatpak", "update", "--user", "--noninteractive", "-y", item.source["ref"]]]
    if item.kind in ("apt", "axon-apt"):
        return [["pkcon", "-y", "update", *item.source["packages"]]]
    return []


def is_supported(item: Item) -> bool:
    """Components are installed by other Axon services, not from here (yet)."""
    return item.kind != "component"


def installed_ids(items: list[Item], runner: Runner = run_command) -> set[str]:
    """Which of ``items`` are installed. Cheap enough to call when a page opens."""
    flatpaks: set[str] = set()
    lines: list[str] = []
    runner(["flatpak", "list", "--user", "--app", "--columns=application"], lines.append)
    runner(["flatpak", "list", "--user", "--runtime", "--columns=application"], lines.append)
    flatpaks.update(lines)
    done: set[str] = set()
    for item in items:
        if item.kind == "flatpak" and item.source["ref"] in flatpaks:
            done.add(item.id)
        elif item.kind in ("apt", "axon-apt") and all(
            _dpkg_installed(p, runner) for p in item.source["packages"]
        ):
            done.add(item.id)
    return done


def flatpak_runtime_present(runner: Runner = run_command) -> bool:
    """True once any user Flatpak runtime is installed (so the next app is small)."""
    lines: list[str] = []
    runner(["flatpak", "list", "--user", "--runtime", "--columns=application"], lines.append)
    return any(ln.startswith("org.") for ln in lines)


def _dpkg_installed(package: str, runner: Runner) -> bool:
    out: list[str] = []
    code = runner(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package], out.append)
    return code == 0 and any(ln.startswith("ii") for ln in out)


# -- Progress and errors ----------------------------------------------------

_PERCENT = re.compile(r"(\d{1,3})\s*%")
_PK_PERCENT = re.compile(r"^Percentage:\s*(\d{1,3})")


def parse_progress(line: str) -> float | None:
    """0..1 from a flatpak ("Installing… 45%") or pkcon ("Percentage: 45") line."""
    match = _PK_PERCENT.match(line) or _PERCENT.search(line)
    if not match:
        return None
    value = int(match.group(1))
    return value / 100 if 0 <= value <= 100 else None


def friendly_error(output: list[str], online: bool) -> str:
    """A short explanation for a failed job, from its output."""
    if not online:
        return OFFLINE_MESSAGE
    text = "\n".join(output[-8:]).lower()
    if "not authorized" in text or "permission denied" in text or "authentication" in text:
        return "Permission was not granted."
    if "no remote refs found" in text or "not found" in text or "no packages were found" in text:
        return "This app was not found in its source. The catalog may be out of date."
    if "no space left" in text:
        return "Not enough disk space."
    if "could not resolve" in text or "unable to connect" in text or "network" in text:
        return OFFLINE_MESSAGE
    last = output[-1] if output else ""
    return last[:200] or "The installation failed."


# -- Queue ------------------------------------------------------------------


@dataclass
class _Job:
    item: Item
    action: str
    remove_unused: bool = False


class JobQueue:
    """Runs install and remove jobs in the background, one at a time per backend."""

    def __init__(
        self,
        on_event: Callable[[Event], None],
        runner: Runner = run_command,
        online: Callable[[], bool] = is_online,
    ) -> None:
        self._on_event = on_event
        self._runner = runner
        self._online = online
        self._queues: dict[str, queue.Queue[_Job | None]] = {}
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._pending: set[tuple[str, str]] = set()

    def install(self, item: Item) -> bool:
        return self._submit(_Job(item, "install"))

    def remove(self, item: Item, remove_unused: bool = False) -> bool:
        return self._submit(_Job(item, "remove", remove_unused))

    def update(self, item: Item) -> bool:
        return self._submit(_Job(item, "update"))

    def _submit(self, job: _Job) -> bool:
        """Queue a job. False if the same job is already waiting or running."""
        key = (job.item.id, job.action)
        with self._lock:
            if key in self._pending:
                return False
            self._pending.add(key)
            q = self._queues.get(job.item.backend)
            if q is None:
                q = self._queues[job.item.backend] = queue.Queue()
                thread = threading.Thread(target=self._work, args=(q,), daemon=True)
                self._threads.append(thread)
                thread.start()
        self._on_event(Event(job.item.id, job.action, QUEUED))
        q.put(job)
        return True

    def close(self) -> None:
        """Stop the workers after their current job."""
        with self._lock:
            for q in self._queues.values():
                q.put(None)

    def _work(self, q: queue.Queue[_Job | None]) -> None:
        while (job := q.get()) is not None:
            try:
                self._run(job)
            finally:
                with self._lock:
                    self._pending.discard((job.item.id, job.action))

    def _run(self, job: _Job) -> None:
        item, action = job.item, job.action
        emit = self._on_event
        if not is_supported(item):
            emit(Event(item.id, action, FAILED, "This is managed by another part of Axon OS."))
            return
        if action in ("install", "update") and not self._online():
            emit(Event(item.id, action, FAILED, OFFLINE_MESSAGE))
            return
        if action == "install":
            steps = install_steps(item)
        elif action == "update":
            steps = update_steps(item)
        else:
            steps = remove_steps(item, job.remove_unused)
        output: list[str] = []
        emit(Event(item.id, action, RUNNING, fraction=0.0))
        for index, argv in enumerate(steps):

            def on_line(line: str, index: int = index) -> None:
                output.append(line)
                fraction = parse_progress(line)
                if fraction is not None:
                    overall = (index + fraction) / len(steps)
                    emit(Event(item.id, action, RUNNING, line, overall))

            if self._runner(argv, on_line) != 0:
                emit(Event(item.id, action, FAILED, friendly_error(output, self._online())))
                return
        emit(Event(item.id, action, DONE, fraction=1.0))
