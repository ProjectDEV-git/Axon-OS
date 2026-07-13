"""Sample Axon OS plugin service.

Demonstrates how to build a service plugin using ServiceBase.
To install: place this directory under ~/.local/share/axon/plugins/
"""

import sys
import threading
from pathlib import Path

_plugin_parent = str(Path(__file__).resolve().parents[1])
if _plugin_parent not in sys.path:
    sys.path.insert(0, _plugin_parent)

import dbus
from service_base import ServiceBase


class SamplePluginService(ServiceBase):
    BUS_NAME = "org.axonos.plugins.Sample"
    OBJECT_PATH = "/org/axonos/plugins/Sample"
    SERVICE_NAME = "sample-plugin"

    def _setup(self) -> None:
        self._counter = 0
        self._counter_lock = threading.Lock()

    @dbus.service.method("org.axonos.plugins.Sample", out_signature="s")
    def Hello(self):
        with self._counter_lock:
            self._counter += 1
            count = self._counter
        return f"Hello from SamplePlugin! (call #{count})"

    @dbus.service.method("org.axonos.plugins.Sample", in_signature="s", out_signature="s")
    def Echo(self, message: str) -> str:
        return message


def create_service() -> SamplePluginService:
    """Factory function called by the plugin registry."""
    return SamplePluginService()


if __name__ == "__main__":
    SamplePluginService.main()
