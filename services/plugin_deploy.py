"""Generate deployment artifacts for Axon OS service plugins.

Creates systemd user units, D-Bus session service files, and D-Bus
policy configs from plugin manifests. Run once at install time or
when a plugin is added/updated.

Usage::

    python3 plugin_deploy.py [--install] [--remove] <plugin_dir>

Without flags, generates artifacts to stdout for review.
With ``--install``, writes them to the standard system locations.
With ``--remove``, removes installed artifacts for the plugin.
"""

import argparse
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def generate_systemd_unit(manifest: dict, install_dir: Path) -> str:
    """Generate a systemd user unit for a plugin."""
    svc = manifest["service"]
    sysd = manifest.get("systemd", {})
    bus_name = svc["bus_name"]
    name = svc["name"]
    desc = sysd.get("description", f"Axon {name} Plugin")
    restart_sec = sysd.get("restart_sec", 3)
    after_deps = sysd.get("after", [])

    lines = [
        "[Unit]",
        f"Description={desc}",
        "After=dbus.socket",
        "Requires=dbus.socket",
    ]
    for dep in after_deps:
        lines.append(f"After={dep}")

    entry = install_dir / svc["entry_point"]
    lines.extend(
        [
            "",
            "[Service]",
            "Type=dbus",
            f"BusName={bus_name}",
            f"ExecStart=/usr/bin/python3 {entry}",
            "Restart=on-failure",
            f"RestartSec={restart_sec}",
            "",
            "[Install]",
            "WantedBy=default.target",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_dbus_service(manifest: dict, install_dir: Path) -> str:
    """Generate a D-Bus session service activation file."""
    svc = manifest["service"]
    entry = install_dir / svc["entry_point"]
    return (
        "[D-BUS Service]\n"
        f"Name={svc['bus_name']}\n"
        f"Exec=/usr/bin/python3 {entry}\n"
    )


def generate_dbus_policy(manifest: dict, user: str = "${user}") -> str:
    """Generate a D-Bus session policy XML file."""
    svc = manifest["service"]
    bus_name = svc["bus_name"]
    return f"""\
<busconfig>
  <policy user="{user}">
    <allow own="{bus_name}"/>
  </policy>
  <policy context="default">
    <allow send_destination="{bus_name}"/>
    <allow receive_sender="{bus_name}"/>
    <deny eavesdrop="true"/>
  </policy>
</busconfig>
"""


def install_plugin(manifest_path: Path) -> None:
    """Install deployment artifacts for a plugin manifest."""
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    name = manifest["service"]["name"]
    bus_name = manifest["service"]["bus_name"]
    plugin_dir = manifest_path.parent

    # Paths
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    dbus_services_dir = Path.home() / ".local" / "share" / "dbus-1" / "services"
    dbus_policy_dir = Path("/usr/share/dbus-1/session.d")
    axon_plugin_dir = Path.home() / ".local" / "share" / "axon" / "plugins" / name

    # Create dirs
    systemd_dir.mkdir(parents=True, exist_ok=True)
    dbus_services_dir.mkdir(parents=True, exist_ok=True)
    axon_plugin_dir.mkdir(parents=True, exist_ok=True)

    # Copy plugin to install location
    import shutil

    if axon_plugin_dir.exists():
        shutil.rmtree(axon_plugin_dir)
    shutil.copytree(plugin_dir, axon_plugin_dir)

    # Generate and write systemd unit
    unit_name = f"axon-plugin-{name}.service"
    unit_content = generate_systemd_unit(manifest, axon_plugin_dir)
    (systemd_dir / unit_name).write_text(unit_content, encoding="utf-8")
    print(f"  systemd unit: {systemd_dir / unit_name}")  # noqa: T201

    # Generate and write D-Bus service file
    safe_bus = bus_name + ".service"
    dbus_svc_content = generate_dbus_service(manifest, axon_plugin_dir)
    (dbus_services_dir / safe_bus).write_text(dbus_svc_content, encoding="utf-8")
    print(f"  D-Bus service: {dbus_services_dir / safe_bus}")  # noqa: T201

    # Generate D-Bus policy (requires sudo)
    policy_name = bus_name + ".conf"
    policy_content = generate_dbus_policy(manifest)
    print(f"  D-Bus policy (requires sudo): {dbus_policy_dir / policy_name}")  # noqa: T201
    print(f"    sudo tee {dbus_policy_dir / policy_name} <<'EOF'")  # noqa: T201
    print(policy_content, end="")  # noqa: T201
    print("EOF")  # noqa: T201

    print(f"\nPlugin '{name}' installed. Enable with:")  # noqa: T201
    print(f"  systemctl --user enable {unit_name}")  # noqa: T201
    print(f"  systemctl --user start {unit_name}")  # noqa: T201


def remove_plugin(manifest_path: Path) -> None:
    """Remove deployment artifacts for a plugin manifest."""
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    name = manifest["service"]["name"]
    bus_name = manifest["service"]["bus_name"]

    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    dbus_services_dir = Path.home() / ".local" / "share" / "dbus-1" / "services"
    axon_plugin_dir = Path.home() / ".local" / "share" / "axon" / "plugins" / name

    unit_name = f"axon-plugin-{name}.service"
    safe_bus = bus_name + ".service"

    for p in [
        systemd_dir / unit_name,
        dbus_services_dir / safe_bus,
    ]:
        if p.exists():
            p.unlink()
            print(f"  Removed: {p}")  # noqa: T201

    if axon_plugin_dir.exists():
        import shutil

        shutil.rmtree(axon_plugin_dir)
        print(f"  Removed: {axon_plugin_dir}")  # noqa: T201

    print(f"\nPlugin '{name}' removed. Run: systemctl --user daemon-reload")  # noqa: T201


def main() -> None:
    parser = argparse.ArgumentParser(description="Axon plugin deployment tool")
    parser.add_argument("plugin_dir", type=Path, help="Path to plugin directory")
    parser.add_argument("--install", action="store_true", help="Install artifacts")
    parser.add_argument("--remove", action="store_true", help="Remove artifacts")
    args = parser.parse_args()

    manifest_path = args.plugin_dir / "manifest.toml"
    if not manifest_path.is_file():
        print(f"Error: No manifest.toml found in {args.plugin_dir}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    if args.remove:
        remove_plugin(manifest_path)
    elif args.install:
        install_plugin(manifest_path)
    else:
        # Preview mode
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        print("=== systemd unit ===")  # noqa: T201
        print(generate_systemd_unit(manifest, args.plugin_dir))  # noqa: T201
        print("=== D-Bus service ===")  # noqa: T201
        print(generate_dbus_service(manifest, args.plugin_dir))  # noqa: T201
        print("=== D-Bus policy ===")  # noqa: T201
        print(generate_dbus_policy(manifest))  # noqa: T201


if __name__ == "__main__":
    main()
