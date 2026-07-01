---
name: new-service
description: Create a new D-Bus service for Axon OS following ServiceBase conventions
---

# New D-Bus Service

Create a new D-Bus service for Axon OS with proper structure, activation files, systemd units, and tests.

## When to Use

- User says "create a new service", "add a service", "new D-Bus service"
- Adding a new AI capability, system integration, or background daemon
- Creating a plugin that extends Axon OS functionality

## Prerequisites

- Understanding of what the service should do
- D-Bus interface design (method names, parameters, return types)
- Whether it needs Ollama, file system access, or other system resources

## Workflow

### Phase 1: Design

1. **Name the service**: Use `axon-<name>` convention (e.g., `axon-brain`, `axon-voice`)
2. **Choose D-Bus bus name**: `org.axonos.<Name>` (e.g., `org.axonos.Brain`)
3. **Define the object path**: `/org/axonos/<Name>` (e.g., `/org/axonos/Brain`)
4. **List D-Bus methods**: What operations does the service expose?

### Phase 2: Create Service Directory

```bash
mkdir -p services/axon-<name>
```

### Phase 3: Implement Service

Create `services/axon-<name>/<name>_service.py`:

```python
#!/usr/bin/env python3
"""Axon <Name> — <description>."""

from __future__ import annotations

import sys
from pathlib import Path

# Service import pattern for root-level modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import dbus
import dbus.service
from gi.repository import GLib

from services.axon_logger import configure_app_logger
from services.constants import DBUS_NAME_<NAME>

# If using ServiceBase:
# from services.service_base import ServiceBase


class <Name>Service(dbus.service.Object):
    """D-Bus service for <description>."""

    BUS_NAME = "org.axonos.<Name>"
    OBJECT_PATH = "/org/axonos/<Name>"
    INTERFACE_NAME = "org.axonos.<Name>"

    def __init__(self):
        self bus_name = dbus.service.BusName(self.BUS_NAME, bus=dbus.SessionBus())
        super().__init__(self bus_name, self.OBJECT_PATH)
        self.logger = configure_app_logger("<name>")
        self.logger.info("<Name> service started")

    # --- D-Bus methods ---

    @dbus.service.method(
        INTERFACE_NAME,
        in_signature="s",
        out_signature="s",
    )
    def DoSomething(self, input_text: str) -> str:
        """Example method."""
        self.logger.info("DoSomething called with: %s", input_text)
        return f"Result: {input_text}"

    # --- D-Bus signals ---

    @dbus.service.signal(INTERFACE_NAME, signature="s")
    def SomethingChanged(self, value: str):
        """Signal emitted when something changes."""


def main():
    service = <Name>Service()
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        loop.quit()


if __name__ == "__main__":
    main()
```

**Key conventions**:
- Use `configure_app_logger` from `axon_logger.py` for logging
- Import constants from `services/constants.py` (add DBUS_NAME_<NAME> there)
- Use `dbus.service.method` decorator with explicit `in_signature` and `out_signature`
- Use `dbus.service.signal` for events
- No `abc.ABC` (metaclass conflict with `dbus.service.Object`)

### Phase 4: Create D-Bus Activation File

Create `services/axon-<name>/org.axonos.<Name>.service`:

```ini
[D-BUS Service]
Name=org.axonos.<Name>
Exec=/usr/bin/python3 AXON_SERVICES_DIR/axon-<name>/<name>_service.py
User=%u
Environment=PYTHONPATH=AXON_SERVICES_DIR
```

**Note**: `AXON_SERVICES_DIR` is replaced at install time by `chroot-setup.sh`.

### Phase 5: Create D-Bus Policy (Optional)

Create `services/axon-<name>/org.axonos.<Name>.conf` if the service needs to be callable from other users:

```xml
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy context="default">
    <allow send_destination="org.axonos.<Name>"/>
    <allow receive_sender="org.axonos.<Name>"/>
  </policy>
</busconfig>
```

### Phase 6: Create Systemd Unit

Create `services/axon-<name>/axon-<name>.service`:

```ini
[Unit]
Description=Axon <Name> service
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=dbus
BusName=org.axonos.<Name>
ExecStart=/usr/bin/python3 AXON_SERVICES_DIR/axon-<name>/<name>_service.py
Restart=on-failure
RestartSec=5
Environment=PYTHONPATH=AXON_SERVICES_DIR

[Install]
WantedBy=default.target
```

**Key options**:
- `Type=dbus` with `BusName=` for D-Bus activation readiness
- `Restart=on-failure` for resilience
- `After=graphical-session.target` if service needs display
- Add `Nice=10` and `IOSchedulingClass=idle` for background services

### Phase 7: Add Constants

Add to `services/constants.py`:

```python
DBUS_NAME_<NAME> = "org.axonos.<Name>"
DBUS_PATH_<NAME> = "/org/axonos/<Name>"
```

### Phase 8: Write Tests

Create `tests/test_<name>.py`:

```python
"""Tests for axon-<name> service."""

import pytest
from unittest.mock import patch, MagicMock


class Test<Name>Service:
    """Test suite for <Name>Service."""

    def test_import(self):
        """Service module imports successfully."""
        from services.axon_<name>.<name>_service import <Name>Service
        assert <Name>Service is not None

    def test_bus_name(self):
        """Service has correct D-Bus bus name."""
        from services.axon_<name>.<name>_service import <Name>Service
        assert <Name>Service.BUS_NAME == "org.axonos.<Name>"

    def test_object_path(self):
        """Service has correct D-Bus object path."""
        from services.axon_<name>.<name>_service import <Name>Service
        assert <Name>Service.OBJECT_PATH == "/org/axonos/<Name>"

    # Add more tests for specific functionality
```

Run tests: `pytest tests/test_<name>.py -v`

### Phase 9: Register in chroot-setup.sh

Add to `build/config/chroot-setup.sh` in the service installation section:

```bash
# D-Bus session activation files
for activation in "${SERVICES_DIR}"/<name>/org.axonos.*.service; do
    [[ -f "${activation}" ]] || continue
    sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${activation}" \
        > "/usr/share/dbus-1/services/$(basename "${activation}")"
done
```

The existing loop should automatically pick up new services if they follow the naming convention.

### Phase 10: Verify

1. **Lint**: `ruff check services/axon-<name>/`
2. **Type check**: `mypy services/axon-<name>/ --ignore-missing-imports`
3. **Tests**: `pytest tests/test_<name>.py -v`
4. **Import test**: `python3 -c "from services.axon_<name>.<name>_service import <Name>Service"`

## Key Files

- `services/service_base.py` — Base class for D-Bus services (optional, use if boilerplate reduction is desired)
- `services/constants.py` — Shared constants (D-Bus names, paths, limits)
- `services/axon_logger.py` — Centralized logging
- `services/service_utils.py` — TTLCache, RateLimiter, decorators
- `build/config/chroot-setup.sh` — Service registration in chroot

## Common Patterns

- **Thread safety**: Use `threading.Lock` for shared state accessed from GLib main loop + background threads
- **SQLite**: Use `PRAGMA journal_mode=WAL` + `check_same_thread=False`
- **Lazy imports**: Use `try/except` for optional dependencies (e.g., `vosk`, `sqlite_vec`)
- **Logging**: Use `configure_app_logger(__name__)` — wraps RotatingFileHandler with fallback to console
