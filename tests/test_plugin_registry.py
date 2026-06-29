"""Tests for plugin_registry — manifest parsing, validation, and PluginInfo."""


import pytest

from services.plugin_registry import (
    _CORE_BUS_NAMES,
    PluginInfo,
    ServiceManifest,
    ServiceRegistry,
)


@pytest.fixture
def plugin_dir(tmp_path):
    """Create a temporary plugin directory with a valid manifest."""
    plugin = tmp_path / "test-plugin"
    plugin.mkdir()
    manifest = plugin / "manifest.toml"
    manifest.write_text("""
[service]
name = "test-plugin"
description = "A test plugin"
bus_name = "org.axonos.plugins.Test"
object_path = "/org/axonos/plugins/Test"
entry_point = "test_service.py"
dependencies = []

[systemd]
description = "Axon Test Plugin Service"
after = ["axon-brain.service"]
restart_sec = 5
""")
    entry = plugin / "test_service.py"
    entry.write_text("# plugin entry point\nprint('hello')\n")
    return tmp_path


class TestServiceManifest:
    def test_from_toml_valid(self, plugin_dir):
        manifest_path = plugin_dir / "test-plugin" / "manifest.toml"
        m = ServiceManifest.from_toml(manifest_path)
        assert m.name == "test-plugin"
        assert m.bus_name == "org.axonos.plugins.Test"
        assert m.object_path == "/org/axonos/plugins/Test"
        assert m.entry_point == "test_service.py"
        assert m.restart_sec == 5

    def test_from_toml_missing_key(self, tmp_path):
        plugin = tmp_path / "bad-plugin"
        plugin.mkdir()
        manifest = plugin / "manifest.toml"
        manifest.write_text("""
[service]
name = "bad-plugin"
""")
        with pytest.raises(ValueError, match="missing required key"):
            ServiceManifest.from_toml(manifest)

    def test_from_toml_defaults(self, tmp_path):
        plugin = tmp_path / "minimal"
        plugin.mkdir()
        manifest = plugin / "manifest.toml"
        manifest.write_text("""
[service]
name = "minimal"
bus_name = "org.axonos.Minimal"
object_path = "/org/axonos/Minimal"
entry_point = "main.py"
""")
        entry = plugin / "main.py"
        entry.write_text("")
        m = ServiceManifest.from_toml(manifest)
        assert m.dependencies == []
        assert m.after == []
        assert m.restart_sec == 3


class TestServiceRegistry:
    def test_discover_valid_plugin(self, plugin_dir):
        registry = ServiceRegistry(plugins_dir=plugin_dir)
        manifests = registry.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "test-plugin"

    def test_discover_nonexistent_dir(self, tmp_path):
        registry = ServiceRegistry(plugins_dir=tmp_path / "nonexistent")
        manifests = registry.discover()
        assert manifests == []

    def test_discover_skips_non_dirs(self, plugin_dir):
        (plugin_dir / "not-a-dir.txt").write_text("ignore me")
        registry = ServiceRegistry(plugins_dir=plugin_dir)
        manifests = registry.discover()
        assert len(manifests) == 1

    def test_discover_skips_dirs_without_manifest(self, plugin_dir):
        (plugin_dir / "empty-plugin").mkdir()
        registry = ServiceRegistry(plugins_dir=plugin_dir)
        manifests = registry.discover()
        assert len(manifests) == 1

    def test_validate_rejects_core_bus_name(self, tmp_path):
        plugin = tmp_path / "core-plugin"
        plugin.mkdir()
        manifest = plugin / "manifest.toml"
        manifest.write_text("""
[service]
name = "core-plugin"
bus_name = "org.axonos.Brain"
object_path = "/org/axonos/Brain"
entry_point = "main.py"
""")
        (plugin / "main.py").write_text("")
        registry = ServiceRegistry(plugins_dir=tmp_path)
        # discover() catches ValueError and skips the plugin
        manifests = registry.discover()
        assert manifests == []

    def test_validate_rejects_missing_entry_point(self, tmp_path):
        plugin = tmp_path / "no-entry"
        plugin.mkdir()
        manifest = plugin / "manifest.toml"
        manifest.write_text("""
[service]
name = "no-entry"
bus_name = "org.axonos.plugins.NoEntry"
object_path = "/org/axonos/plugins/NoEntry"
entry_point = "nonexistent.py"
""")
        registry = ServiceRegistry(plugins_dir=tmp_path)
        manifests = registry.discover()
        assert manifests == []  # skipped due to validation error

    def test_get_plugin_info(self, plugin_dir):
        registry = ServiceRegistry(plugins_dir=plugin_dir)
        registry.discover()
        info = registry.get_plugin("test-plugin")
        assert info is not None
        assert info["status"] == "discovered"
        assert info["name"] == "test-plugin"

    def test_get_nonexistent_plugin(self, plugin_dir):
        registry = ServiceRegistry(plugins_dir=plugin_dir)
        assert registry.get_plugin("nonexistent") is None

    def test_list_plugins(self, plugin_dir):
        registry = ServiceRegistry(plugins_dir=plugin_dir)
        registry.discover()
        plugins = registry.list_plugins()
        assert len(plugins) == 1

    def test_load_nonexistent_plugin(self, plugin_dir):
        registry = ServiceRegistry(plugins_dir=plugin_dir)
        assert registry.load("nonexistent") is False


class TestPluginInfo:
    def test_default_status(self, plugin_dir):
        manifest_path = plugin_dir / "test-plugin" / "manifest.toml"
        manifest = ServiceManifest.from_toml(manifest_path)
        info = PluginInfo(manifest=manifest)
        assert info.status == "discovered"
        assert info.error == ""
        assert info.load_time == 0.0


class TestCoreBusNames:
    def test_contains_expected_names(self):
        assert "org.axonos.Brain" in _CORE_BUS_NAMES
        assert "org.axonos.Context" in _CORE_BUS_NAMES
        assert "org.axonos.Search" in _CORE_BUS_NAMES
