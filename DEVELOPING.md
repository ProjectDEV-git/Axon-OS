# Developing Axon OS

## Setup

### Prerequisites
- Python 3.10+
- Git

### Install Dependencies

1. **Runtime dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   
   Note: On Ubuntu, some dependencies require system packages:
   ```bash
   sudo apt-get install python3-dbus python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-vte-2.91
   ```

2. **Development dependencies:**
   ```bash
   pip install -r requirements-dev.txt
   ```

### Pre-commit Hooks (Optional but Recommended)

Set up automatic code quality checks before each commit:

```bash
pre-commit install
pre-commit run --all-files
```

## Running Tests

Run the test suite:

```bash
pytest tests/ -v
```

Run tests with coverage:

```bash
pytest tests/ --cov=apps --cov=services --cov-report=term-missing
```

## Code Quality

### Linting with Ruff

Check for style and correctness issues:

```bash
ruff check apps/ services/ tests/ installer/
```

Auto-fix issues:

```bash
ruff check --fix apps/ services/ tests/ installer/
```

### Type Checking with mypy

Run type checks:

```bash
mypy apps/ services/ --ignore-missing-imports
```

### Format Code with Black

Format Python code:

```bash
black apps/ services/ tests/ installer/
```

## Project Structure

- **`apps/`** - Desktop applications (UI panels, terminal, file browser, etc.)
- **`services/`** - System services (brain, context engine)
- **`installer/`** - Installation and partitioning tools
- **`shell/`** - GNOME Shell extension
- **`theme/`** - GTK theme, icon theme, wallpapers
- **`tests/`** - Test suite
- **`.github/workflows/`** - CI/CD pipelines

## Development Container

For a consistent development environment, use VS Code's Dev Container:

```bash
# Open in VS Code Dev Container
# Ctrl+Shift+P → "Dev Containers: Reopen in Container"
```

This automatically:
- Installs all system dependencies
- Creates a Python virtual environment
- Installs dev dependencies
- Sets up pre-commit hooks
- Runs initial test suite

## Logging

Use the centralized logging utility for consistent log output:

```python
from apps.axon_logger import configure_app_logger

logger = configure_app_logger(__name__)
logger.info("App started")
logger.error("Something went wrong: %s", error_msg)
```

## Troubleshooting

### Tests fail on Windows
Many tests require Linux system bindings (dbus, GTK). Run tests on Ubuntu or in WSL2.

### Import errors with gi (PyGObject)
Ensure you've installed the system GTK bindings:
```bash
sudo apt-get install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

### mypy reports unresolved imports
Some D-Bus and GTK types may not have stubs. Use `--ignore-missing-imports` flag.

## Contributing

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make changes and run tests: `pytest tests/`
3. Lint and format: `ruff check --fix` and `black .`
4. Commit with clear message: `git commit -m "feat: description"`
5. Push and open a pull request

## Service Development

### D-Bus Service Template

New D-Bus services should follow this pattern:

```python
#!/usr/bin/env python3
"""Your service description."""

import sys
from pathlib import Path
from axon_logger import configure_app_logger

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

logger = configure_app_logger(__name__)

class YourService(dbus.service.Object):
    """D-Bus service for your feature."""
    
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        
        try:
            self.bus_name = dbus.service.BusName(
                'org.axonos.YourService',
                bus=self.session_bus
            )
        except dbus.exceptions.NameExistsException:
            logger.error("Service already running")
            sys.exit(1)
        
        dbus.service.Object.__init__(
            self,
            self.session_bus,
            '/org/axonos/YourService'
        )
        logger.info("Service registered")
    
    @dbus.service.method('org.axonos.YourService', in_signature='', out_signature='s')
    def GetStatus(self) -> str:
        """Return service status as JSON."""
        import json
        return json.dumps({"status": "ok"})
```

### Service Utilities

Use the service utilities for common patterns:

```python
from services.service_utils import TTLCache, RateLimiter, cached, rate_limited

# Caching
self.cache = TTLCache(ttl_seconds=300)  # 5 min cache

# Rate limiting
self.limiter = RateLimiter(rate=100, window_seconds=60)  # 100/min

# Use decorators
@cached(ttl_seconds=300)
def expensive_operation(self):
    return compute_result()

@rate_limited(rate=50, window_seconds=60)
@dbus.service.method(...)
def limited_method(self):
    pass
```

## Enhanced Code Quality Tools

### Pre-commit Hooks

Automatic code quality checks run before each commit:

```bash
# Install hooks
pre-commit install

# Run all hooks
pre-commit run --all-files

# Run specific hook
pre-commit run ruff --all-files
```

Configured hooks:
- **Black**: Code formatting
- **isort**: Import sorting
- **Ruff**: Fast linting with fixes
- **Mypy**: Type checking
- **Bandit**: Security scanning
- **YAML/JSON validation**: Config file syntax

### Type Checking (Strict Mode)

For new code, use strict type annotations:

```python
from typing import Optional, Dict, Any

def process_data(data: Dict[str, Any]) -> Optional[str]:
    """Process and return data.
    
    Args:
        data: Input dictionary with structured data.
    
    Returns:
        Processed string or None if empty.
    """
    if not data:
        return None
    return str(data)
```

Run mypy with strict settings:

```bash
mypy apps/ services/ --disallow-untyped-defs --check-untyped-defs
```

### Testing with Enhanced Coverage

Run tests with coverage reporting:

```bash
# Generate coverage report
pytest tests/ --cov=apps --cov=services --cov-report=term-missing --cov-report=html

# View HTML report
open htmlcov/index.html
```

Target: 70%+ coverage for critical paths (D-Bus services, conversation storage)

## Documentation

Comprehensive guides available:

- [Architecture Decisions (ADRs)](docs/architecture-decisions.md) - Why Axon OS uses D-Bus, Python, etc.
- [Security Hardening](docs/security.md) - Input validation, D-Bus policy, isolation
- [Troubleshooting Guide](docs/troubleshooting.md) - Common issues and solutions
- [Building Guide](docs/building.md) - How to build ISO images

## Performance Profiling

Profile service performance:

```bash
# CPU profiling
python3 -m cProfile -s cumtime services/axon-brain/brain_service.py | head -20

# Memory profiling
python3 -m memory_profiler services/axon-brain/brain_service.py

# Monitor with systemd
systemd-run --scope -u test-scope --MemoryLimit=512M python3 service.py
```

## CI Pipeline

The project uses GitHub Actions (`.github/workflows/ci.yml`) to automatically:
- Run tests on Python 3.10, 3.11, 3.12
- Check code with ruff
- Perform type checks with mypy
- Generate coverage reports

See the CI configuration in `.github/workflows/` for details.
