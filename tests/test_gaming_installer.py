"""Installing and removing catalog items: commands, queueing, progress and errors."""

import threading
from pathlib import Path

import catalog_installer as installer
import pytest
from catalog import load_catalog
from catalog_installer import DONE, FAILED, QUEUED, RUNNING, Event, JobQueue

CATALOG = Path(__file__).resolve().parent.parent / "data" / "gaming" / "catalog.json"


@pytest.fixture(autouse=True)
def _pkcon(monkeypatch):
    """The commands the tests expect are the pkcon ones, whatever the host has installed."""
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: "/usr/bin/pkcon" if name == "pkcon" else None
    )


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG)


class FakeRunner:
    """Records commands; ``results`` maps a command prefix to (exit code, output lines)."""

    def __init__(self, results=None):
        self.calls: list[list[str]] = []
        self.results = results or {}
        self.gate: threading.Event | None = None

    def __call__(self, argv, on_line):
        self.calls.append(argv)
        if self.gate is not None:
            self.gate.wait(5)
        for prefix, (code, lines) in self.results.items():
            if argv[: len(prefix)] == list(prefix):
                for line in lines:
                    on_line(line)
                return code
        return 0


def wait_for(events, item_id, state, timeout=5):
    deadline = threading.Event()
    for _ in range(int(timeout * 100)):
        if any(e.item_id == item_id and e.state == state for e in list(events)):
            return
        deadline.wait(0.01)
    raise AssertionError(f"{item_id} never reached {state}: {events}")


@pytest.mark.unit
class TestCommands:
    def test_flatpak_installs_per_user_without_a_password(self, catalog):
        steps = installer.install_steps(catalog.items["heroic"])
        assert steps[0][:5] == ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub"]
        assert steps[1] == [
            "flatpak", "install", "--user", "--noninteractive", "-y",
            "flathub", "com.heroicgameslauncher.hgl",
        ]  # fmt: skip
        assert not any(a == "pkexec" or a == "sudo" for step in steps for a in step)

    def test_apt_goes_through_packagekit(self, catalog):
        steps = installer.install_steps(catalog.items["media-codecs"])
        assert steps == [
            [
                "pkcon", "-y", "install", "gstreamer1.0-plugins-bad",
                "gstreamer1.0-plugins-ugly", "gstreamer1.0-libav", "ffmpeg",
            ]
        ]  # fmt: skip

    def test_required_apt_packages_come_first(self, catalog):
        steps = installer.install_steps(catalog.items["piper"])
        assert steps[0] == ["pkcon", "-y", "install", "ratbagd"]
        assert steps[-1][:3] == ["flatpak", "install", "--user"]

    def test_remove_and_optional_runtime_cleanup(self, catalog):
        item = catalog.items["heroic"]
        assert installer.remove_steps(item) == [
            [
                "flatpak",
                "uninstall",
                "--user",
                "--noninteractive",
                "-y",
                "com.heroicgameslauncher.hgl",
            ]
        ]
        with_unused = installer.remove_steps(item, remove_unused=True)
        assert with_unused[1] == [
            "flatpak",
            "uninstall",
            "--user",
            "--unused",
            "--noninteractive",
            "-y",
        ]
        assert installer.remove_steps(catalog.items["input-remapper"]) == [
            ["pkcon", "-y", "remove", "input-remapper"]
        ]

    def test_commands_are_argument_lists(self, catalog):
        for item in catalog.items.values():
            for step in installer.install_steps(item) + installer.remove_steps(item):
                assert isinstance(step, list) and all(isinstance(a, str) for a in step)
                assert not any(ch in " ".join(step) for ch in ";&|`$")

    def test_other_components_are_not_installed_from_here(self, catalog):
        item = catalog.items["ai-models"]
        assert not installer.is_supported(item)
        assert installer.install_steps(item) == []

    def test_ollama_gpu_libraries_install_through_the_polkit_helper(self, catalog):
        for item_id, backend in (("ollama-cuda", "cuda"), ("ollama-rocm", "rocm")):
            item = catalog.items[item_id]
            assert installer.is_supported(item)
            helper = installer.GPU_HELPER
            assert installer.install_steps(item) == [["pkexec", helper, backend]]
            assert installer.remove_steps(item) == [["pkexec", helper, "remove", backend]]
            assert installer.update_steps(item) == [["pkexec", helper, backend]]


