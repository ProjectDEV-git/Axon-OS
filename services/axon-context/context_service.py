#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

try:
    from axon_logger import configure_app_logger
except ImportError:  # running standalone — repo root / installed shim not on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from axon_logger import configure_app_logger
    except ImportError:
        import logging as _logging

        def configure_app_logger(
            name: str,
            level: int = _logging.INFO,
            log_file: str | None = None,
            json_output: bool = False,
        ) -> _logging.Logger:
            _logging.basicConfig(level=level)
            return _logging.getLogger(name)


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from constants import AXON_DIR, MAX_CLIPBOARD_ENTRY_LEN, MAX_CLIPBOARD_HISTORY

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clipboard_store import ClipboardStore

logger = configure_app_logger(__name__)


class ContextService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()

        try:
            self.bus_name = dbus.service.BusName("org.axonos.Context", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            logger.error("org.axonos.Context service is already running.")
            sys.exit(1)

        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/Context")

        # State tracking (updated dynamically by shell extension or helpers)
        self.active_window_title = "None"
        self.active_window_app = "None"
        self.active_space = "Default"

        self.track_clipboard = True
        self.track_terminal_history = True
        self.track_open_files = True

        # Clipboard history with SQLite persistence
        self._clipboard_store = ClipboardStore(
            max_entries=MAX_CLIPBOARD_HISTORY, max_entry_len=MAX_CLIPBOARD_ENTRY_LEN
        )
        self._clipboard_history = self._clipboard_store.to_deque()
        self._clipboard_watcher = None
        self._clipboard_watch_id = None
        self._config_mtime = 0.0
        self._terminal_cache = None
        self._terminal_cache_mtime = {}
        self._start_clipboard_watcher()

        logger.info("Axon Context Engine Service registered successfully at /org/axonos/Context")

    def _load_config(self):
        config_path = Path.home() / ".config" / "axon-os" / "context.json"
        if not config_path.exists():
            return
        try:
            mtime = config_path.stat().st_mtime
            if mtime == self._config_mtime:
                return
            self._config_mtime = mtime
            with open(config_path) as f:
                cfg = json.load(f)
                self.track_clipboard = cfg.get("track_clipboard", True)
                self.track_terminal_history = cfg.get("track_terminal_history", True)
                self.track_open_files = cfg.get("track_open_files", True)
        except Exception as e:
            logger.warning("Failed to load context config: %s", e)

    # ------------------------------------------------------------------
    # D-Bus Mutation Methods (Called by Shell Extension / Hooks)
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.Context", in_signature="ss", out_signature="b")
    def SetActiveWindow(self, title, app_id):
        """Called by GNOME shell extension when focus changes."""
        self.active_window_title = str(title)
        self.active_window_app = str(app_id)
        self.ContextChanged(self.GetActiveContext())
        return True

    @dbus.service.method("org.axonos.Context", in_signature="s", out_signature="b")
    def SetActiveSpace(self, space_name):
        """Called by GNOME shell extension when space changes."""
        self.active_space = str(space_name)
        self.ContextChanged(self.GetActiveContext())
        return True

    # ------------------------------------------------------------------
    # D-Bus Query Methods
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.Context", in_signature="", out_signature="s")
    def GetActiveContext(self):
        """Aggregates all current session context into a JSON string."""
        context = {
            "active_window": {"title": self.active_window_title, "app": self.active_window_app},
            "active_space": self.active_space,
            "open_files": self._get_open_files(),
            "terminal_commands": self._get_terminal_commands(),
            "last_stderr": self._get_last_stderr(),
            "clipboard_history": list(self._clipboard_history),
        }
        return json.dumps(context)

    @dbus.service.method("org.axonos.Context", in_signature="", out_signature="s")
    def GetContextString(self):
        """Formats context for injection into LLM system prompts."""
        parts = []

        if self.active_window_title and self.active_window_title != "None":
            parts.append(
                f"Active window: {self.active_window_title} (App: {self.active_window_app})"
            )

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

        if self._clipboard_history:
            parts.append("Recent clipboard entries:")
            for i, entry in enumerate(reversed(list(self._clipboard_history)), 1):
                # Truncate long entries for prompt injection
                display = entry[:120] + "..." if len(entry) > 120 else entry
                parts.append(f"  [{i}] {display}")

        if not parts:
            return "No desktop context available."

        return "\n".join(parts)

    @dbus.service.method("org.axonos.Context", in_signature="s", out_signature="s")
    def SemanticSearch(self, query_text):
        """Performs vector search in the indexed documents using sqlite-vec."""
        conn = None
        try:
            import sqlite3
            from array import array

            import sqlite_vec

            # 1. Fetch embedding of query_text via Brain service
            brain_obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            brain_interface = dbus.Interface(brain_obj, "org.axonos.Brain")
            emb_json = brain_interface.GetEmbeddings(query_text, "")
            emb = json.loads(emb_json)
            if not emb or not isinstance(emb, list) or not isinstance(emb[0], (int, float)):
                return "[]"

            from constants import SEMANTIC_INDEX_DB

            db_path = str(SEMANTIC_INDEX_DB)
            if not os.path.exists(db_path):
                return "[]"

            conn = sqlite3.connect(db_path)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)

            cursor = conn.cursor()
            emb_bytes = array("f", emb).tobytes()

            cursor.execute(
                """
                SELECT f.path, f.content, vec.distance
                FROM vec_items vec
                JOIN files f ON f.id = vec.rowid
                WHERE vec_embedding MATCH ?
                ORDER BY distance
                LIMIT 5
            """,
                (emb_bytes,),
            )

            results = []
            for row in cursor.fetchall():
                results.append({"path": row[0], "content": row[1], "distance": row[2]})
            return json.dumps(results)
        except Exception as e:
            return json.dumps({"error": str(e)})
        finally:
            if conn is not None:
                conn.close()

    # ------------------------------------------------------------------
    # D-Bus Signals
    # ------------------------------------------------------------------

    @dbus.service.signal("org.axonos.Context", signature="s")
    def ContextChanged(self, context_json):
        """Fires when any critical context parameter changes."""
        pass

    # ------------------------------------------------------------------
    # Clipboard Watcher
    # ------------------------------------------------------------------

    def _start_clipboard_watcher(self):
        """Starts a background clipboard watcher using wl-paste (Wayland) or xclip (X11)."""
        self._load_config()
        if not self.track_clipboard:
            logger.info("Clipboard tracking is disabled by privacy settings.")
            return

        # Try Wayland first (wl-paste --watch), fall back to polling xclip
        try:
            self._clipboard_watcher = subprocess.Popen(
                ["wl-paste", "--watch", "cat"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            # Read clipboard changes in a GLib IO watch
            if self._clipboard_watcher.stdout is not None:
                self._clipboard_watch_id = GLib.io_add_watch(
                    self._clipboard_watcher.stdout.fileno(),
                    GLib.IO_IN | GLib.IO_HUP,
                    self._on_clipboard_data,
                )
            logger.info("Clipboard watcher started (Wayland/wl-paste)")
            return
        except FileNotFoundError:
            logger.debug("wl-paste not found, trying X11 fallback")
        except Exception as e:
            logger.debug("wl-paste failed: %s", e)

        # X11 fallback: poll xclip every 2 seconds
        self._last_xclip_content = ""
        GLib.timeout_add_seconds(2, self._poll_xclip)
        logger.info("Clipboard watcher started (X11/xclip polling)")

    def cleanup(self):
        """Terminate the clipboard watcher subprocess on shutdown."""
        if self._clipboard_watcher is not None:
            try:
                self._clipboard_watcher.terminate()
                self._clipboard_watcher.wait(timeout=2)
            except Exception:
                try:
                    self._clipboard_watcher.kill()
                except Exception:
                    pass
            self._clipboard_watcher = None
        if self._clipboard_watch_id is not None:
            try:
                GLib.source_remove(self._clipboard_watch_id)
            except Exception:
                pass
            self._clipboard_watch_id = None

    def _on_clipboard_data(self, fd, condition):
        """GLib IO callback for wl-paste --watch output."""
        if condition & GLib.IO_HUP:
            return False  # watcher died
        self._load_config()
        if not self.track_clipboard:
            return True
        try:
            data = os.read(fd, 4096)
            if data:
                text = data.decode("utf-8", errors="replace").strip()
                if text:
                    added = self._clipboard_store.add(text)
                    if added:
                        self._clipboard_history = self._clipboard_store.to_deque()
                        self.ContextChanged(self.GetActiveContext())
        except Exception as e:
            logger.debug("Clipboard data read error: %s", e)
        return True  # keep watching

    def _poll_xclip(self):
        """Polls xclip for clipboard changes (X11 fallback)."""
        self._load_config()
        if not self.track_clipboard:
            return True
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            text = result.stdout.strip()
            if text:
                added = self._clipboard_store.add(text)
                if added:
                    self._clipboard_history = self._clipboard_store.to_deque()
                    self.ContextChanged(self.GetActiveContext())
        except Exception as e:
            logger.debug("xclip poll error: %s", e)
        return True  # keep polling

    # ------------------------------------------------------------------
    # Context Fetching Helpers
    # ------------------------------------------------------------------

    def _get_open_files(self):
        self._load_config()
        if not self.track_open_files:
            return []
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
        except Exception as e:
            logger.debug("Failed to scan /proc for open files: %s", e)
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
        self._load_config()
        if not self.track_terminal_history:
            return []

        # Check all candidate history files
        candidates = [
            Path.home() / ".bash_history",
            Path.home() / ".zsh_history",
            Path.home() / ".local" / "share" / "fish" / "fish_history",
        ]

        # Find the most recently modified history file
        active_path = None
        active_mtime = 0.0
        for p in candidates:
            try:
                mtime = p.stat().st_mtime
                if mtime > active_mtime:
                    active_mtime = mtime
                    active_path = p
            except OSError:
                continue

        if active_path is None:
            return []

        # Return cached results if file hasn't changed
        cache_key = str(active_path)
        if (
            self._terminal_cache is not None
            and self._terminal_cache_mtime.get(cache_key) == active_mtime
        ):
            return self._terminal_cache[-n:]

        # Read and parse the history file
        commands = self._read_history_file(active_path)
        self._terminal_cache = commands
        self._terminal_cache_mtime[cache_key] = active_mtime
        return commands[-n:]

    def _read_history_file(self, path, max_lines=50):
        """Read and parse a shell history file, returning the last max_lines commands."""
        commands = []
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Bash: skip timestamp lines
                    if path.name == ".bash_history" and line.startswith("#"):
                        continue
                    # Zsh extended format: `: <timestamp>:<duration>;command`
                    match = re.match(r"^:\s*\d+:\d+(?::\d+)?;(.+)$", line)
                    if match:
                        commands.append(match.group(1))
                    # Fish format: `- cmd: command`
                    elif path.name == "fish_history":
                        fish_match = re.match(r"^- cmd: (.+)$", line)
                        if fish_match:
                            commands.append(fish_match.group(1))
                    else:
                        commands.append(line)
                    if len(commands) > max_lines * 2:
                        commands = commands[-max_lines:]
        except Exception as e:
            logger.debug("Failed to read history file %s: %s", path, e)
        return commands[-max_lines:]

    def _get_last_stderr(self):
        stderr_file = AXON_DIR / "last_stderr"
        try:
            if stderr_file.exists():
                content = stderr_file.read_text(errors="replace").strip()
                return content if content else None
        except Exception as e:
            logger.debug("Failed to read last_stderr: %s", e)
        return None


if __name__ == "__main__":
    import signal

    loop = GLib.MainLoop()
    service = ContextService()

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        service.cleanup()
        loop.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        loop.run()
    except KeyboardInterrupt:
        logger = configure_app_logger(__name__)
        logger.info("Stopping Axon Context service...")
    finally:
        service.cleanup()
        loop.quit()
