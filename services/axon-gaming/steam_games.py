"""Find running Steam games without help from the game.

Game mode normally starts when GameMode says a game has started, but a Steam game only does that
if its launch options say ``gamemoderun %command%``. Most never will, so the Gaming service also
looks at the processes: Steam starts every game (Linux native and Proton) under its own
``reaper`` helper, ``reaper SteamLaunch AppId=<id> -- <the game>``, and it exists for as long as the
game does. No D-Bus or GLib here, so the rules are testable with a fake process list.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

_APP_ID = re.compile(r"^AppId=(\d+)$")
# Steam tools and runtimes that run under reaper but are not a game the user is playing
NOT_GAMES = frozenset({"0"})


def steam_app_id(argv: list[str]) -> str | None:
    """The app id if ``argv`` is Steam's reaper for a game ("reaper SteamLaunch AppId=620 -- ...")."""
    if "SteamLaunch" not in argv:
        return None
    # Only the part before "--" belongs to reaper; the game's own arguments come after it
    head = argv[: argv.index("--")] if "--" in argv else argv
    if not head or Path(head[0]).name != "reaper":
        return None
    for arg in head:
        m = _APP_ID.match(arg)
        if m and m.group(1) not in NOT_GAMES:
            return m.group(1)
    return None


def scan_proc(proc: Path = Path("/proc")) -> list[tuple[int, list[str]]]:
    """(pid, argv) of every process whose command line can be read."""
    found: list[tuple[int, list[str]]] = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue  # the process ended, or is not ours to read
        if b"SteamLaunch" not in raw:  # cheap test before splitting
            continue
        found.append((int(entry.name), raw.decode("utf-8", "replace").split("\0")))
    return found


def running_games(processes: Iterable[tuple[int, list[str]]]) -> dict[str, int]:
    """app id -> pid of its reaper. If a game has two (a relaunch), the lowest pid is kept."""
    games: dict[str, int] = {}
    for pid, argv in processes:
        app_id = steam_app_id(argv)
        if app_id is not None and (app_id not in games or pid < games[app_id]):
            games[app_id] = pid
    return games


class SteamWatcher:
    """Turns successive scans into "started" and "ended" games.

    ``enter(app_id, pid) -> token`` and ``leave(token)`` are the game mode manager's; the watcher
    keeps the token of each game it entered, so it only ever releases what it started (GameMode's
    own tokens, for the same game, are separate).
    """

    def __init__(
        self,
        enter: Callable[[str, int], str],
        leave: Callable[[str], object],
        scan: Callable[[], Iterable[tuple[int, list[str]]]] = scan_proc,
    ) -> None:
        self._enter = enter
        self._leave = leave
        self._scan = scan
        self._tokens: dict[str, str] = {}  # app id -> token

    @property
    def tracked(self) -> list[str]:
        return sorted(self._tokens)

    def poll(self) -> tuple[list[str], list[str]]:
        """Look once. Returns (app ids started, app ids ended)."""
        now = running_games(self._scan())
        started = sorted(a for a in now if a not in self._tokens)
        ended = sorted(a for a in self._tokens if a not in now)
        for app_id in started:
            self._tokens[app_id] = self._enter(f"steam:{app_id}", now[app_id])
        for app_id in ended:
            self._leave(self._tokens.pop(app_id))
        return started, ended

    def stop(self) -> list[str]:
        """Release every game this watcher entered (the setting was turned off)."""
        ended = sorted(self._tokens)
        for app_id in ended:
            self._leave(self._tokens.pop(app_id))
        return ended