@pytest.mark.unit
class TestInstalledState:
    def test_flatpak_and_apt_items_are_detected(self, catalog):
        def runner(argv, on_line):
            if argv[:2] == ["flatpak", "list"]:
                on_line("com.heroicgameslauncher.hgl")
                return 0
            if argv[0] == "dpkg-query":
                on_line("ii " if argv[-1] == "input-remapper" else "un ")
                return 0 if argv[-1] == "input-remapper" else 1
            return 1

        items = [catalog.items[i] for i in ("heroic", "lutris", "input-remapper", "media-codecs")]
        assert installer.installed_ids(items, runner) == {"heroic", "input-remapper"}

    def test_flatpak_missing_means_nothing_installed(self, catalog):
        runner = lambda argv, on_line: (on_line("flatpak: not found"), 127)[1]  # noqa: E731
        assert installer.installed_ids([catalog.items["heroic"]], runner) == set()

    def test_installed_gpu_libraries_are_detected_from_the_helper(self, catalog):
        def runner(argv, on_line):
            if argv[-1] == "status":
                on_line("cuda v0.35.1")
            return 0

        items = [catalog.items["ollama-cuda"], catalog.items["ollama-rocm"]]
        assert installer.installed_ids(items, runner) == {"ollama-cuda"}

    def test_runtime_presence(self):
        assert installer.flatpak_runtime_present(
            lambda a, cb: (cb("org.freedesktop.Platform"), 0)[1]
        )
        assert not installer.flatpak_runtime_present(lambda a, cb: 0)


@pytest.mark.unit
class TestProgressAndErrors:
    @pytest.mark.parametrize(
        ("line", "fraction"),
        [
            ("Installing… ██████░░░░ 45%", 0.45),
            ("Percentage: 80", 0.8),
            ("Downloading 100%", 1.0),
            ("Installing runtime", None),
            ("Percentage: 250", None),
        ],
    )
    def test_parse_progress(self, line, fraction):
        assert installer.parse_progress(line) == fraction

    def test_offline_beats_every_other_explanation(self):
        assert installer.friendly_error(["whatever"], online=False) == installer.OFFLINE_MESSAGE

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            (["Error: Not authorized"], "Permission was not granted."),
            (
                ["error: No remote refs found for 'x'"],
                "This app was not found in its source. The catalog may be out of date.",
            ),
            (["write failed: No space left on device"], "Not enough disk space."),
            (["Unable to connect to host"], installer.OFFLINE_MESSAGE),
            (["something odd"], "something odd"),
            ([], "The installation failed."),
        ],
    )
    def test_friendly_error(self, output, expected):
        assert installer.friendly_error(output, online=True) == expected


