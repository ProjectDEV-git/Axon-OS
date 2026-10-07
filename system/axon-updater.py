#!/usr/bin/env python3
"""
Axon OS Updater

A seamless, self-healing graphical update manager for Axon OS.
Takes a BTRFS snapshot via timeshift, updates system packages via APT,
updates sandboxed apps via Flatpak, and refreshes the GRUB bootloader.

Rewritten for GTK4 + libadwaita.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk

# ---------------------------------------------------------------------------
# Logging — use the shared Axon logger when available, otherwise stdlib
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from axon_logger import configure_app_logger

    logger = configure_app_logger(__name__)
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("axon-updater")


# ---------------------------------------------------------------------------
# Shared update pipeline helpers
# ---------------------------------------------------------------------------
# Unattended: keep the user's edited config files instead of stopping at
# dpkg's "configuration file modified" prompt, which has no one to answer it.
APT_UPGRADE_CMD = [
    "apt-get",
    "dist-upgrade",
    "-y",
    "-q",
    "-o",
    "Dpkg::Options::=--force-confdef",
    "-o",
    "Dpkg::Options::=--force-confold",
]


def _run_cmd_logged(cmd: list[str], extra_env: dict[str, str] | None = None) -> bool:
    """Run a subprocess; returns True on success. Logs output on failure."""
    try:
        run_env = os.environ.copy()
        if extra_env:
            run_env.update(extra_env)
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=run_env,
        )
        if result.returncode != 0:
            logger.error("Error running '%s':\n%s", " ".join(cmd), result.stdout)
            return False
        return True
    except Exception as exc:
        logger.exception("Exception running '%s': %s", " ".join(cmd), exc)
        return False


UPDATER_PATH = "/usr/local/bin/axon-update"
PROGRESS_PREFIX = "AXON-UPDATE:"
ERROR_PREFIX = "AXON-UPDATE-ERROR:"


def _print_progress(fraction: float, phase: str, message: str) -> None:
    """Machine-readable progress for the GUI, which runs this pipeline via pkexec."""
    print(f"{PROGRESS_PREFIX}{fraction:.2f}:{phase}:{message}", flush=True)  # noqa: T201


def _print_error(message: str) -> None:
    print(f"{ERROR_PREFIX}{message}", flush=True)  # noqa: T201


def run_headless_update(report_progress: bool = False) -> int:
    """Run the update pipeline as root.

    Used by the axon-update-auto systemd unit (``--auto``) and by the GUI,
    which starts ``pkexec axon-update --auto --progress`` so only this part
    runs as root and the window stays with the user.
    """
    if os.geteuid() != 0:
        logger.error("axon-update --auto must run as root")
        return 1

    def progress(fraction: float, phase: str, message: str) -> None:
        logger.info("Auto-update: %s", message)
        if report_progress:
            _print_progress(fraction, phase, message)

    def error(message: str) -> int:
        logger.error(message)
        if report_progress:
            _print_error(message)
        return 1

    progress(0.10, "Phase 1 / 4", "Creating Self-Healing Snapshot…")
    if not _run_cmd_logged(["timeshift", "--create", "--comments", "Axon OS Auto-Update Snapshot"]):
        logger.warning("Snapshot failed (Timeshift not configured?); proceeding")

    progress(0.35, "Phase 2 / 4", "Updating System Packages (APT)…")
    if not _run_cmd_logged(["apt-get", "update"]):
        return error("Failed to update package lists.")

    progress(0.50, "Phase 2 / 4", "Installing System Upgrades…")
    if not _run_cmd_logged(APT_UPGRADE_CMD, extra_env={"DEBIAN_FRONTEND": "noninteractive"}):
        return error("System package update failed.")

    progress(0.75, "Phase 3 / 4", "Updating Sandboxed Apps (Flatpak)…")
    if not _run_cmd_logged(["flatpak", "update", "-y", "--noninteractive"]):
        logger.warning("Flatpak update encountered an issue")

    progress(0.95, "Phase 4 / 4", "Updating Bootloader…")
    if not _run_cmd_logged(["update-grub"]):
        logger.error("update-grub failed — bootloader configuration may be stale")

    progress(1.0, "", "Update complete")
    return 0


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------
class AxonUpdaterWindow(Adw.ApplicationWindow):
    """Main updater window."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("Axon OS Updater")
        self.set_default_size(520, 380)
        self.set_resizable(False)

        # ---- Load external CSS ----
        css_path = Path(__file__).resolve().parent / "axon-updater.css"
        if css_path.exists():
            css_provider = Gtk.CssProvider()
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        # ---- Root layout ----
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        # Header bar
        header = Adw.HeaderBar()
        header.add_css_class("updater-header")
        root.append(header)

        # Content container
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_spacing(12)
        content.set_margin_start(32)
        content.set_margin_end(32)
        content.set_margin_top(16)
        content.set_margin_bottom(24)
        content.set_vexpand(True)
        root.append(content)

        # Status page — icon + title + description
        status = Adw.StatusPage()
        status.set_icon_name("software-update-available-symbolic")
        status.set_title("System Update Available")
        status.set_description("Ready to update core OS and sandboxed apps.")
        status.add_css_class("updater-status-page")
        self._status_page = status
        content.append(status)

        # Phase label
        self._phase_label = Gtk.Label(label="")
        self._phase_label.add_css_class("phase-label")
        self._phase_label.set_halign(Gtk.Align.CENTER)
        content.append(self._phase_label)

        # Progress bar
        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(False)
        self._progress.add_css_class("updater-progress-bar")
        content.append(self._progress)

        # Percentage label below bar
        self._pct_label = Gtk.Label(label="0 %")
        self._pct_label.add_css_class("progress-percent")
        self._pct_label.set_halign(Gtk.Align.END)
        content.append(self._pct_label)

        # Action button
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(8)
        content.append(btn_box)

        self._btn = Gtk.Button(label="Start Update")
        self._btn.add_css_class("updater-start-btn")
        self._btn.connect("clicked", self._on_start_clicked)
        btn_box.append(self._btn)

    # ---- helpers --------------------------------------------------------

    def _set_progress(self, fraction: float) -> None:
        """Thread-safe progress update (fraction 0.0 – 1.0)."""
        GLib.idle_add(self._progress.set_fraction, fraction)
        GLib.idle_add(self._pct_label.set_text, f"{int(fraction * 100)} %")

    def _set_status(self, msg: str, fraction: float | None = None) -> None:
        """Thread-safe status text update."""
        GLib.idle_add(self._status_page.set_description, msg)
        if fraction is not None:
            self._set_progress(fraction)

    def _set_phase(self, text: str) -> None:
        GLib.idle_add(self._phase_label.set_text, text)

    # ---- UI callbacks ---------------------------------------------------

    def _on_start_clicked(self, _btn: Gtk.Button) -> None:
        self._btn.set_sensitive(False)
        self._set_progress(0.0)
        threading.Thread(target=self._update_process, daemon=True).start()

    # ---- update pipeline ------------------------------------------------

    def _update_process(self) -> None:
        """Runs the update pipeline as root via pkexec, following its progress."""
        cmd = [UPDATER_PATH, "--auto", "--progress"]
        if os.geteuid() != 0:
            cmd = ["/usr/bin/pkexec", *cmd]
        self._set_phase("")
        self._set_status("Waiting for authentication…", 0.0)
        error = ""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout or []:
                line = line.rstrip("\n")
                if line.startswith(PROGRESS_PREFIX):
                    fraction, phase, message = line[len(PROGRESS_PREFIX) :].split(":", 2)
                    self._set_phase(phase)
                    self._set_status(message, float(fraction))
                elif line.startswith(ERROR_PREFIX):
                    error = line[len(ERROR_PREFIX) :]
            returncode = proc.wait()
        except OSError as exc:
            logger.exception("Could not start the update: %s", exc)
            returncode, error = 1, "Could not start the update."

        if returncode in (126, 127):  # pkexec: dialog dismissed / not authorized
            self._set_status("Update cancelled. Administrator authentication is required.", 0.0)
            self._set_phase("")
            GLib.idle_add(self._btn.set_sensitive, True)
            return
        if returncode != 0:
            self._set_status("Update Failed.", 0.0)
            self._set_phase("")
            self._progress_remove_add("error")
            GLib.idle_add(
                self._show_error_dialog,
                f"{error or 'The update failed.'} See the journal for details.",
            )
            GLib.idle_add(self._btn.set_sensitive, True)
            return

        # Done
        self._set_progress(1.0)
        self._set_phase("")
        self._set_status("Update Complete! Your system is fully up to date.")
        self._progress_remove_add("complete")
        GLib.idle_add(self._pct_label.add_css_class, "complete")

        GLib.idle_add(self._status_page.set_icon_name, "emblem-ok-symbolic")
        GLib.idle_add(self._status_page.set_title, "All Done")

        # Swap button to "Close"
        def _swap_button():
            self._btn.set_label("Close")
            self._btn.remove_css_class("updater-start-btn")
            self._btn.add_css_class("updater-close-btn")
            self._btn.set_sensitive(True)
            # Reconnect handler
            self._btn.disconnect_by_func(self._on_start_clicked)
            self._btn.connect("clicked", lambda _b: self.close())

        GLib.idle_add(_swap_button)

    # ---- dialogs --------------------------------------------------------

    def _show_error_dialog(self, message: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Update Error",
            body=message,
        )
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.present()

    def _progress_remove_add(self, css_class: str) -> None:
        """Remove all state classes then add *css_class*."""

        def _apply():
            for cls in ("complete", "error"):
                self._progress.remove_css_class(cls)
            self._progress.add_css_class(css_class)

        GLib.idle_add(_apply)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
class AxonUpdaterApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.github.axon_os.Updater")
        self._window: AxonUpdaterWindow | None = None

    def do_activate(self) -> None:
        if self._window is None:
            self._window = AxonUpdaterWindow(application=self)
        self._window.present()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    # Headless mode for the axon-update-auto.service systemd unit: no display
    # is available there, and GTK would reject the unknown --auto option.
    if "--auto" in sys.argv:
        return run_headless_update(report_progress="--progress" in sys.argv)
    app = AxonUpdaterApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
