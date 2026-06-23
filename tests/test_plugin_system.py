"""Tests for the Axon OS plugin system (ServiceBase, ServiceRegistry, deploy)."""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest.py already inserts ROOT into sys.path and registers 'services' as a
# namespace package, so we can import directly from services.*


# ---------------------------------------------------------------------------
# ServiceManifest tests
# ---------------------------------------------------------------------------


class TestServiceManifest:
    """Tests for ServiceManifest.from_toml."""

    def test_parse_valid_manifest(self, tmp_path):
        from services.plugin_registry import ServiceManifest

        m = tmp_path / "manifest.toml"
        m.write_text(textwrap.dedent("""\
            [service]
            name = "test-svc"
            description = "A test"
            bus_name = "org.axonos.Test"
            object_path = "/org/axonos/Test"
            entry_point = "svc.py"
            dependencies = ["org.axonos.Brain"]

            [systemd]
            description = "Test Service"
            after = ["axon-brain.service"]
            restart_sec = 5
        """))

        manifest = ServiceManifest.from_toml(m)
        assert manifest.name == "test-svc"
        assert manifest.bus_name == "org.axonos.Test"
        assert manifest.object_path == "/org/axonos/Test"
        assert manifest.entry_point == "svc.py"
        assert manifest.dependencies == ["org.axonos.Brain"]
        assert manifest.restart_sec == 5
        assert manifest.after == ["axon-brain.service"]

    def test_minimal_manifest(self, tmp_path):
        from services.plugin_registry import ServiceManifest

        m = tmp_path / "manifest.toml"
        m.write_text(textwrap.dedent("""\
            [service]
            name = "mini"
            bus_name = "org.axonos.Mini"
            object_path = "/org/axonos/Mini"
            entry_point = "mini.py"
        """))

        manifest = ServiceManifest.from_toml(m)
        assert manifest.name == "mini"
        assert manifest.dependencies == []
        assert manifest.restart_sec == 3  # default

    def test_missing_required_key(self, tmp_path):
        from services.plugin_registry import ServiceManifest

        m = tmp_path / "manifest.toml"
        m.write_text(textwrap.dedent("""\
            [service]
            name = "bad"
            bus_name = "org.axonos.Bad"
        """))

        with pytest.raises(ValueError, match="missing required key"):
            ServiceManifest.from_toml(m)


# ---------------------------------------------------------------------------
# ServiceRegistry tests
# ---------------------------------------------------------------------------


