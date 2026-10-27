"""org.axonos.Gaming wiring, the axon-game-mode command, and the Search/Context hooks."""

import importlib.machinery
import importlib.util
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

dbus = pytest.importorskip("dbus", reason="Requires dbus-python (Linux only)")

import gaming_service
from game_mode import GameModeManager

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ("org.axonos.Brain", "/org/axonos/Brain")
SEARCH = ("org.axonos.Search", "/org/axonos/Search")
CONTEXT = ("org.axonos.Context", "/org/axonos/Context")


@pytest.fixture
def gaming(tmp_path):
    """A GamingService without a bus: calls to other services are recorded."""
    service = gaming_service.GamingService.__new__(gaming_service.GamingService)
    service.logger = MagicMock()
    service._settings = {
        "pause_ai": True,
        "use_cloud": False,
        "pause_indexing": True,
        "steam_games": True,
    }
    service._calls = []
    service._call = lambda name, path, method, *args: service._calls.append((name, method, *args))
    service.GameModeChanged = MagicMock()
    service.notify_property_changed = MagicMock()
    service._grace_source = 0
    service._manager = GameModeManager(service._on_mode_change, clock=lambda: 0.0)
    return service


@pytest.mark.unit
class TestApplyingGameMode:
    def test_starting_a_game_pauses_brain_search_and_context(self, gaming):
        gaming._manager.enter("steam_app_1", 0)
        assert gaming._calls == [
            ("org.axonos.Brain", "SetGameModeCloud", False),
            ("org.axonos.Brain", "Suspend", "game mode"),
            ("org.axonos.Search", "Pause"),
            ("org.axonos.Context", "Throttle", True),
        ]
        gaming.GameModeChanged.assert_called_once_with(True, "steam_app_1")

    def test_ending_resumes_everything(self, gaming):
        gaming._manager.enter("g", 0)
        gaming._calls.clear()
        gaming._manager.force_exit()
        assert gaming._calls == [
            ("org.axonos.Brain", "Resume"),
            ("org.axonos.Search", "Resume"),
            ("org.axonos.Context", "Throttle", False),
        ]
        assert gaming.GameModeChanged.call_args == call(False, "")

    def test_ai_stays_awake_when_the_user_turned_pausing_off(self, gaming):
        gaming._settings["pause_ai"] = False
        gaming._manager.enter("g", 0)
        assert ("org.axonos.Brain", "Suspend", "game mode") not in gaming._calls

    def test_indexing_keeps_running_when_the_user_turned_its_pause_off(self, gaming):
        gaming._settings["pause_indexing"] = False
        gaming._manager.enter("g", 0)
        assert ("org.axonos.Search", "Pause") not in gaming._calls
        assert ("org.axonos.Search", "Resume") in gaming._calls

    def test_cloud_setting_is_passed_to_brain(self, gaming):
        gaming._settings["use_cloud"] = True
        gaming._manager.enter("g", 0)
        assert ("org.axonos.Brain", "SetGameModeCloud", True) in gaming._calls

    def test_service_that_restarts_mid_game_is_put_back_to_sleep(self, gaming):
        gaming._manager.enter("g", 0)
        gaming._calls.clear()
        gaming._on_name_owner_changed("org.axonos.Brain", "", ":1.99")
        assert [c[:2] for c in gaming._calls] == [
            ("org.axonos.Brain", "SetGameModeCloud"),
            ("org.axonos.Brain", "Suspend"),
        ]

    def test_names_leaving_the_bus_or_strangers_are_ignored(self, gaming):
        gaming._manager.enter("g", 0)
        gaming._calls.clear()
        gaming._on_name_owner_changed("org.axonos.Brain", ":1.5", "")
        gaming._on_name_owner_changed("org.example.Other", "", ":1.6")
        assert gaming._calls == []

    def test_a_service_appearing_while_idle_is_left_alone(self, gaming):
        gaming._on_name_owner_changed("org.axonos.Brain", "", ":1.7")
        assert gaming._calls == []


