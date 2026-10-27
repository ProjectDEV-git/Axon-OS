"""Reference-counted game mode state, with no D-Bus or GLib in it.

``GameModeManager`` keeps the list of running games ("tokens") and decides when
game mode turns on and off. It never sleeps or starts threads: the service calls
``tick()`` from a timer and the manager reads time through an injectable clock,
so the rules are testable without waiting.

Rules:
  * The first token turns game mode on; more tokens only add a reference.
  * Releasing the last token starts a grace period (``grace`` seconds). A new
    token inside it cancels the shutdown, so a launcher restarting its game
    does not make the AI services thrash.
  * ``tick()`` drops tokens whose process has exited (crash safety) and ends
    game mode once the grace period is over.
  * ``force_exit()`` clears everything and turns game mode off immediately.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_GRACE_SECONDS = 10.0
DEFAULT_POLL_SECONDS = 15.0


def pid_alive(pid: int) -> bool:
    """True if a process with this pid exists (pid 0 means "unknown": always alive)."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """A zombie has exited but not been reaped; it must not keep game mode on."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            stat = f.read()
    except OSError:
        return False  # no /proc (or already gone): trust os.kill
    # Format: "pid (comm) S ..." and comm may contain spaces and parentheses.
    return stat.rpartition(")")[2].split()[:1] == ["Z"]


@dataclass(frozen=True)
class Game:
    token: str
    app_id: str
    pid: int


class GameModeManager:
    """Tracks running games and reports on/off transitions."""

    def __init__(
        self,
        on_change: Callable[[bool, str], None],
        clock: Callable[[], float],
        alive: Callable[[int], bool] = pid_alive,
        grace: float = DEFAULT_GRACE_SECONDS,
    ) -> None:
        self._on_change = on_change
        self._clock = clock
        self._alive = alive
        self._grace = grace
        self._games: dict[str, Game] = {}
        self._active = False
        self._deactivate_at: float | None = None

    @property
    def active(self) -> bool:
        """True from the first game starting until the grace period after the last ends."""
        return self._active

    @property
    def games(self) -> list[Game]:
        return list(self._games.values())

    @property
    def grace_pending(self) -> bool:
        return self._deactivate_at is not None

    def enter(self, app_id: str, pid: int) -> str:
        """Register a running game and return its token."""
        token = uuid.uuid4().hex
        self._games[token] = Game(token, app_id, pid)
        self._deactivate_at = None  # a restart inside the grace period keeps game mode on
        if not self._active:
            self._active = True
            self._on_change(True, app_id)
        return token

    def exit(self, token: str) -> bool:
        """Release one game. Returns False for an unknown token (already released)."""
        if self._games.pop(token, None) is None:
            return False
        if not self._games and self._active and self._deactivate_at is None:
            self._deactivate_at = self._clock() + self._grace
        return True

    def force_exit(self) -> None:
        """Forget every game and turn game mode off now."""
        self._games.clear()
        self._deactivate_at = None
        self._deactivate()

    def tick(self) -> None:
        """Reap games whose process is gone and finish a pending shutdown."""
        for game in self.games:
            if not self._alive(game.pid):
                self.exit(game.token)
        if (
            self._deactivate_at is not None
            and not self._games
            and self._clock() >= self._deactivate_at
        ):
            self._deactivate_at = None
            self._deactivate()

    def _deactivate(self) -> None:
        if self._active:
            self._active = False
            self._on_change(False, "")
