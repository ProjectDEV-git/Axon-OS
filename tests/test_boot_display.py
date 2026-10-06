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
