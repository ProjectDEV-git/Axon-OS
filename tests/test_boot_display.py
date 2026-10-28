"""Regression tests for the boot splash, VM display setup and small-screen UI.

Background: in VirtualBox a normal (``quiet splash``) boot looked dead while
"safe graphics" worked, and the live session was unusable at the low
resolutions safe graphics gives. Causes covered here:

- ``axon.script`` used hex literals, which Plymouth's parser rejects; the
  whole script was discarded, so every splash boot showed a black screen.
- ``chroot-setup.sh`` forced an Xorg ``vboxvideo`` driver that Ubuntu 24.04
  does not ship (pushing VMSVGA off the ``vmware`` driver) and ran
  ``VBoxClient`` from a root system unit.
- The installer wizard needed 895x773 px and the Welcome app ~690 px of
  height, so their buttons were cut off at 800x600 / 1024x768.
"""

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPLASH_DIR = ROOT / "plymouth" / "axon-splash"
SPLASH_SCRIPT = SPLASH_DIR / "axon.script"
CHROOT_SETUP = ROOT / "build" / "config" / "chroot-setup.sh"
VM_GRAPHICS_ENV = ROOT / "build" / "config" / "axon-vm-graphics-env"

# Smallest screen we support: 800x600 minus the 48 px Axon taskbar.
SMALL_WORK_AREA = (800, 600 - 48)


def _script_code() -> str:
    """The splash script with // comments removed."""
    return re.sub(r"//[^\n]*", "", SPLASH_SCRIPT.read_text())


# ---------------------------------------------------------------------------
# Plymouth splash
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_splash_script_has_no_hex_literals():
    # Plymouth's script parser only understands decimal numbers; one hex
    # literal makes it reject the entire script (black screen at boot).
    assert re.search(r"\b0[xX][0-9a-fA-F]+", _script_code()) is None


@pytest.mark.unit
def test_splash_script_only_calls_existing_image_constructors():
    # Plymouth's Image API has no CreateFilledRectangle & co. — only
    # Image("file.png") and Image.Text(...) create images.
    static_calls = set(re.findall(r"\bImage\.(\w+)\s*\(", _script_code()))
    assert static_calls <= {"Text"}


@pytest.mark.unit
def test_splash_images_exist_and_are_installed():
    images = re.findall(r'\bImage\(\s*"([^"]+)"\s*\)', _script_code())
    assert images, "splash script loads no images"
    setup = CHROOT_SETUP.read_text()
    for name in images:
        assert (SPLASH_DIR / name).is_file(), name
        assert f"plymouth/axon-splash/{name}" in setup, f"{name} not copied into the image"


# ---------------------------------------------------------------------------
# VM display integration (chroot-setup.sh)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_vm_xorg_driver_is_forced():
    setup = CHROOT_SETUP.read_text()
    # Ubuntu 24.04 has no vboxvideo_drv.so; Xorg autodetection picks the
    # right driver (vmware for VMSVGA, modesetting for VBoxVGA).
    assert re.search(r'Driver\s+"vboxvideo"', setup) is None
    # VBoxClient belongs in the X session (98vboxadd-xclient), not in
    # hand-rolled launchers; "vboxservice" is not a unit on Ubuntu.
    assert "VBoxClient --" not in setup
    assert not re.search(r"systemctl enable[^\n]*\b(vboxservice|axon-vm-guest)\b", setup)


@pytest.mark.unit
def test_stale_vm_display_hacks_are_removed_from_reused_chroots():
    # build.sh reuses the chroot, so files from older builds must be deleted.
    setup = CHROOT_SETUP.read_text()
    for path in (
        "/etc/systemd/system/axon-vbox-xorg-setup.service",
        "/etc/systemd/system/sysinit.target.wants/axon-vbox-xorg-setup.service",
        "/etc/systemd/system/axon-vm-guest.service",
        "/etc/systemd/system/graphical.target.wants/axon-vm-guest.service",
        "/etc/xdg/autostart/axon-vm-guest.desktop",
        "/etc/X11/xorg.conf.d/10-vboxvideo.conf",
    ):
        assert re.search(r"rm -f[^\n]*(\\\n[^\n]*)*" + re.escape(path), setup), path


