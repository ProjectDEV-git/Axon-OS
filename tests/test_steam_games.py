"""Recognizing running Steam games from the process list."""

import pytest
import steam_games as sg

REAPER = "/home/u/.local/share/Steam/ubuntu12_32/reaper"


def reaper(app_id, pid=100, game="/games/foo"):
    return (pid, [REAPER, "SteamLaunch", f"AppId={app_id}", "--", game, "-fullscreen"])


@pytest.mark.unit
class TestRecognition:
    def test_steams_reaper_is_a_game(self):
        assert sg.steam_app_id(reaper(620)[1]) == "620"

    def test_proton_games_run_under_the_same_helper(self):
        argv = [
            REAPER,
            "SteamLaunch",
            "AppId=1245620",
            "--",
            "/p/proton",
            "waitforexitandrun",
            "/g/x.exe",
        ]
        assert sg.steam_app_id(argv) == "1245620"

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["/usr/bin/steam"],
            [REAPER, "SteamLaunch"],  # no id
            [REAPER, "SteamLaunch", "AppId=0", "--", "x"],  # not a game
            ["/games/foo", "SteamLaunch", "AppId=620"],  # a game that only mentions it
            [REAPER, "--", "/games/foo", "SteamLaunch", "AppId=620"],  # after the game's own "--"
            ["/usr/bin/python3", "reaper", "SteamLaunch", "AppId=620"],
        ],
    )
    def test_everything_else_is_not(self, argv):
        assert sg.steam_app_id(argv) is None

    def test_a_relaunch_keeps_the_oldest_helper(self):
        assert sg.running_games([reaper(620, 300), reaper(620, 200), reaper(70, 50)]) == {
            "620": 200,
            "70": 50,
        }


@pytest.mark.unit
def test_scan_reads_command_lines_from_proc(tmp_path):
    def proc(pid, argv):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")

    proc(100, [REAPER, "SteamLaunch", "AppId=620", "--", "/g"])
    proc(101, ["/usr/bin/bash"])  # not interesting: skipped before it is split
    (tmp_path / "self").mkdir()  # not a pid
    (tmp_path / "102").mkdir()  # no cmdline (the process just ended)
    found = sg.scan_proc(tmp_path)
    assert [pid for pid, _ in found] == [100]
    assert sg.running_games(found) == {"620": 100}
    assert sg.scan_proc(tmp_path / "missing") == []


@pytest.mark.unit
class TestWatcher:
    def make(self):
        self.procs = []
        self.entered, self.left = [], []

        def enter(app_id, pid):
            self.entered.append((app_id, pid))
            return f"t{len(self.entered)}"

        return sg.SteamWatcher(enter, self.left.append, scan=lambda: list(self.procs))

    def test_start_and_end_of_a_game(self):
        w = self.make()
        assert w.poll() == ([], [])
        self.procs = [reaper(620, 100)]
        assert w.poll() == (["620"], [])
        assert self.entered == [("steam:620", 100)]
        assert w.poll() == ([], []), "still running: nothing new"
        self.procs = []
        assert w.poll() == ([], ["620"])
        assert self.left == ["t1"]
        assert w.tracked == []

    def test_two_games_are_tracked_separately(self):
        w = self.make()
        self.procs = [reaper(620, 100), reaper(70, 200)]
        w.poll()
        self.procs = [reaper(70, 200)]
        assert w.poll() == ([], ["620"])
        assert w.tracked == ["70"]

    def test_stop_lets_go_of_everything(self):
        w = self.make()
        self.procs = [reaper(620, 100), reaper(70, 200)]
        w.poll()
        assert w.stop() == ["620", "70"] and self.left == ["t1", "t2"]
        assert w.tracked == []
