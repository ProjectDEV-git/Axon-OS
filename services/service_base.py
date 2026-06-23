"""Base class for Axon OS D-Bus services.

Encapsulates the repeated boilerplate found in every service:
  - GLib main loop integration
  - Bus name claim with duplicate detection
  - Object path registration
  - Logger setup
  - Health check / status reporting

Services subclass ServiceBase and implement their D-Bus methods on top.
"""

import sys
import threading
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

try:
    from axon_logger import configure_app_logger
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from axon_logger import configure_app_logger
    except ImportError:
        import logging as _logging

        def configure_app_logger(name, level=_logging.INFO, log_file=None):
            _logging.basicConfig(level=level)
            return _logging.getLogger(name)


class ServiceBase(dbus.service.Object):
    """Base class for all Axon D-Bus services.

    Subclasses must define the class attributes below and implement
    ``_setup()`` for service-specific initialization.

    Class attributes (required):
        BUS_NAME: D-Bus bus name, e.g. ``"org.axonos.Brain"``
        OBJECT_PATH: D-Bus object path, e.g. ``"/org/axonos/Brain"``
        SERVICE_NAME: Human-readable name for logging, e.g. ``"axon-brain"``
    """

    BUS_NAME: str
    OBJECT_PATH: str
    SERVICE_NAME: str

    def __init__(self) -> None:
        # --- GLib / D-Bus bootstrap ---
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()

        try:
            self.bus_name = dbus.service.BusName(self.BUS_NAME, bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            # Use print here because logger may not be ready yet
            print(f"{self.BUS_NAME} service is already running.", file=sys.stderr)  # noqa: T201
            sys.exit(1)

        dbus.service.Object.__init__(self, self.session_bus, self.OBJECT_PATH)

        # --- Logging ---
        self.logger = configure_app_logger(self.SERVICE_NAME)

        # --- Health / lifecycle tracking ---
        self._start_time = time.monotonic()
        self._healthy = True
        self._health_lock = threading.Lock()

        # --- Service-specific setup ---
        self._setup()

        self.logger.info(
            "%s registered successfully at %s", self.SERVICE_NAME, self.OBJECT_PATH
        )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        """Service-specific initialization. Called after D-Bus registration.

        Subclasses must override this method.
        """

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------

    @property
    def uptime(self) -> float:
        """Seconds since service started."""
        return time.monotonic() - self._start_time

    def set_healthy(self, healthy: bool) -> None:
        """Mark the service as healthy or unhealthy."""
        with self._health_lock:
            self._healthy = healthy

    def is_healthy(self) -> bool:
        """Return current health status."""
        with self._health_lock:
            return self._healthy

    # ------------------------------------------------------------------
    # Standard D-Bus methods (available on every service)
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.Service", out_signature="s")
    def GetStatus(self):
        """Return JSON status string with uptime and health."""
        import json

        return json.dumps(
            {
                "service": self.SERVICE_NAME,
                "healthy": self.is_healthy(),
                "uptime": round(self.uptime, 1),
            }
        )

    @dbus.service.method("org.axonos.Service", out_signature="s")
    def GetServiceName(self):
        """Return the service name."""
        return self.SERVICE_NAME

    # ------------------------------------------------------------------
    # Main loop entry point
    # ------------------------------------------------------------------

    @classmethod
    def main(cls) -> None:
        """Instantiate and run the GLib main loop. Call from ``if __name__``."""
        loop = GLib.MainLoop()
        service = cls()  # noqa: F841 — side effect: starts D-Bus service
        try:
            loop.run()
        except KeyboardInterrupt:
            loop.quit()