@pytest.mark.unit
def test_vm_graphics_generator_is_installed():
    setup = CHROOT_SETUP.read_text()
    assert "build/config/axon-vm-graphics-env" in setup
    assert "/usr/lib/systemd/user-environment-generators/" in setup


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize(
    ("virt", "opt_out", "expected"),
    [
        ("oracle", False, "LIBGL_ALWAYS_SOFTWARE=1"),
        ("oracle", True, ""),
        ("vmware", False, ""),
        ("kvm", False, ""),
        ("none", False, ""),
    ],
)
def test_vm_graphics_generator_output(tmp_path, virt, opt_out, expected):
    fake_virt = tmp_path / "systemd-detect-virt"
    fake_virt.write_text(f"#!/bin/sh\necho {virt}\n")
    fake_virt.chmod(0o755)
    opt_out_file = tmp_path / "vbox-3d"
    if opt_out:
        opt_out_file.touch()

    script = VM_GRAPHICS_ENV.read_text()
    assert "/usr/bin/systemd-detect-virt" in script
    assert "/etc/axon/vbox-3d" in script
    script = script.replace("/usr/bin/systemd-detect-virt", str(fake_virt))
    script = script.replace("/etc/axon/vbox-3d", str(opt_out_file))
    generator = tmp_path / "generator"
    generator.write_text(script)

    result = subprocess.run(["sh", str(generator)], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# GNOME defaults (90_axon-os.gschema.override from chroot-setup.sh)
# ---------------------------------------------------------------------------

AXON_OVERRIDE_PATH = "/usr/share/glib-2.0/schemas/90_axon-os.gschema.override"
# Desktop names GNOME sessions put in XDG_CURRENT_DESKTOP: "GNOME" for the gnome session,
# "ubuntu:GNOME" for Ubuntu's session. Desktop-specific groups win over plain ones for them.
SESSION_DESKTOPS = ("GNOME", "ubuntu")


def _generate_override(tmp_path: Path) -> Path:
    """Run the override-writing block of chroot-setup.sh into tmp_path."""
    setup = CHROOT_SETUP.read_text()
    match = re.search(
        r'^log "Applying GNOME defaults\.\.\."\n.*?(?=^glib-compile-schemas )',
        setup,
        re.S | re.M,
    )
    assert match, "GNOME defaults block not found in chroot-setup.sh"
    block = match.group(0)
    assert f"AXON_GSCHEMA_OVERRIDE={AXON_OVERRIDE_PATH}" in block
    out = tmp_path / "90_axon-os.gschema.override"
    script = tmp_path / "gnome-defaults.sh"
    script.write_text(
        "log() { :; }\n"
        "GTK_THEME_NAME=axon-gtk ICON_THEME_NAME=Papirus-Dark SHELL_THEME_NAME=\n"
        "USER_THEME_EXT=user-theme@gnome-shell-extensions.gcampax.github.com\n"
        "BROWSER_DESKTOP=brave-browser.desktop\n" + block.replace(AXON_OVERRIDE_PATH, str(out))
    )
    subprocess.run(["bash", str(script)], check=True, capture_output=True, text=True)
    return out


def _parse_override(path: Path) -> dict[str, dict[str, str]]:
    """Group name -> {key: value text} for a gschema override file."""
    groups: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = groups.setdefault(line[1:-1], {})
        else:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    return groups


def _plain_groups(groups: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Groups without a desktop suffix or relocatable path."""
    return {name: keys for name, keys in groups.items() if ":" not in name}


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_gnome_override_sets_axon_values_for_session_desktops(tmp_path):
    # GLib picks [schema:<desktop>] over [schema] whenever <desktop> is in
    # XDG_CURRENT_DESKTOP, whatever the file number: gnome-shell-common's
    # [org.gnome.mutter:GNOME] turned dynamic workspaces back on, and
    # ubuntu-settings' [*:ubuntu] groups set Yaru and Ubuntu Sans.
    groups = _parse_override(_generate_override(tmp_path))
    plain = _plain_groups(groups)
    assert plain["org.gnome.desktop.interface"]["gtk-theme"] == "'axon-gtk'"
    mirrored = {name for name in plain if not name.startswith("org.gnome.settings-daemon.")}
    for desktop in SESSION_DESKTOPS:
        for name in mirrored:
            assert groups.get(f"{name}:{desktop}") == plain[name], f"{name}:{desktop}"

    ubuntu_interface = groups["org.gnome.desktop.interface:ubuntu"]
    assert ubuntu_interface["gtk-theme"] == "'axon-gtk'"
    assert ubuntu_interface["icon-theme"] == "'Papirus-Dark'"
    assert ubuntu_interface["font-name"] == "'Inter 11'"
    assert groups["org.gnome.mutter:GNOME"]["dynamic-workspaces"] == "false"
    assert groups["org.gnome.shell:ubuntu"]["favorite-apps"].startswith("['axon-welcome.desktop'")


def _gvariant_type(value: str) -> str:
    if value.startswith("'"):
        return "s"
    if value.startswith("["):
        return "as"
    if value in ("true", "false"):
        return "b"
    return "d" if "." in value else "i"


@pytest.mark.integration
@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("bash", "glib-compile-schemas", "gsettings")),
    reason="needs bash and GLib's schema tools",
)
@pytest.mark.parametrize("current_desktop", ["GNOME", "ubuntu:GNOME"])
def test_gnome_override_wins_over_desktop_specific_defaults(tmp_path, current_desktop):
    # Same check through GLib itself: stand-in schemas for the keys Axon sets,
    # the desktop-specific defaults Ubuntu 26.04 ships, then Axon's file.
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    axon = _generate_override(schema_dir)
    plain = _plain_groups(_parse_override(axon))

    schemas = []
    for name, keys in plain.items():
        key_xml = "".join(
            f'<key name="{key}" type="{_gvariant_type(value)}">'
            f"<default>{xml_escape(value)}</default></key>"
            for key, value in keys.items()
        )
        path = "/" + name.replace(".", "/") + "/"
        schemas.append(f'<schema id="{name}" path="{path}">{key_xml}</schema>')
    (schema_dir / "axon-test.gschema.xml").write_text(
        f"<schemalist>{''.join(schemas)}</schemalist>"
    )
    # Abridged from gnome-shell-common 50.1 and ubuntu-settings 26.04.6
    (schema_dir / "00_org.gnome.shell.gschema.override").write_text(
        "[org.gnome.mutter:GNOME]\ndynamic-workspaces=true\nedge-tiling=true\n"
    )
    (schema_dir / "10_ubuntu-settings.gschema.override").write_text(
        "[org.gnome.desktop.interface:ubuntu]\n"
        "gtk-theme='Yaru'\nicon-theme='Yaru'\nfont-name='Ubuntu Sans 11'\n\n"
        "[org.gnome.desktop.background:ubuntu]\n"
        "picture-uri='file:///usr/share/backgrounds/warty-final-ubuntu.png'\n\n"
        "[org.gnome.mutter:ubuntu]\ndynamic-workspaces=true\n\n"
        "[org.gnome.desktop.wm.preferences:ubuntu]\nbutton-layout=':minimize,maximize,close'\n\n"
        "[org.gnome.shell:ubuntu]\nfavorite-apps=['firefox_firefox.desktop']\n"
    )
    subprocess.run(
        ["glib-compile-schemas", str(schema_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

    env = {
        **os.environ,
        "GSETTINGS_SCHEMA_DIR": str(schema_dir),
        "GSETTINGS_BACKEND": "memory",
        "XDG_CURRENT_DESKTOP": current_desktop,
    }
    for schema, key in [
        ("org.gnome.desktop.interface", "gtk-theme"),
        ("org.gnome.desktop.interface", "icon-theme"),
        ("org.gnome.desktop.interface", "font-name"),
        ("org.gnome.desktop.background", "picture-uri"),
        ("org.gnome.mutter", "dynamic-workspaces"),
        ("org.gnome.desktop.wm.preferences", "button-layout"),
        ("org.gnome.shell", "favorite-apps"),
    ]:
        result = subprocess.run(
            ["gsettings", "get", schema, key],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == plain[schema][key], f"{schema} {key}"


# ---------------------------------------------------------------------------
# Small screens (needs GTK 4 + a display, e.g. under xvfb-run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gtk_app():
    """GTK module plus one registered application shared by the tests."""
    gi = pytest.importorskip("gi", reason="Requires PyGObject (Linux only)")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, Gtk
    except (ImportError, ValueError) as exc:
        pytest.skip(f"GTK 4 / libadwaita not available: {exc}")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("no display (run under xvfb-run)")
    if not Gtk.init_check():
        pytest.skip("cannot open a display")
    app = Adw.Application(
        application_id="org.axonos.tests.SmallScreen", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    app.register(None)
    yield Gtk, app
    app.quit()


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _min_size(Gtk, window):
    content = window.get_content()
    width = content.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    height = content.measure(Gtk.Orientation.VERTICAL, width)[0]
    return width, height


@pytest.mark.integration
def test_installer_wizard_fits_small_screens(gtk_app):
    Gtk, app = gtk_app
    wizard = _load("axon_installer_wizard", ROOT / "apps" / "axon-installer" / "ui" / "wizard.py")
    window = wizard.InstallerWindow(app)
    try:
        width, height = _min_size(Gtk, window)
    finally:
        window.destroy()
    assert width <= SMALL_WORK_AREA[0] and height <= SMALL_WORK_AREA[1], (width, height)


@pytest.mark.integration
def test_welcome_app_fits_small_screens(gtk_app):
    Gtk, app = gtk_app
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        pytest.skip("WelcomeWindow needs a D-Bus session bus")
    welcome = _load("axon_welcome_ui", ROOT / "apps" / "axon-welcome" / "ui" / "welcome.py")
    window = welcome.WelcomeWindow(app)
    try:
        width, height = _min_size(Gtk, window)
    finally:
        window.destroy()
    assert width <= SMALL_WORK_AREA[0] and height <= SMALL_WORK_AREA[1], (width, height)
