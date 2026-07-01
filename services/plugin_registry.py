"""Plugin registry for Axon OS services.

Discovers, validates, loads, and manages service plugins from a
directory of TOML manifests. Each manifest declares a service's
D-Bus name, entry point, dependencies, and systemd properties.

Manifest format (``manifest.toml``)::

    [service]
    name = "my-plugin"
    description = "What this service does"
    bus_name = "org.axonos.MyPlugin"
    object_path = "/org/axonos/MyPlugin"
    entry_point = "my_plugin_service.py"
    dependencies = ["org.axonos.Brain"]

    [systemd]
    description = "Axon My Plugin Service"
    after = ["axon-brain.service"]
    restart_sec = 3
"""

import importlib.util
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import dbus
from gi.repository import GLib

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


logger = configure_app_logger("plugin-registry")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

try:
    from constants import (
        DBUS_NAME_BRAIN,
        DBUS_NAME_CONTEXT,
        DBUS_NAME_GUI_AGENT,
        DBUS_NAME_SANDBOX,
        DBUS_NAME_SEARCH,
        DBUS_NAME_VOICE,
    )
except ImportError:
    # Fallback when constants.py is not on sys.path yet
    DBUS_NAME_BRAIN = "org.axonos.Brain"
    DBUS_NAME_CONTEXT = "org.axonos.Context"
    DBUS_NAME_SEARCH = "org.axonos.Search"
    DBUS_NAME_VOICE = "org.axonos.Voice"
    DBUS_NAME_GUI_AGENT = "org.axonos.GuiAgent"
    DBUS_NAME_SANDBOX = "org.axonos.Sandbox"


@dataclass
class ServiceManifest:
    """Parsed manifest for a single service plugin."""

    name: str
    description: str
    bus_name: str
    object_path: str
    entry_point: str
    manifest_path: Path
    dependencies: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    restart_sec: int = 3

    @classmethod
    def from_toml(cls, path: Path) -> "ServiceManifest":
        """Parse a manifest.toml file into a ServiceManifest."""
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        svc = data.get("service", {})
        sysd = data.get("systemd", {})

        for key in ("name", "bus_name", "object_path", "entry_point"):
            if key not in svc:
                raise ValueError(f"manifest {path} missing required key [service].{key}")

        return cls(
            name=svc["name"],
            description=svc.get("description", ""),
            bus_name=svc["bus_name"],
            object_path=svc["object_path"],
            entry_point=svc["entry_point"],
            manifest_path=path,
            dependencies=svc.get("dependencies", []),
            after=sysd.get("after", []),
            restart_sec=sysd.get("restart_sec", 3),
        )


@dataclass
class PluginInfo:
    """Runtime state of a loaded plugin."""

    manifest: ServiceManifest
    status: str = "discovered"  # discovered | loaded | running | error | stopped
    error: str = ""
    load_time: float = 0.0
    module: object = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Bus names that are part of core Axon (not plugins)
_CORE_BUS_NAMES = frozenset(
    {
        DBUS_NAME_BRAIN,
        DBUS_NAME_CONTEXT,
        DBUS_NAME_SEARCH,
        DBUS_NAME_VOICE,
        DBUS_NAME_GUI_AGENT,
        DBUS_NAME_SANDBOX,
    }
)