@pytest.mark.unit
class TestJobQueue:
    def make(self, runner, online=True):
        events: list[Event] = []
        queue = JobQueue(events.append, runner=runner, online=lambda: online)
        return queue, events

    def test_install_runs_every_step_and_reports_done(self, catalog):
        runner = FakeRunner()
        queue, events = self.make(runner)
        assert queue.install(catalog.items["heroic"])
        wait_for(events, "heroic", DONE)
        assert [c[1] for c in runner.calls] == ["remote-add", "install"]
        assert [e.state for e in events][:2] == [QUEUED, RUNNING]
        queue.close()

    def test_progress_is_spread_over_the_steps(self, catalog):
        runner = FakeRunner({("flatpak", "install"): (0, ["Installing… 50%"])})
        queue, events = self.make(runner)
        queue.install(catalog.items["heroic"])
        wait_for(events, "heroic", DONE)
        progress = [e.fraction for e in events if e.state == RUNNING and e.message]
        assert progress == [0.75]  # step 2 of 2, half done
        queue.close()

    def test_offline_fails_before_running_anything(self, catalog):
        runner = FakeRunner()
        queue, events = self.make(runner, online=False)
        queue.install(catalog.items["heroic"])
        wait_for(events, "heroic", FAILED)
        assert events[-1].message == installer.OFFLINE_MESSAGE
        assert runner.calls == []
        queue.close()

    def test_a_failing_step_stops_the_job_with_a_clear_message(self, catalog):
        runner = FakeRunner({("pkcon",): (1, ["Error: Not authorized"])})
        queue, events = self.make(runner)
        queue.install(catalog.items["piper"])
        wait_for(events, "piper", FAILED)
        assert events[-1].message == "Permission was not granted."
        assert all(c[0] == "pkcon" for c in runner.calls)  # the Flatpak step never ran
        queue.close()

    def test_removing_works_offline(self, catalog):
        runner = FakeRunner()
        queue, events = self.make(runner, online=False)
        queue.remove(catalog.items["heroic"])
        wait_for(events, "heroic", DONE)
        queue.close()

    def test_same_backend_runs_one_job_at_a_time_in_order(self, catalog):
        runner = FakeRunner()
        runner.gate = threading.Event()
        queue, events = self.make(runner)
        queue.install(catalog.items["heroic"])
        queue.install(catalog.items["lutris"])
        wait_for(events, "heroic", RUNNING)
        assert not any(e.item_id == "lutris" and e.state == RUNNING for e in events)
        runner.gate.set()
        wait_for(events, "lutris", DONE)
        starts = [e.item_id for e in events if e.state == RUNNING and e.fraction == 0.0]
        assert starts == ["heroic", "lutris"]
        queue.close()

    def test_different_backends_do_not_wait_for_each_other(self, catalog):
        runner = FakeRunner()
        runner.gate = threading.Event()
        queue, events = self.make(runner)
        queue.install(catalog.items["heroic"])  # flatpak, blocked on the gate
        queue.install(catalog.items["input-remapper"])  # apt, own worker
        wait_for(events, "input-remapper", RUNNING)
        runner.gate.set()
        wait_for(events, "heroic", DONE)
        queue.close()

    def test_the_same_job_is_not_queued_twice(self, catalog):
        runner = FakeRunner()
        runner.gate = threading.Event()
        queue, events = self.make(runner)
        assert queue.install(catalog.items["heroic"])
        assert not queue.install(catalog.items["heroic"])
        runner.gate.set()
        wait_for(events, "heroic", DONE)
        queue.close()

    def test_components_owned_by_other_services_report_a_clear_failure(self, catalog):
        queue, events = self.make(FakeRunner())
        queue.install(catalog.items["ai-models"])
        wait_for(events, "ai-models", FAILED)
        queue.close()


@pytest.mark.unit
class TestUpdates:
    def test_update_commands(self, catalog):
        assert installer.update_steps(catalog.items["heroic"]) == [
            ["flatpak", "update", "--user", "--noninteractive", "-y", "com.heroicgameslauncher.hgl"]
        ]
        assert installer.update_steps(catalog.items["input-remapper"]) == [
            ["pkcon", "-y", "update", "input-remapper"]
        ]
        assert installer.update_steps(catalog.items["ai-models"]) == []

    def test_update_needs_the_network(self, catalog):
        events = []
        queue = JobQueue(events.append, runner=FakeRunner(), online=lambda: False)
        queue.update(catalog.items["heroic"])
        wait_for(events, "heroic", FAILED)
        queue.close()

    def test_update_job_runs(self, catalog):
        runner = FakeRunner()
        events = []
        queue = JobQueue(events.append, runner=runner, online=lambda: True)
        queue.update(catalog.items["heroic"])
        wait_for(events, "heroic", DONE)
        assert runner.calls == [installer.update_steps(catalog.items["heroic"])[0]]
        queue.close()


@pytest.mark.parametrize(
    ("have", "expected"),
    [
        ({"pkcon"}, ["pkcon", "-y", "install", "ratbagd"]),
        # Ubuntu 26.04 dropped pkcon; pkgcli takes --yes before the command
        ({"pkgcli"}, ["pkgcli", "--yes", "install", "ratbagd"]),
        (set(), ["pkcon", "-y", "install", "ratbagd"]),
        ({"pkcon", "pkgcli"}, ["pkcon", "-y", "install", "ratbagd"]),
    ],
)
def test_packagekit_client_follows_what_is_installed(monkeypatch, have, expected):
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: f"/usr/bin/{name}" if name in have else None
    )
    assert installer._pk("install", "ratbagd") == expected
