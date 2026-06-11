#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib
from axon_logger import configure_app_logger

AXON_DIR = Path.home() / ".axon"

class ContextService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        
        logger = configure_app_logger(__name__)
        try:
            self.bus_name = dbus.service.BusName('org.axonos.Context', bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            logger.error("org.axonos.Context service is already running.")
            sys.exit(1)
            
        dbus.service.Object.__init__(self, self.session_bus, '/org/axonos/Context')
        
        # State tracking (updated dynamically by shell extension or helpers)
        self.active_window_title = "None"
        self.active_window_app = "None"
        self.active_space = "Default"
        
        logger.info("Axon Context Engine Service registered successfully at /org/axonos/Context")

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

    @dbus.service.method('org.axonos.Context', in_signature='s', out_signature='s')
    def SemanticSearch(self, query_text):
        """Performs vector search in the indexed documents using sqlite-vec."""
        try:
            import sqlite3
            import sqlite_vec
            from array import array
            
            # 1. Fetch embedding of query_text via Brain service
            brain_obj = self.session_bus.get_object('org.axonos.Brain', '/org/axonos/Brain')
            brain_interface = dbus.Interface(brain_obj, 'org.axonos.Brain')
            emb_json = brain_interface.GetEmbeddings(query_text, "")
            emb = json.loads(emb_json)
            if not emb or len(emb) != 768:
                return "[]"
                
            db_path = os.path.expanduser("~/.axon/semantic_search.db")
            if not os.path.exists(db_path):
                return "[]"
                
            conn = sqlite3.connect(db_path)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            
            cursor = conn.cursor()
            emb_bytes = array('f', emb).tobytes()
            
            cursor.execute("""
                SELECT f.path, f.content, vec.distance 
                FROM vec_items vec
                JOIN files f ON f.id = vec.rowid
                WHERE vec_embedding MATCH ?
                ORDER BY distance
                LIMIT 5
            """, (emb_bytes,))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "path": row[0],
                    "content": row[1],
                    "distance": row[2]
                })
            return json.dumps(results)
        except Exception as e:
            return json.dumps({"error": str(e)})

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
                commands = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
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
        logger = configure_app_logger(__name__)
        logger.info("Stopping Axon Context service...")
        loop.quit()