class ServiceRegistry:
    """Discovers and manages service plugins.

    Args:
        plugins_dir: Directory containing plugin subdirectories, each with
            a ``manifest.toml``.
        auto_load: If True, discover and validate on init.
    """

    def __init__(
        self,
        plugins_dir: Path | None = None,
        auto_load: bool = False,
    ) -> None:
        self.plugins_dir = plugins_dir or self._default_plugins_dir()
        self._plugins: dict[str, PluginInfo] = {}
        self._lock = threading.Lock()

        if auto_load:
            self.discover()

    @staticmethod
    def _default_plugins_dir() -> Path:
        """Return the default plugins directory (~/.local/share/axon/plugins)."""
        from constants import AXON_DIR

        return AXON_DIR / "plugins"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> list[ServiceManifest]:
        """Scan the plugins directory for manifest.toml files.

        Returns:
            List of discovered (and validated) manifests.
        """
        manifests: list[ServiceManifest] = []

        if not self.plugins_dir.is_dir():
            logger.info("Plugins directory %s does not exist; skipping.", self.plugins_dir)
            return manifests

        for entry in sorted(self.plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.toml"
            if not manifest_path.is_file():
                continue

            try:
                manifest = ServiceManifest.from_toml(manifest_path)
                self._validate_manifest(manifest)
                with self._lock:
                    self._plugins[manifest.name] = PluginInfo(manifest=manifest)
                manifests.append(manifest)
                logger.info("Discovered plugin: %s (%s)", manifest.name, manifest.bus_name)
            except Exception as exc:
                logger.warning("Skipping invalid plugin in %s: %s", entry, exc)

        return manifests

    def _validate_manifest(self, manifest: ServiceManifest) -> None:
        """Validate a manifest against the registry.

        Raises ValueError if the manifest is invalid or conflicts.
        """
        # Reject core bus names
        if manifest.bus_name in _CORE_BUS_NAMES:
            raise ValueError(f"Plugin '{manifest.name}' declares core bus name {manifest.bus_name}")

        # Reject duplicate names
        if manifest.name in self._plugins:
            raise ValueError(f"Duplicate plugin name: {manifest.name}")

        # Entry point must exist relative to the manifest directory
        entry = manifest.manifest_path.parent / manifest.entry_point
        if not entry.is_file():
            raise ValueError(f"Entry point {entry} not found for plugin '{manifest.name}'")

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, name: str) -> bool:
        """Load a single plugin by name.

        Validates dependencies, imports the entry point, and instantiates
        the service class (but does NOT start the GLib main loop — that
        is left to the caller or systemd).

        Returns True on success.
        """
        with self._lock:
            info = self._plugins.get(name)
        if info is None:
            logger.error("Plugin '%s' not found in registry.", name)
            return False

        if info.status == "running":
            logger.warning("Plugin '%s' is already running.", name)
            return True

        # Check dependencies
        for dep in info.manifest.dependencies:
            if not self._is_bus_available(dep):
                info.status = "error"
                info.error = f"Dependency {dep} not available"
                logger.error("Plugin '%s' dependency %s not available.", name, dep)
                return False

        # Import entry point module
        entry_path = info.manifest.manifest_path.parent / info.manifest.entry_point
        try:
            start = time.monotonic()
            spec = importlib.util.spec_from_file_location(f"axon_plugin_{name}", str(entry_path))
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create spec for {entry_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            info.module = module
            info.load_time = time.monotonic() - start
            info.status = "loaded"
            logger.info("Plugin '%s' loaded in %.1fms", name, info.load_time * 1000)
            return True
        except Exception as exc:
            info.status = "error"
            info.error = str(exc)
            logger.error("Failed to load plugin '%s': %s", name, exc)
            return False

    def load_all(self) -> dict[str, bool]:
        """Load all discovered plugins (respecting dependency order).

        Returns:
            Dict mapping plugin name to load success.
        """
        results: dict[str, bool] = {}

        # Topological sort by dependencies
        ordered = self._topo_sort()

        for name in ordered:
            results[name] = self.load(name)

        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, name: str) -> bool:
        """Start a loaded plugin in a background thread.

        The plugin's entry point module must define a ``create_service()``
        function that returns a ServiceBase subclass instance. The
        instance's main loop runs in a daemon thread.
        """
        with self._lock:
            info = self._plugins.get(name)
        if info is None:
            return False
        if info.status == "running":
            return True
        if info.status != "loaded":
            logger.error("Plugin '%s' not loaded (status=%s).", name, info.status)
            return False

        try:
            create_fn = getattr(info.module, "create_service", None)
            if create_fn is None:
                # Fall back: look for a ServiceBase subclass with main()
                create_fn = self._find_service_factory(info.module)
            if create_fn is None:
                raise AttributeError(
                    f"Plugin '{name}' has no create_service() or ServiceBase subclass"
                )

            service = create_fn()  # noqa: F841 — side effect: starts D-Bus service

            def _run():
                loop = GLib.MainLoop()
                try:
                    loop.run()
                except Exception:
                    loop.quit()

            thread = threading.Thread(target=_run, daemon=True, name=f"plugin-{name}")
            thread.start()

            info.status = "running"
            logger.info("Plugin '%s' started.", name)
            return True
        except Exception as exc:
            info.status = "error"
            info.error = str(exc)
            logger.error("Failed to start plugin '%s': %s", name, exc)
            return False

    def stop(self, name: str) -> bool:
        """Signal a running plugin to stop.

        The plugin must have a ``shutdown()`` callable or the main loop
        will be quit via SIGTERM-style approach.
        """
        with self._lock:
            info = self._plugins.get(name)
        if info is None or info.status != "running":
            return False

        try:
            shutdown_fn = getattr(info.module, "shutdown", None)
            if shutdown_fn:
                shutdown_fn()
            info.status = "stopped"
            logger.info("Plugin '%s' stopped.", name)
            return True
        except Exception as exc:
            logger.error("Error stopping plugin '%s': %s", name, exc)
            return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_plugins(self) -> list[dict]:
        """Return a list of all plugins with their status."""
        with self._lock:
            return [
                {
                    "name": info.manifest.name,
                    "description": info.manifest.description,
                    "bus_name": info.manifest.bus_name,
                    "status": info.status,
                    "error": info.error,
                    "dependencies": info.manifest.dependencies,
                }
                for info in self._plugins.values()
            ]

    def get_plugin(self, name: str) -> dict | None:
        """Return info for a single plugin, or None."""
        with self._lock:
            info = self._plugins.get(name)
        if info is None:
            return None
        return {
            "name": info.manifest.name,
            "description": info.manifest.description,
            "bus_name": info.manifest.bus_name,
            "object_path": info.manifest.object_path,
            "status": info.status,
            "error": info.error,
            "load_time_ms": round(info.load_time * 1000, 1),
            "dependencies": info.manifest.dependencies,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_bus_available(self, bus_name: str) -> bool:
        """Check if a D-Bus name has an owner on the session bus."""
        try:
            bus = dbus.SessionBus()
            return bool(bus.name_has_owner(bus_name))
        except Exception:
            return False

    def _topo_sort(self) -> list[str]:
        """Topological sort of plugins by dependency order."""
        with self._lock:
            names = list(self._plugins.keys())
            plugins_snapshot = dict(self._plugins)

        # Map bus names to plugin names for dependency resolution
        bus_to_name: dict[str, str] = {}
        for name in names:
            bus_to_name[plugins_snapshot[name].manifest.bus_name] = name

        # Build adjacency using plugin names
        deps: dict[str, list[str]] = {}
        for name in names:
            info = plugins_snapshot[name]
            deps[name] = [bus_to_name[d] for d in info.manifest.dependencies if d in bus_to_name]

        # Kahn's algorithm
        in_degree: dict[str, int] = dict.fromkeys(names, 0)
        for n in names:
            in_degree[n] += len(deps[n])

        queue = [n for n in names if in_degree[n] == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for n in names:
                if node in deps[n]:
                    in_degree[n] -= 1
                    if in_degree[n] == 0:
                        queue.append(n)

        # Append any remaining (circular deps) — log warning
        for n in names:
            if n not in result:
                logger.warning("Plugin '%s' has circular dependency; appending last.", n)
                result.append(n)

        return result

    @staticmethod
    def _find_service_factory(module: object) -> Callable | None:
        """Find a ServiceBase subclass in the module and return a factory."""
        from service_base import ServiceBase

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, ServiceBase) and attr is not ServiceBase:
                return attr
        return None
