"""Game mode rules: reference counting, the grace period and crash recovery."""

import os
import subprocess
import sys

import pytest
from game_mode import GameModeManager, pid_alive


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def events():
    return []


@pytest.fixture
def alive():
    """Pids that count as running; tests remove one to simulate a crash."""
    return set()


@pytest.fixture
def manager(clock, events, alive):
    return GameModeManager(
        on_change=lambda active, app_id: events.append((active, app_id)),
        clock=clock,
        alive=lambda pid: pid in alive,
        grace=10.0,
    )


@pytest.mark.unit
class TestReferenceCounting:
    def test_first_game_turns_game_mode_on(self, manager, events):
        manager.enter("steam_app_1", 100)
        assert manager.active
        assert events == [(True, "steam_app_1")]

    def test_second_game_only_adds_a_reference(self, manager, events):
        manager.enter("a", 100)
        manager.enter("b", 200)
        assert events == [(True, "a")]
        assert len(manager.games) == 2

    def test_stays_on_until_both_games_close(self, manager, clock, events):
        first = manager.enter("a", 100)
        second = manager.enter("b", 200)
        manager.exit(first)
        clock.advance(60)
        manager.tick()
        assert manager.active
        manager.exit(second)
        clock.advance(11)
        manager.tick()
        assert not manager.active
        assert events == [(True, "a"), (False, "")]

    def test_unknown_token_is_ignored(self, manager):
        manager.enter("a", 100)
        assert manager.exit("nope") is False
        assert manager.active

    def test_releasing_twice_does_not_drop_another_game(self, manager, clock):
        first = manager.enter("a", 100)
        manager.enter("b", 200)
        manager.exit(first)
        manager.exit(first)
        clock.advance(60)
        manager.tick()
        assert manager.active


@pytest.mark.unit
class TestGracePeriod:
    def test_mode_stays_on_during_the_grace_period(self, manager, clock):
        manager.exit(manager.enter("a", 100))
        clock.advance(9.9)
        manager.tick()
        assert manager.active
        assert manager.grace_pending

    def test_mode_ends_after_the_grace_period(self, manager, clock, events):
        manager.exit(manager.enter("a", 100))
        clock.advance(10.0)
        manager.tick()
        assert not manager.active
        assert events[-1] == (False, "")
        assert not manager.grace_pending

    def test_relaunch_inside_grace_cancels_the_shutdown(self, manager, clock, events):
        manager.exit(manager.enter("launcher", 100))
        clock.advance(5)
        manager.enter("game", 101)
        clock.advance(60)
        manager.tick()
        assert manager.active
        assert events == [(True, "launcher")]  # no off/on flip

    def test_grace_restarts_after_a_relaunch(self, manager, clock):
        manager.exit(manager.enter("a", 1))
        clock.advance(5)
        token = manager.enter("a", 2)
        manager.exit(token)
        clock.advance(9)
        manager.tick()
        assert manager.active  # 10 s counted from the second exit, not the first
        clock.advance(1)
        manager.tick()
        assert not manager.active


@pytest.mark.unit
class TestCrashSafety:
    def test_dead_process_is_released_by_tick(self, manager, clock, alive):
        alive.add(100)
        manager.enter("game", 100)
        alive.discard(100)  # kill -9
        manager.tick()
        assert manager.active and manager.grace_pending
        clock.advance(10)
        manager.tick()
        assert not manager.active

    def test_kill_is_noticed_within_poll_plus_grace(self, manager, clock, alive):
        """15 s poll + 10 s grace: game mode is off by 25 s after the kill."""
        alive.add(100)
        manager.enter("game", 100)
        alive.discard(100)
        clock.advance(15)  # next poll finds the process gone
        manager.tick()
        clock.advance(10)  # the grace timer fires
        manager.tick()
        assert not manager.active

    def test_living_process_keeps_game_mode_on(self, manager, clock, alive):
        alive.add(100)
        manager.enter("game", 100)
        for _ in range(10):
            clock.advance(15)
            manager.tick()
        assert manager.active

    def test_other_games_survive_one_crash(self, manager, clock, alive):
        alive.update({1, 2})
        manager.enter("a", 1)
        manager.enter("b", 2)
        alive.discard(1)
        manager.tick()
        clock.advance(60)
        manager.tick()
        assert manager.active
        assert [g.app_id for g in manager.games] == ["b"]

    def test_force_exit_turns_off_immediately(self, manager, events, alive):
        alive.update({1, 2})
        manager.enter("a", 1)
        manager.enter("b", 2)
        manager.force_exit()
        assert not manager.active
        assert manager.games == []
        assert events[-1] == (False, "")

    def test_force_exit_when_idle_is_silent(self, manager, events):
        manager.force_exit()
        assert events == []

    def test_unknown_pid_is_never_reaped(self, clock, events):
        manager = GameModeManager(lambda *a: events.append(a), clock, alive=pid_alive)
        manager.enter("gamemode", 0)
        clock.advance(600)
        manager.tick()
        assert manager.active


@pytest.mark.unit
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
class TestPidAlive:
    def test_own_process_is_alive(self):
        assert pid_alive(os.getpid())

    def test_pid_zero_means_unknown_and_counts_as_alive(self):
        assert pid_alive(0)

    def test_exited_and_reaped_process_is_gone(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        assert not pid_alive(proc.pid)

    def test_zombie_counts_as_gone(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        try:
            deadline = 50
            while deadline and not _is_zombie(proc.pid):
                deadline -= 1
                subprocess.run(["sleep", "0.05"], check=False)
            assert _is_zombie(proc.pid)
            assert not pid_alive(proc.pid)
        finally:
            proc.wait()


def _is_zombie(pid):
    from game_mode import _is_zombie as check

    return check(pid)