class TestServiceRegistry:
    """Tests for ServiceRegistry discovery and validation."""

    def _make_plugin(self, tmp_path, name, bus_name, entry="svc.py"):
        """Helper to create a plugin directory with manifest and entry point."""
        d = tmp_path / name
        d.mkdir()
        (d / "manifest.toml").write_text(textwrap.dedent(f"""\
            [service]
            name = "{name}"
            bus_name = "{bus_name}"
            object_path = "/org/axonos/plugins/{name.title()}"
            entry_point = "{entry}"
        """))
        (d / entry).write_text("# placeholder\n")
        return d

    def test_discover_finds_plugins(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        self._make_plugin(tmp_path, "alpha", "org.axonos.plugins.Alpha")
        self._make_plugin(tmp_path, "beta", "org.axonos.plugins.Beta")

        registry = ServiceRegistry(plugins_dir=tmp_path)
        manifests = registry.discover()

        names = {m.name for m in manifests}
        assert names == {"alpha", "beta"}

    def test_discover_skips_missing_entry_point(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        d = tmp_path / "broken"
        d.mkdir()
        (d / "manifest.toml").write_text(textwrap.dedent("""\
            [service]
            name = "broken"
            bus_name = "org.axonos.plugins.Broken"
            object_path = "/org/axonos/plugins/Broken"
            entry_point = "nonexistent.py"
        """))

        registry = ServiceRegistry(plugins_dir=tmp_path)
        manifests = registry.discover()
        assert manifests == []

    def test_discover_rejects_core_bus_name(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        d = tmp_path / "evil"
        d.mkdir()
        (d / "manifest.toml").write_text(textwrap.dedent("""\
            [service]
            name = "evil"
            bus_name = "org.axonos.Brain"
            object_path = "/org/axonos/Brain"
            entry_point = "evil.py"
        """))
        (d / "evil.py").write_text("# placeholder\n")

        registry = ServiceRegistry(plugins_dir=tmp_path)
        manifests = registry.discover()
        assert manifests == []

    def test_discover_empty_dir(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        empty = tmp_path / "empty"
        empty.mkdir()

        registry = ServiceRegistry(plugins_dir=empty)
        assert registry.discover() == []

    def test_discover_nonexistent_dir(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        registry = ServiceRegistry(plugins_dir=tmp_path / "nope")
        assert registry.discover() == []

    def test_list_plugins_after_discover(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        self._make_plugin(tmp_path, "gamma", "org.axonos.plugins.Gamma")

        registry = ServiceRegistry(plugins_dir=tmp_path)
        registry.discover()

        plugins = registry.list_plugins()
        assert len(plugins) == 1
        assert plugins[0]["name"] == "gamma"
        assert plugins[0]["status"] == "discovered"

    def test_get_plugin_returns_none_for_unknown(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        registry = ServiceRegistry(plugins_dir=tmp_path)
        assert registry.get_plugin("nope") is None

    def test_topo_sort_respects_dependencies(self, tmp_path):
        from services.plugin_registry import ServiceRegistry

        # alpha depends on beta
        d = tmp_path / "alpha"
        d.mkdir()
        (d / "manifest.toml").write_text(textwrap.dedent("""\
            [service]
            name = "alpha"
            bus_name = "org.axonos.plugins.Alpha"
            object_path = "/org/axonos/plugins/Alpha"
            entry_point = "svc.py"
            dependencies = ["org.axonos.plugins.Beta"]
        """))
        (d / "svc.py").write_text("# placeholder\n")

        d2 = tmp_path / "beta"
        d2.mkdir()
        (d2 / "manifest.toml").write_text(textwrap.dedent("""\
            [service]
            name = "beta"
            bus_name = "org.axonos.plugins.Beta"
            object_path = "/org/axonos/plugins/Beta"
            entry_point = "svc.py"
        """))
        (d2 / "svc.py").write_text("# placeholder\n")

        registry = ServiceRegistry(plugins_dir=tmp_path)
        registry.discover()

        order = registry._topo_sort()
        assert order.index("beta") < order.index("alpha")


# ---------------------------------------------------------------------------
# ServiceBase tests
# ---------------------------------------------------------------------------


class TestServiceBase:
    """Tests for ServiceBase without a real D-Bus session bus."""

    @patch("dbus.service.BusName")
    @patch("dbus.service.Object.__init__")
    @patch("dbus.mainloop.glib.DBusGMainLoop")
    @patch("dbus.SessionBus")
    def test_subclass_init(self, mock_bus, mock_loop, mock_obj_init, mock_busname):
        """ServiceBase subclass should call D-Bus boilerplate and _setup."""
        from services.service_base import ServiceBase

        mock_bus.return_value = MagicMock()

        class TestSvc(ServiceBase):
            BUS_NAME = "org.axonos.Test"
            OBJECT_PATH = "/org/axonos/Test"
            SERVICE_NAME = "test-svc"

            def _setup(self):
                self.ready = True

        svc = TestSvc()

        mock_loop.assert_called_once_with(set_as_default=True)
        mock_busname.assert_called_once_with("org.axonos.Test", bus=mock_bus.return_value)
        mock_obj_init.assert_called_once()
        assert svc.ready is True

    @patch("dbus.service.BusName")
    @patch("dbus.service.Object.__init__")
    @patch("dbus.mainloop.glib.DBusGMainLoop")
    @patch("dbus.SessionBus")
    def test_health_tracking(self, mock_bus, mock_loop, mock_obj_init, mock_busname):
        from services.service_base import ServiceBase

        mock_bus.return_value = MagicMock()

        class TestSvc(ServiceBase):
            BUS_NAME = "org.axonos.Test"
            OBJECT_PATH = "/org/axonos/Test"
            SERVICE_NAME = "test"

            def _setup(self):
                pass

        svc = TestSvc()

        assert svc.is_healthy() is True
        svc.set_healthy(False)
        assert svc.is_healthy() is False
        svc.set_healthy(True)
        assert svc.is_healthy() is True

    @patch("dbus.service.BusName")
    @patch("dbus.service.Object.__init__")
    @patch("dbus.mainloop.glib.DBusGMainLoop")
    @patch("dbus.SessionBus")
    def test_uptime(self, mock_bus, mock_loop, mock_obj_init, mock_busname):
        from services.service_base import ServiceBase

        mock_bus.return_value = MagicMock()

        class TestSvc(ServiceBase):
            BUS_NAME = "org.axonos.Test"
            OBJECT_PATH = "/org/axonos/Test"
            SERVICE_NAME = "test"

            def _setup(self):
                pass

        svc = TestSvc()
        assert svc.uptime >= 0

    @patch("dbus.service.BusName", side_effect=SystemExit(1))
    @patch("dbus.mainloop.glib.DBusGMainLoop")
    @patch("dbus.SessionBus")
    def test_duplicate_name_exits(self, mock_bus, mock_loop, mock_busname):
        from services.service_base import ServiceBase

        mock_bus.return_value = MagicMock()

        class TestSvc(ServiceBase):
            BUS_NAME = "org.axonos.Test"
            OBJECT_PATH = "/org/axonos/Test"
            SERVICE_NAME = "test"

            def _setup(self):
                pass

        with pytest.raises(SystemExit):
            TestSvc()


# ---------------------------------------------------------------------------
# Plugin deploy tests
# ---------------------------------------------------------------------------


class TestPluginDeploy:
    """Tests for plugin deployment artifact generation."""

    def _sample_manifest(self):
        return {
            "service": {
                "name": "my-plugin",
                "description": "My plugin",
                "bus_name": "org.axonos.plugins.MyPlugin",
                "object_path": "/org/axonos/plugins/MyPlugin",
                "entry_point": "my_plugin.py",
                "dependencies": ["org.axonos.Brain"],
            },
            "systemd": {
                "description": "Axon My Plugin",
                "after": ["axon-brain.service"],
                "restart_sec": 5,
            },
        }

    def test_generate_systemd_unit(self):
        from services.plugin_deploy import generate_systemd_unit

        manifest = self._sample_manifest()
        install_dir = Path("/opt/axon/plugins/my-plugin")

        unit = generate_systemd_unit(manifest, install_dir)

        assert "Type=dbus" in unit
        assert "BusName=org.axonos.plugins.MyPlugin" in unit
        assert f"ExecStart=/usr/bin/python3 {install_dir / 'my_plugin.py'}" in unit
        assert "RestartSec=5" in unit
        assert "After=axon-brain.service" in unit
        assert "Requires=dbus.socket" in unit

    def test_generate_dbus_service(self):
        from services.plugin_deploy import generate_dbus_service

        manifest = self._sample_manifest()
        install_dir = Path("/opt/axon/plugins/my-plugin")

        svc = generate_dbus_service(manifest, install_dir)

        assert "Name=org.axonos.plugins.MyPlugin" in svc
        assert f"Exec=/usr/bin/python3 {install_dir / 'my_plugin.py'}" in svc

    def test_generate_dbus_policy(self):
        from services.plugin_deploy import generate_dbus_policy

        manifest = self._sample_manifest()
        policy = generate_dbus_policy(manifest)

        assert '<allow own="org.axonos.plugins.MyPlugin"/>' in policy
        assert '<allow send_destination="org.axonos.plugins.MyPlugin"/>' in policy
        assert '<deny eavesdrop="true"/>' in policy

    def test_generate_dbus_policy_custom_user(self):
        from services.plugin_deploy import generate_dbus_policy

        manifest = self._sample_manifest()
        policy = generate_dbus_policy(manifest, user="myuser")

        assert '<policy user="myuser">' in policy
