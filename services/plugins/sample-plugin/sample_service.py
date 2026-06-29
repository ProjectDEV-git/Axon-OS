"""Sample Axon OS plugin service.

Demonstrates how to build a service plugin using ServiceBase.
To install: place this directory under ~/.local/share/axon/plugins/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dbus
from service_base import ServiceBase


class SamplePluginService(ServiceBase):
    BUS_NAME = "org.axonos.plugins.Sample"
    OBJECT_PATH = "/org/axonos/plugins/Sample"
    SERVICE_NAME = "sample-plugin"

    def _setup(self) -> None:
        self._counter = 0

    @dbus.service.method("org.axonos.plugins.Sample", out_signature="s")
    def Hello(self):
        self._counter += 1
        return f"Hello from SamplePlugin! (call #{self._counter})"

    @dbus.service.method("org.axonos.plugins.Sample", out_signature="s")
    def Echo(self, message: str) -> str:
        return message


def create_service() -> SamplePluginService:
    """Factory function called by the plugin registry."""
    return SamplePluginService()


if __name__ == "__main__":
    SamplePluginService.main()
