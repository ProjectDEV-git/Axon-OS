#!/usr/bin/env python3
"""Axon Gaming — game mode coordinator.

Exposes org.axonos.Gaming on the session bus. When a game starts (GameMode's
``start`` hook runs ``axon-game-mode on``) the service asks the AI services to
get out of the way: Brain unloads its models and refuses GPU work, Search stops
indexing, Context polls less. When the last game ends, and after a short grace
period, everything is resumed.

The on/off rules live in ``game_mode.py`` (no D-Bus, easy to test); this file
wires them to the bus and to the other services.

Game mode always starts *off*. A restarted Gaming service resumes every service
it can reach, so a crash can never leave Brain suspended.
"""

import json
import sys
import time
from pathlib import Path

import dbus
import dbus.service
from gi.repository import GLib

_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)
from constants import (
    AXON_CONFIG_DIR,
    DBUS_NAME_BRAIN,
    DBUS_NAME_CONTEXT,
    DBUS_NAME_GAMING,
    DBUS_NAME_SEARCH,
)

_this = str(Path(__file__).resolve().parent)
if _this not in sys.path:
    sys.path.insert(0, _this)
import game_mode
import gaming_settings
import steam_games
from service_base import ServiceBase

IFACE = DBUS_NAME_GAMING
SETTINGS_FILE = AXON_CONFIG_DIR / "gaming.json"

# Short D-Bus timeout (seconds): a stuck service must not stall game launch.
_CALL_TIMEOUT = 5
# How often to look for Steam games (a scan reads the command line of the few processes
# that mention Steam; the grace period hides a launcher that restarts its game)
STEAM_POLL_SECONDS = 4