@pytest.mark.unit
class TestDbusApi:
    def test_enter_returns_a_token_and_exit_releases_it(self, gaming):
        token = gaming.EnterGameMode("game", dbus.UInt32(42))
        assert gaming._manager.active
        gaming.ExitGameMode(token)
        assert gaming._manager.grace_pending
        assert gaming._manager.active  # grace period

    def test_properties(self, gaming):
        assert gaming.get_dbus_properties(gaming_service.IFACE)["Active"] == dbus.Boolean(False)
        gaming.EnterGameMode("game", dbus.UInt32(42))
        props = gaming.get_dbus_properties(gaming_service.IFACE)
        assert props["Active"] == dbus.Boolean(True)
        (token, app_id, pid) = props["ActiveGames"][0]
        assert (app_id, pid) == ("game", 42) and token
        assert gaming.get_dbus_properties("other") == {}

    def test_force_exit(self, gaming):
        gaming.EnterGameMode("a", dbus.UInt32(1))
        gaming.EnterGameMode("b", dbus.UInt32(2))
        gaming.ForceExit()
        assert not gaming._manager.active

    def test_set_setting_saves_and_applies_to_a_running_game(self, gaming, tmp_path, monkeypatch):
        monkeypatch.setattr(gaming_service, "SETTINGS_FILE", tmp_path / "gaming.json")
        gaming.EnterGameMode("g", dbus.UInt32(0))
        gaming._calls.clear()
        assert gaming.SetSetting("pause_ai", False)
        assert json.loads((tmp_path / "gaming.json").read_text())["pause_ai"] is False
        assert ("org.axonos.Brain", "Resume") in gaming._calls
        assert json.loads(gaming.GetSettings())["pause_ai"] is False

    def test_unknown_setting_is_rejected(self, gaming):
        assert gaming.SetSetting("bogus", True) is False


@pytest.mark.unit
def test_calls_never_start_a_service_that_is_not_running():
    service = gaming_service.GamingService.__new__(gaming_service.GamingService)
    service.session_bus = MagicMock()
    service.session_bus.name_has_owner.return_value = False
    service.logger = MagicMock()
    service._call(*BRAIN, "Suspend", "x")
    service.session_bus.get_object.assert_not_called()


