#!/usr/bin/env python3
import os
import sys
import json
import re
import urllib.request
from pathlib import Path
from datetime import datetime

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

AXON_DIR = Path.home() / ".axon"

class ContextService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        
        try:
            self.bus_name = dbus.service.BusName('org.axonos.Context', bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            print("org.axonos.Context service is already running.")
            sys.exit(1)
            
        dbus.service.Object.__init__(self, self.session_bus, '/org/axonos/Context')
        
        # State tracking (updated dynamically by shell extension or helpers)
        self.active_window_title = "None"
        self.active_window_app = "None"
        self.active_space = "Default"
        
        print("Axon Context Engine Service registered successfully at /org/axonos/Context")

    # ------------------------------------------------------------------
    # D-Bus Mutation Methods (Called by Shell Extension / Hooks)
    # ------------------------------------------------------------------

    @dbus.service.method('org.axonos.Context', in_signature='ss', out_signature='b')
    def SetActiveWindow(self, title, app_id):
        """Called by GNOME shell extension when focus changes."""
        self.active_window_title = str(title)
        self.active_window_app = str(app_id)
        self.ContextChanged(self.GetActiveContext())
        return True

    @dbus.service.method('org.axonos.Context', in_signature='s', out_signature='b')
    def SetActiveSpace(self, space_name):
        """Called by GNOME shell extension when space changes."""
        self.active_space = str(space_name)
        self.ContextChanged(self.GetActiveContext())
        return True

    # ------------------------------------------------------------------
    # D-Bus Query Methods
    # ------------------------------------------------------------------

    @dbus.service.method('org.axonos.Context', in_signature='', out_signature='s')
    def GetActiveContext(self):
        """Aggregates all current session context into a JSON string."""
        context = {
            "active_window": {
                "title": self.active_window_title,
                "app": self.active_window_app
            },
            "active_space": self.active_space,
            "open_files": self._get_open_files(),
            "terminal_commands": self._get_terminal_commands(),
            "last_stderr": self._get_last_stderr()
        }
        return json.dumps(context)

    @dbus.service.method('org.axonos.Context', in_signature='', out_signature='s')
    def GetContextString(self):
        """Formats context for injection into LLM system prompts."""
        parts = []
        
        if self.active_window_title and self.active_window_title != "None":
            parts.append(f"Active window: {self.active_window_title} (App: {self.active_window_app})")
            
        parts.append(f"Current space: {self.active_space}")
        
        open_files = self._get_open_files()
        if open_files:
            parts.append("Open files in editors:")
            for f in open_files:
                parts.append(f"  - {f}")
                
        cmds = self._get_terminal_commands()
        if cmds:
            parts.append("Recent terminal commands:")
            for cmd in cmds:
                parts.append(f"  $ {cmd}")
                
        stderr = self._get_last_stderr()
        if stderr:
            parts.append(f"Last terminal error:\n{stderr}")
            
        if not parts:
            return "No desktop context available."
            
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # D-Bus Signals
    # ------------------------------------------------------------------

    @dbus.service.signal('org.axonos.Context', signature='s')
    def ContextChanged(self, context_json):
        """Fires when any critical context parameter changes."""
        pass

    # ------------------------------------------------------------------
    # Context Fetching Helpers
    # ------------------------------------------------------------------

    def _get_open_files(self):
        editor_names = {"gedit", "code", "vim", "nvim", "nano"}
        found_pids = []
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                comm_file = entry / "comm"
                try:
                    comm = comm_file.read_text().strip()
                except OSError:
                    continue
                if comm in editor_names:
                    found_pids.append(int(entry.name))
        except Exception:
            return []

        unique_paths = []
        seen = set()
        for pid in found_pids:
            fd_dir = Path(f"/proc/{pid}/fd")
            try:
                for fd_entry in fd_dir.iterdir():
                    try:
                        target = os.readlink(str(fd_entry))
                    except OSError:
                        continue
                    if not target.startswith("/"):
                        continue
                    p = Path(target)
                    try:
                        if not p.is_file():
                            continue
                    except OSError:
                        continue
                    if target not in seen:
                        seen.add(target)
                        unique_paths.append(target)
                    if len(unique_paths) >= 10:
                        break
            except (PermissionError, OSError):
                continue
            if len(unique_paths) >= 10:
                break
        return unique_paths

    def _get_terminal_commands(self, n=10):
        # Try bash history
        bash_history = Path.home() / ".bash_history"
        if bash_history.exists():
            try:
                lines = bash_history.read_text(errors="replace").splitlines()
                commands = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
                return commands[-n:]
            except Exception:
                pass
        
        # Fish history
        fish_history = Path.home() / ".local" / "share" / "fish" / "fish_history"
        if fish_history.exists():
            try:
                text = fish_history.read_text(errors="replace")
                commands = re.findall(r"^- cmd: (.+)$", text, re.MULTILINE)
                return commands[-n:]
            except Exception:
                pass
        return []

    def _get_last_stderr(self):
        stderr_file = AXON_DIR / "last_stderr"
        try:
            if stderr_file.exists():
                content = stderr_file.read_text(errors="replace").strip()
                return content if content else None
        except Exception:
            pass
        return None

if __name__ == '__main__':
    loop = GLib.MainLoop()
    service = ContextService()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("Stopping Axon Context service...")
        loop.quit()