class GamingService(ServiceBase):
    BUS_NAME = DBUS_NAME_GAMING
    OBJECT_PATH = "/org/axonos/Gaming"
    SERVICE_NAME = "axon-gaming"

    def _setup(self):
        self._settings = gaming_settings.load(SETTINGS_FILE)
        self._manager = game_mode.GameModeManager(
            on_change=self._on_mode_change, clock=time.monotonic
        )
        self._grace_source = 0
        self._steam = steam_games.SteamWatcher(self._steam_enter, self._steam_leave)
        # Start from a known state: nothing suspended, whatever happened before.
        self._resume_all()
        GLib.timeout_add_seconds(int(game_mode.DEFAULT_POLL_SECONDS), self._on_poll)
        GLib.timeout_add_seconds(STEAM_POLL_SECONDS, self._on_steam_poll)
        # A service that (re)starts during a game must be put back to sleep.
        self.session_bus.add_signal_receiver(
            self._on_name_owner_changed,
            signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus",
        )

    # ------------------------------------------------------------------
    # D-Bus API
    # ------------------------------------------------------------------

    @dbus.service.method(IFACE, in_signature="su", out_signature="s")
    def EnterGameMode(self, app_id, pid):
        """Start game mode for a game; returns a token for ExitGameMode."""
        token = self._manager.enter(str(app_id), int(pid))
        self.logger.info("Game started: %s (pid %d)", app_id, pid)
        self.notify_property_changed(IFACE, "ActiveGames")
        return token

    @dbus.service.method(IFACE, in_signature="s", out_signature="")
    def ExitGameMode(self, token):
        """Release one game. Game mode ends 10 s after the last one."""
        if self._manager.exit(str(token)):
            self.logger.info("Game ended (token %s)", str(token)[:8])
            self.notify_property_changed(IFACE, "ActiveGames")
            self._arm_grace_timer()

    @dbus.service.method(IFACE, in_signature="", out_signature="")
    def ForceExit(self):
        """Forget every game and resume everything now."""
        self.logger.info("Game mode forced off")
        self._manager.force_exit()
        self.notify_property_changed(IFACE, "ActiveGames")

    @dbus.service.method(IFACE, in_signature="", out_signature="s")
    def GetSettings(self):
        """The user's game mode settings as JSON."""
        return json.dumps(self._settings)

    @dbus.service.method(IFACE, in_signature="sb", out_signature="b")
    def SetSetting(self, key, value):
        """Change one setting ("pause_ai", "use_cloud", "pause_indexing" or "steam_games")."""
        key = str(key)
        if key not in gaming_settings.DEFAULTS:
            return False
        self._settings[key] = bool(value)
        gaming_settings.save(SETTINGS_FILE, self._settings)
        if key == "steam_games":
            self._on_steam_poll()  # look now (or let go of the games it was holding)
        if self._manager.active:
            self._apply_active()  # take effect now, not at the next game
        return True

    @dbus.service.signal(IFACE, signature="bs")
    def GameModeChanged(self, active, app_id):
        """Fired on every on/off transition."""

    def get_dbus_properties(self, interface):
        if interface != IFACE:
            return {}
        return {
            "Active": dbus.Boolean(self._manager.active),
            "ActiveGames": dbus.Array(
                [(g.token, g.app_id, dbus.UInt32(g.pid)) for g in self._manager.games],
                signature="(ssu)",
            ),
        }

    # ------------------------------------------------------------------
    # State changes
    # ------------------------------------------------------------------

    def _on_mode_change(self, active, app_id):
        if active:
            self._apply_active()
        else:
            self._resume_all()
        self.logger.info("Game mode %s", "on" if active else "off")
        self.GameModeChanged(active, app_id)
        self.notify_property_changed(IFACE, "Active", "ActiveGames")

    def _steam_enter(self, app_id, pid):
        token = self._manager.enter(app_id, pid)
        self.logger.info("Steam game started: %s (pid %d)", app_id, pid)
        self.notify_property_changed(IFACE, "ActiveGames")
        return token

    def _steam_leave(self, token):
        if self._manager.exit(token):
            self.logger.info("Steam game ended")
            self.notify_property_changed(IFACE, "ActiveGames")
            self._arm_grace_timer()

    def _on_steam_poll(self):
        """Follow Steam's own games. Off when the user turned the setting off."""
        try:
            if self._settings["steam_games"]:
                self._steam.poll()
            elif self._steam.tracked:
                self._steam.stop()
        except Exception:  # a scan problem must never stop the timer
            self.logger.exception("Steam game scan failed")
        return True

    def _on_poll(self):
        self._manager.tick()
        self._arm_grace_timer()
        return True

    def _arm_grace_timer(self):
        """Wake up when the grace period ends instead of waiting for the next poll."""
        if self._grace_source or not self._manager.grace_pending:
            return
        delay_ms = int(game_mode.DEFAULT_GRACE_SECONDS * 1000) + 100
        self._grace_source = GLib.timeout_add(delay_ms, self._on_grace_timer)

    def _on_grace_timer(self):
        self._grace_source = 0
        self._manager.tick()
        return False

    def _on_name_owner_changed(self, name, _old_owner, new_owner):
        if (
            new_owner
            and self._manager.active
            and name
            in (
                DBUS_NAME_BRAIN,
                DBUS_NAME_SEARCH,
                DBUS_NAME_CONTEXT,
            )
        ):
            self._apply_active(only=name)

    # ------------------------------------------------------------------
    # Talking to the other services
    # ------------------------------------------------------------------

    def _proxy(self, name, path):
        """A proxy for a service that is already running, else None.

        Never D-Bus-activates a service: suspending Brain must not start it.
        """
        try:
            if not self.session_bus.name_has_owner(name):
                return None
            return dbus.Interface(self.session_bus.get_object(name, path), name)
        except dbus.exceptions.DBusException:
            return None

    def _call(self, name, path, method, *args):
        proxy = self._proxy(name, path)
        if proxy is None:
            return
        try:
            getattr(proxy, method)(*args, timeout=_CALL_TIMEOUT)
        except dbus.exceptions.DBusException as exc:
            self.logger.warning("%s.%s failed: %s", name, method, exc)

    def _apply_active(self, only=None):
        """Put the AI services to sleep (all, or just ``only`` if it just started)."""
        s = self._settings
        if only in (None, DBUS_NAME_BRAIN):
            if s["pause_ai"]:
                self._call(DBUS_NAME_BRAIN, "/org/axonos/Brain", "SetGameModeCloud", s["use_cloud"])
                self._call(DBUS_NAME_BRAIN, "/org/axonos/Brain", "Suspend", "game mode")
            else:
                self._call(DBUS_NAME_BRAIN, "/org/axonos/Brain", "Resume")
        if only in (None, DBUS_NAME_SEARCH):
            method = "Pause" if s["pause_indexing"] else "Resume"
            self._call(DBUS_NAME_SEARCH, "/org/axonos/Search", method)
        if only in (None, DBUS_NAME_CONTEXT):
            self._call(DBUS_NAME_CONTEXT, "/org/axonos/Context", "Throttle", True)

    def _resume_all(self):
        self._call(DBUS_NAME_BRAIN, "/org/axonos/Brain", "Resume")
        self._call(DBUS_NAME_SEARCH, "/org/axonos/Search", "Resume")
        self._call(DBUS_NAME_CONTEXT, "/org/axonos/Context", "Throttle", False)


if __name__ == "__main__":
    GamingService.main()
