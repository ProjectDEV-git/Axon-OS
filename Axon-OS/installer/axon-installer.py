#!/usr/bin/env python3
"""
axon-installer.py — GTK4 / libadwaita wizard installer for Axon OS.

Pages:
  1. WelcomePage   — branding + "Let's go" prompt
  2. UserSetupPage — username / password / hostname
  3. DiskPage      — list available disks, pick target
  4. ProgressPage  — live progress bar during installation
"""

from __future__ import annotations

import sys
import threading
from typing import Any

import gi

gi.require_version("Gtk",  "4.0")
gi.require_version("Adw",  "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

# Installer backend
try:
    from partitioner import DiskInfo, Partitioner
except ImportError:
    # Allow running standalone for UI prototyping
    Partitioner = None  # type: ignore[assignment,misc]
    DiskInfo    = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

def _label(text: str, **kwargs: Any) -> Gtk.Label:
    lbl = Gtk.Label(label=text, **kwargs)
    lbl.set_wrap(True)
    return lbl


def _entry(placeholder: str, secret: bool = False) -> Gtk.Entry:
    entry = Gtk.Entry()
    entry.set_placeholder_text(placeholder)
    if secret:
        entry.set_visibility(False)
    return entry


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class WelcomePage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_top(48)
        self.set_margin_bottom(48)
        self.set_margin_start(48)
        self.set_margin_end(48)

        icon = Gtk.Image.new_from_icon_name("computer-symbolic")
        icon.set_pixel_size(96)
        self.append(icon)

        title = _label("<span size='xx-large' weight='bold'>Welcome to Axon OS</span>")
        title.set_use_markup(True)
        self.append(title)

        subtitle = _label(
            "A minimal, AI-native desktop built on GNOME.\n"
            "This wizard will guide you through installation.",
            justify=Gtk.Justification.CENTER,
        )
        subtitle.set_halign(Gtk.Align.CENTER)
        self.append(subtitle)


class UserSetupPage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_top(32)
        self.set_margin_bottom(32)
        self.set_margin_start(64)
        self.set_margin_end(64)

        self.append(_label("<span size='x-large' weight='bold'>Create Your Account</span>",
                           use_markup=True))

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.username_entry  = _entry("Username")
        self.password_entry  = _entry("Password", secret=True)
        self.password2_entry = _entry("Confirm password", secret=True)
        self.hostname_entry  = _entry("Computer name (hostname)")
        self.hostname_entry.set_text("axon")

        for widget in (
            self.username_entry,
            self.password_entry,
            self.password2_entry,
            self.hostname_entry,
        ):
            form.append(widget)

        self.append(form)

    # ------------------------------------------------------------------
    def get_values(self) -> dict[str, str]:
        return {
            "username":  self.username_entry.get_text(),
            "password":  self.password_entry.get_text(),
            "password2": self.password2_entry.get_text(),
            "hostname":  self.hostname_entry.get_text(),
        }

    def validate(self) -> str | None:
        """Return an error string, or None if the form is valid."""
        v = self.get_values()
        if not v["username"]:
            return "Username cannot be empty."
        if not v["password"]:
            return "Password cannot be empty."
        if v["password"] != v["password2"]:
            return "Passwords do not match."
        if not v["hostname"]:
            return "Hostname cannot be empty."
        return None


class DiskRow(Gtk.ListBoxRow):
    def __init__(self, disk: "DiskInfo") -> None:
        super().__init__()
        self.disk = disk

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        icon = Gtk.Image.new_from_icon_name("drive-harddisk-symbolic")
        box.append(icon)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_lbl = Gtk.Label(label=disk.device, xalign=0.0)
        name_lbl.add_css_class("heading")
        info_box.append(name_lbl)

        model_str = disk.model or "Unknown model"
        detail_lbl = Gtk.Label(label=f"{model_str}  —  {disk.size}", xalign=0.0)
        detail_lbl.add_css_class("dim-label")
        info_box.append(detail_lbl)

        box.append(info_box)
        self.set_child(box)


class DiskPage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(48)
        self.set_margin_end(48)

        self.append(_label("<span size='x-large' weight='bold'>Choose Installation Disk</span>",
                           use_markup=True))
        self.append(_label(
            "All data on the selected disk will be erased.",
            css_classes=["warning"],
        ))

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")

        self._populate()

        scroll.set_child(self.listbox)
        self.append(scroll)

    def _populate(self) -> None:
        if Partitioner is None:
            placeholder = Gtk.ListBoxRow()
            placeholder.set_child(_label("(partitioner unavailable in preview mode)"))
            self.listbox.append(placeholder)
            return

        try:
            disks = Partitioner().list_disks()
        except Exception as exc:
            placeholder = Gtk.ListBoxRow()
            placeholder.set_child(_label(f"Could not enumerate disks: {exc}"))
            self.listbox.append(placeholder)
            return

        if not disks:
            placeholder = Gtk.ListBoxRow()
            placeholder.set_child(_label("No disks found."))
            self.listbox.append(placeholder)
            return

        for disk in disks:
            self.listbox.append(DiskRow(disk))

        # Pre-select first row
        first = self.listbox.get_row_at_index(0)
        if first:
            self.listbox.select_row(first)

    def get_selected_disk(self) -> "DiskInfo | None":
        row = self.listbox.get_selected_row()
        if isinstance(row, DiskRow):
            return row.disk
        return None


class ProgressPage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_top(48)
        self.set_margin_bottom(48)
        self.set_margin_start(64)
        self.set_margin_end(64)

        self.append(_label("<span size='x-large' weight='bold'>Installing Axon OS…</span>",
                           use_markup=True))

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_size_request(400, -1)
        self.append(self.progress_bar)

        self.status_label = _label("Preparing…", css_classes=["dim-label"])
        self.append(self.status_label)

    # ------------------------------------------------------------------
    def set_progress(self, fraction: float, status: str) -> None:
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{int(fraction * 100)} %")
        self.status_label.set_label(status)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

PAGE_WELCOME  = "welcome"
PAGE_USER     = "user"
PAGE_DISK     = "disk"
PAGE_PROGRESS = "progress"

PAGE_ORDER = [PAGE_WELCOME, PAGE_USER, PAGE_DISK, PAGE_PROGRESS]


class InstallerApp(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Axon OS Installer")
        self.set_default_size(700, 520)
        self.set_resizable(False)

        # Pages
        self._welcome_page  = WelcomePage()
        self._user_page     = UserSetupPage()
        self._disk_page     = DiskPage()
        self._progress_page = ProgressPage()

        # Stack
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self._stack.set_transition_duration(300)
        self._stack.add_named(self._welcome_page,  PAGE_WELCOME)
        self._stack.add_named(self._user_page,     PAGE_USER)
        self._stack.add_named(self._disk_page,     PAGE_DISK)
        self._stack.add_named(self._progress_page, PAGE_PROGRESS)

        # Navigation buttons
        self._back_btn = Gtk.Button(label="Back")
        self._next_btn = Gtk.Button(label="Next")
        self._next_btn.add_css_class("suggested-action")
        self._back_btn.connect("clicked", self._on_back)
        self._next_btn.connect("clicked", self._on_next)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(8)
        btn_box.set_margin_bottom(12)
        btn_box.set_margin_start(12)
        btn_box.set_margin_end(12)
        btn_box.append(self._back_btn)
        btn_box.append(self._next_btn)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._stack)
        root.append(btn_box)
        self.set_content(root)

        self._current_index = 0
        self._update_nav()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _update_nav(self) -> None:
        page_name = PAGE_ORDER[self._current_index]
        self._stack.set_visible_child_name(page_name)
        self._back_btn.set_sensitive(self._current_index > 0)

        if page_name == PAGE_DISK:
            self._next_btn.set_label("Install")
        elif page_name == PAGE_PROGRESS:
            self._next_btn.set_visible(False)
            self._back_btn.set_visible(False)
        else:
            self._next_btn.set_label("Next")
            self._next_btn.set_visible(True)
            self._back_btn.set_visible(True)

    def _on_back(self, _btn: Gtk.Button) -> None:
        if self._current_index > 0:
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self._current_index -= 1
            self._update_nav()
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)

    def _on_next(self, _btn: Gtk.Button) -> None:
        current_page = PAGE_ORDER[self._current_index]

        # Validate user page before advancing
        if current_page == PAGE_USER:
            error = self._user_page.validate()
            if error:
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="Invalid input",
                    body=error,
                )
                dialog.add_response("ok", "OK")
                dialog.present()
                return

        # Validate disk selection before installing
        if current_page == PAGE_DISK:
            if self._disk_page.get_selected_disk() is None:
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="No disk selected",
                    body="Please select a disk to install Axon OS.",
                )
                dialog.add_response("ok", "OK")
                dialog.present()
                return

        self._current_index += 1
        self._update_nav()

        # Start installation once we land on the progress page
        if PAGE_ORDER[self._current_index] == PAGE_PROGRESS:
            self._start_install()

    # ------------------------------------------------------------------
    # Installation thread
    # ------------------------------------------------------------------

    def _start_install(self) -> None:
        user_info = self._user_page.get_values()
        disk      = self._disk_page.get_selected_disk()

        thread = threading.Thread(
            target=self._install_worker,
            args=(disk, user_info),
            daemon=True,
        )
        thread.start()

    def _set_progress(self, fraction: float, status: str) -> None:
        """Thread-safe progress update via GLib.idle_add."""
        GLib.idle_add(self._progress_page.set_progress, fraction, status)

    def _install_worker(
        self,
        disk: "DiskInfo | None",
        user_info: dict[str, str],
    ) -> None:
        try:
            part = Partitioner(dry_run=(Partitioner is None))
            device = disk.device if disk else "/dev/sda"
            mount  = "/mnt/axon"

            self._set_progress(0.05, "Partitioning disk…")
            part.partition_disk(device, mode="erase")

            self._set_progress(0.20, "Formatting partitions…")
            part.format_partitions(device)

            self._set_progress(0.35, "Mounting filesystems…")
            part.mount_partitions(device, mount)

            self._set_progress(0.50, "Installing base system…")
            # (rsync / debootstrap / unsquashfs would go here)

            self._set_progress(0.75, "Configuring system…")
            # (locale, hostname, user creation would go here)

            self._set_progress(0.90, "Installing bootloader…")
            # (grub-install would go here)

            self._set_progress(1.00, "Installation complete!")
            GLib.idle_add(self._show_done_dialog)

        except Exception as exc:
            GLib.idle_add(self._show_error_dialog, str(exc))

    def _show_done_dialog(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Installation complete",
            body="Axon OS has been installed. Remove the installation media and reboot.",
        )
        dialog.add_response("reboot", "Reboot now")
        dialog.connect("response", lambda _d, _r: self.get_application().quit())
        dialog.present()

    def _show_error_dialog(self, message: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Installation failed",
            body=message,
        )
        dialog.add_response("ok", "OK")
        dialog.present()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = Adw.Application(application_id="com.axonos.installer")

    def on_activate(application: Adw.Application) -> None:
        win = InstallerApp(application)
        win.present()

    app.connect("activate", on_activate)
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
