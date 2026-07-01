"""Telemetry & Crash Reporting for Axon OS — opt-in only.

Collects anonymous usage metrics and crash reports. All data stays
local in ~/.axon/telemetry/ unless the user explicitly opts in to
remote reporting. No PII is ever collected.

Usage::

    from telemetry import Telemetry

    telemetry = Telemetry()
    telemetry.track_event("app_launch", {"app": "files"})
    telemetry.track_crash("axon-brain", "ConnectionError", traceback_str)
"""

import json
import platform
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from axon_logger import configure_app_logger
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


log = configure_app_logger("telemetry")

TELEMETRY_DIR = Path.home() / ".local" / "share" / "axon" / "telemetry"
OPT_IN_FILE = TELEMETRY_DIR / "opt_in"
EVENTS_FILE = TELEMETRY_DIR / "events.jsonl"
CRASHES_FILE = TELEMETRY_DIR / "crashes.jsonl"
DAILY_FILE = TELEMETRY_DIR / "daily.json"
_MAX_JSONL_BYTES = 5 * 1024 * 1024  # 5 MB per JSONL file before rotation


class Telemetry:
    """Opt-in telemetry and crash reporting.

    All data is stored locally. Remote reporting only happens if
    ``~/.local/share/axon/telemetry/opt_in`` exists.
    """

    def __init__(self, enabled: bool | None = None) -> None:
        self._lock = threading.Lock()
        self._session_id = f"{int(time.time())}"
        self._enabled = enabled if enabled is not None else self._check_opt_in()
        self._daily: dict = {}
        self._load_daily()
        if self._enabled:
            log.info("Telemetry enabled (session %s)", self._session_id)

    @staticmethod
    def _dir() -> Path:
        return TELEMETRY_DIR

    @staticmethod
    def _opt_in_file() -> Path:
        return TELEMETRY_DIR / "opt_in"

    @staticmethod
    def _events_file() -> Path:
        return TELEMETRY_DIR / "events.jsonl"

    @staticmethod
    def _crashes_file() -> Path:
        return TELEMETRY_DIR / "crashes.jsonl"

    @staticmethod
    def _daily_file() -> Path:
        return TELEMETRY_DIR / "daily.json"

    @staticmethod
    def _check_opt_in() -> bool:
        return (TELEMETRY_DIR / "opt_in").exists()

    def opt_in(self) -> None:
        """Enable telemetry."""
        self._dir().mkdir(parents=True, exist_ok=True)
        self._opt_in_file().write_text(
            json.dumps({"opted_in": True, "ts": datetime.now(timezone.utc).isoformat()})
        )
        self._enabled = True
        log.info("Telemetry opted in")

    def opt_out(self) -> None:
        """Disable telemetry and remove opt-in marker."""
        f = self._opt_in_file()
        if f.exists():
            f.unlink()
        self._enabled = False
        log.info("Telemetry opted out")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Event tracking
    # ------------------------------------------------------------------

    def track_event(self, event_name: str, data: dict | None = None) -> None:
        """Record an anonymous usage event."""
        if not self._enabled:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self._session_id,
            "event": event_name,
            "data": data or {},
        }
        self._append_jsonl(self._events_file(), entry)
        self._update_daily(event_name)

    def track_crash(self, service: str, error_type: str, traceback_str: str) -> None:
        """Record a crash report (no PII)."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self._session_id,
            "service": service,
            "error": error_type,
            "traceback": traceback_str[:4096],
            "platform": {
                "os": platform.system(),
                "release": platform.release(),
                "python": platform.python_version(),
            },
        }
        self._append_jsonl(self._crashes_file(), entry)
        log.warning("Crash recorded: %s/%s", service, error_type)

    def track_service_start(self, service_name: str) -> None:
        """Record a service start event."""
        self.track_event("service_start", {"service": service_name})

    def track_service_error(self, service_name: str, error: str) -> None:
        """Record a service error."""
        self.track_event("service_error", {"service": service_name, "error": error[:256]})

    # ------------------------------------------------------------------
    # Daily aggregation
    # ------------------------------------------------------------------

    def _load_daily(self) -> None:
        with self._lock:
            f = self._daily_file()
            if f.exists():
                try:
                    self._daily = json.loads(f.read_text())
                except Exception:
                    self._daily = {}

    def _update_daily(self, event_name: str) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if self._daily.get("date") != today:
                self._daily = {"date": today, "events": {}, "services": []}
            events = self._daily.setdefault("events", {})
            events[event_name] = events.get(event_name, 0) + 1
            try:
                save_data = {
                    "date": self._daily["date"],
                    "events": self._daily["events"],
                    "services": self._daily.get("services", []),
                }
                tmp = self._daily_file().with_suffix(".tmp")
                tmp.write_text(json.dumps(save_data, indent=2))
                tmp.replace(self._daily_file())
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Summary / report
    # ------------------------------------------------------------------

    def get_summary(self) -> dict:
        """Return today's telemetry summary."""
        if not self._enabled:
            return {"enabled": False}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily.get("date") != today:
            return {"enabled": True, "date": today, "events": {}, "crashes": 0}

        crash_count = 0
        cf = self._crashes_file()
        if cf.exists():
            try:
                for line in cf.read_text().splitlines():
                    if line.strip():
                        entry = json.loads(line)
                        if entry.get("ts", "").startswith(today):
                            crash_count += 1
            except Exception:
                pass

        return {
            "enabled": True,
            "date": today,
            "events": self._daily.get("events", {}),
            "crashes": crash_count,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _append_jsonl(path: Path, entry: dict) -> None:
        try:
            TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
            # Rotate if file exceeds max size
            try:
                if path.exists() and path.stat().st_size > _MAX_JSONL_BYTES:
                    rotated = path.with_suffix(".jsonl.1")
                    if rotated.exists():
                        rotated.unlink()
                    path.rename(rotated)
            except OSError:
                pass
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass


# Singleton instance
_instance: Telemetry | None = None
_instance_lock = threading.Lock()


def get_telemetry() -> Telemetry:
    """Get or create the singleton Telemetry instance."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = Telemetry()
    return _instance