# --------------------------------------------------------------------------
# axon-game-mode command
# --------------------------------------------------------------------------


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = ROOT / "packaging" / "axon-game-mode" / "files" / "usr" / "bin" / "axon-game-mode"
    loader = importlib.machinery.SourceFileLoader("axon_game_mode_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeGaming:
    def __init__(self):
        self.entered = []
        self.exited = []
        self.forced = 0
        self.counter = 0

    def enter(self, app_id, pid):
        self.counter += 1
        self.entered.append((app_id, pid))
        return f"token-{self.counter}"

    def exit(self, token):
        self.exited.append(token)

    def force_exit(self):
        self.forced += 1

    def state(self):
        return True, [("abcdef123456", "game", 7)]


@pytest.mark.unit
class TestCommand:
    def run(self, cli, fake, *argv):
        return cli.main(list(argv), gaming_factory=lambda: fake)

    def test_on_saves_the_token_and_off_releases_it(self, cli, tmp_path):
        fake = FakeGaming()
        assert self.run(cli, fake, "on", "--app-id", "steam_1", "--pid", "55") == 0
        assert fake.entered == [("steam_1", 55)]
        assert (tmp_path / "axon-game-mode" / "55").read_text() == "token-1"
        assert (tmp_path / "axon-game-mode").stat().st_mode & 0o777 == 0o700
        assert self.run(cli, fake, "off", "--pid", "55") == 0
        assert fake.exited == ["token-1"]
        assert not (tmp_path / "axon-game-mode" / "55").exists()

    def test_hooks_without_arguments_use_the_default_slot(self, cli, tmp_path):
        fake = FakeGaming()
        self.run(cli, fake, "on")
        assert fake.entered == [("gamemode", 0)]
        assert (tmp_path / "axon-game-mode" / "default").exists()
        self.run(cli, fake, "off")
        assert fake.exited == ["token-1"]

    def test_concurrent_games_keep_separate_tokens(self, cli, tmp_path):
        fake = FakeGaming()
        self.run(cli, fake, "on", "--pid", "1")
        self.run(cli, fake, "on", "--pid", "2")
        self.run(cli, fake, "off", "--pid", "1")
        assert fake.exited == ["token-1"]
        assert (tmp_path / "axon-game-mode" / "2").read_text() == "token-2"

    def test_starting_the_same_game_twice_does_not_leak_a_reference(self, cli):
        fake = FakeGaming()
        self.run(cli, fake, "on", "--pid", "9")
        self.run(cli, fake, "on", "--pid", "9")
        assert fake.exited == ["token-1"]

    def test_off_without_a_token_is_harmless(self, cli):
        fake = FakeGaming()
        assert self.run(cli, fake, "off") == 0
        assert fake.exited == []

    def test_reset_forces_exit_and_clears_tokens(self, cli, tmp_path):
        fake = FakeGaming()
        self.run(cli, fake, "on", "--pid", "1")
        assert self.run(cli, fake, "reset") == 0
        assert fake.forced == 1
        assert list((tmp_path / "axon-game-mode").iterdir()) == []

    def test_status_prints_state(self, cli, capsys):
        assert self.run(cli, FakeGaming(), "status") == 0
        out = capsys.readouterr().out
        assert "Active: yes" in out and "game" in out and "pid=7" in out

    def test_unreachable_service_is_an_error_not_a_crash(self, cli, capsys):
        def broken():
            raise RuntimeError("no bus")

        assert cli.main(["on"], gaming_factory=broken) == 1
        assert "no bus" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Search and Context hooks
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_search_pause_and_resume():
    from services.axon_search.search_service import SearchService

    service = SearchService.__new__(SearchService)
    service._lock = threading.Lock()
    service._stats = {"paused": False}
    service._running = threading.Event()
    service._running.set()
    service._rescan_event = threading.Event()

    service.Pause()
    assert not service._running.is_set() and service._stats["paused"]
    service.Resume()
    assert service._running.is_set() and not service._stats["paused"]
    assert service._rescan_event.is_set()  # catch up on changes made during the game


@pytest.mark.unit
def test_search_resume_when_never_paused_does_not_trigger_a_rescan():
    from services.axon_search.search_service import SearchService

    service = SearchService.__new__(SearchService)
    service._lock = threading.Lock()
    service._stats = {"paused": False}
    service._running = threading.Event()
    service._rescan_event = threading.Event()
    service.Resume()
    assert not service._rescan_event.is_set()


@pytest.mark.unit
def test_context_throttle_polls_every_fifth_tick(monkeypatch):
    from services.axon_context import context_service as cs

    service = cs.ContextService.__new__(cs.ContextService)
    service._throttled = False
    service._poll_ticks = 0
    service.track_clipboard = False
    service._load_config = MagicMock()
    ran = []
    service._load_config.side_effect = lambda: ran.append(1)

    for _ in range(5):
        assert service._poll_xclip() is True
    assert len(ran) == 5  # normal: every tick does the work

    ran.clear()
    service.Throttle(True)
    for _ in range(10):
        assert service._poll_xclip() is True
    assert len(ran) == 2  # throttled: every fifth tick

    service.Throttle(False)
    ran.clear()
    service._poll_xclip()
    assert len(ran) == 1


# --------------------------------------------------------------------------
# ServiceBase properties
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_service_base_properties():
    from service_base import ServiceBase

    class Demo(ServiceBase):
        def get_dbus_properties(self, interface):
            return {"Flag": dbus.Boolean(True)} if interface == "demo.Iface" else {}

    demo = Demo.__new__(Demo)
    assert demo.Get("demo.Iface", "Flag") == True  # noqa: E712 - a dbus.Boolean
    assert dict(demo.GetAll("demo.Iface")) == {"Flag": True}
    assert dict(demo.GetAll("other")) == {}
    with pytest.raises(dbus.exceptions.DBusException, match="No property"):
        demo.Get("demo.Iface", "Nope")
    with pytest.raises(dbus.exceptions.DBusException, match="read-only"):
        demo.Set("demo.Iface", "Flag", dbus.Boolean(False))


@pytest.mark.unit
class TestSteamGames:
    """The service follows Steam's own games when the setting is on (the default)."""

    @pytest.fixture
    def steam(self, gaming):
        import steam_games

        procs = []
        gaming._steam = steam_games.SteamWatcher(
            gaming._steam_enter, gaming._steam_leave, scan=lambda: list(procs)
        )
        gaming._procs = procs
        return gaming

    def game(self, steam, app_id="620", pid=100):
        steam._procs[:] = [(pid, ["/s/reaper", "SteamLaunch", f"AppId={app_id}", "--", "/g"])]

    def test_a_steam_game_turns_game_mode_on_and_off(self, steam):
        self.game(steam)
        steam._on_steam_poll()
        assert steam._manager.active
        assert ("org.axonos.Brain", "Suspend", "game mode") in steam._calls
        steam._procs.clear()
        steam._on_steam_poll()
        assert steam._manager.grace_pending, "ends after the usual grace period"

    def test_turning_the_setting_off_lets_go_of_the_game(self, steam):
        self.game(steam)
        steam._on_steam_poll()
        steam._settings["steam_games"] = False
        steam._on_steam_poll()
        assert steam._steam.tracked == [] and steam._manager.grace_pending

    def test_nothing_is_looked_at_while_the_setting_is_off(self, steam):
        steam._settings["steam_games"] = False
        self.game(steam)
        steam._on_steam_poll()
        assert not steam._manager.active

    def test_a_game_that_also_uses_gamemode_counts_once_it_is_over_twice(self, steam):
        steam._manager.enter("A game", 0)  # GameMode's hook
        self.game(steam)
        steam._on_steam_poll()
        steam._procs.clear()
        steam._on_steam_poll()
        assert steam._manager.active, "GameMode's game is still running"
