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
from typing import Any

import dbus
import dbus.mainloop.glib
import dbus.service
from _log_helper import resolve_logger as configure_app_logger
from gi.repository import GLib


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
        try:
            self.session_bus = dbus.SessionBus()
        except dbus.exceptions.DBusException as exc:
            # Use print here because logger may not be ready yet
            print(f"Cannot connect to D-Bus session bus: {exc}", file=sys.stderr)  # noqa: T201
            sys.exit(1)

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

        self.logger.info("%s registered successfully at %s", self.SERVICE_NAME, self.OBJECT_PATH)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        """Service-specific initialization. Called after D-Bus registration.

        Subclasses must override this method.
        """

    def _cleanup(self) -> None:
        """Service-specific cleanup. Called on SIGTERM/SIGINT before exit.

        Subclasses should override to close DB connections, kill subprocesses,
        or release other resources.
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
    # D-Bus properties (org.freedesktop.DBus.Properties)
    # ------------------------------------------------------------------

    def get_dbus_properties(self, interface: str) -> dict[str, Any]:
        """Return the read-only properties of ``interface`` as ``{name: value}``.

        Services that expose properties override this and return typed dbus
        values (``dbus.Boolean``, ``dbus.Array`` ...). Others expose none.
        """
        return {}

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface_name, property_name):
        props = self.get_dbus_properties(str(interface_name))
        if str(property_name) not in props:
            raise dbus.exceptions.DBusException(
                f"No property {property_name} on {interface_name}",
                name="org.freedesktop.DBus.Error.UnknownProperty",
            )
        return props[str(property_name)]

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface_name):
        return dbus.Dictionary(self.get_dbus_properties(str(interface_name)), signature="sv")

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature="ssv", out_signature="")
    def Set(self, interface_name, property_name, value):
        raise dbus.exceptions.DBusException(
            f"Property {property_name} is read-only",
            name="org.freedesktop.DBus.Error.PropertyReadOnly",
        )

    @dbus.service.signal(dbus.PROPERTIES_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface_name, changed, invalidated):
        """Emitted when a property changes (see ``notify_property_changed``)."""

    def notify_property_changed(self, interface: str, *names: str) -> None:
        """Emit ``PropertiesChanged`` with the current values of ``names``."""
        props = self.get_dbus_properties(interface)
        changed = {n: props[n] for n in names if n in props}
        self.PropertiesChanged(interface, dbus.Dictionary(changed, signature="sv"), [])

    # ------------------------------------------------------------------
    # Main loop entry point
    # ------------------------------------------------------------------

    @classmethod
    def main(cls) -> None:
        """Instantiate and run the GLib main loop. Call from ``if __name__``."""
        import signal

        loop = GLib.MainLoop()
        service = cls()  # side effect: exports the service on the bus

        def _shutdown(signum, frame):
            service.logger.info("Received signal %d, shutting down...", signum)
            try:
                service._cleanup()
            except Exception:
                pass
            loop.quit()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        try:
            loop.run()
        except KeyboardInterrupt:
            loop.quit()
