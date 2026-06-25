#!/usr/bin/env python3
"""
axon-installer.py — GTK4 / libadwaita wizard installer for Axon OS.

Pages:
  1. WelcomePage   — branding + Try/Install options
  2. UserSetupPage — username / password / hostname
  3. DiskPage      — choose target disk + partitioning type (Erase/Alongside)
  4. ProgressPage  — live progress bar during installation
"""

from __future__ import annotations

import re
import sys
import threading
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

# Installer backend
try:
    from partitioner import DiskInfo, Partitioner
except ImportError:
    # Allow running standalone for UI prototyping
    Partitioner = None  # type: ignore[assignment,misc]
    DiskInfo = None  # type: ignore[assignment,misc]


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
    def __init__(self, on_try: callable, on_install: callable) -> None:
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
            "Try it out risk-free in live preview mode, or install it to your drive.",
            justify=Gtk.Justification.CENTER,
        )
        subtitle.set_halign(Gtk.Align.CENTER)
        self.append(subtitle)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(16)

        self.try_btn = Gtk.Button(label="Try Axon OS (Live Mode)")
        self.try_btn.add_css_class("pill")
        self.try_btn.set_size_request(200, 48)
        self.try_btn.connect("clicked", lambda _b: on_try())

        self.install_btn = Gtk.Button(label="Install Axon OS")
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.add_css_class("pill")
        self.install_btn.set_size_request(200, 48)
        self.install_btn.connect("clicked", lambda _b: on_install())

        btn_box.append(self.try_btn)
        btn_box.append(self.install_btn)
        self.append(btn_box)


class UserSetupPage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_top(32)
        self.set_margin_bottom(32)
        self.set_margin_start(64)
        self.set_margin_end(64)

        self.append(
            _label("<span size='x-large' weight='bold'>Create Your Account</span>", use_markup=True)
        )

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.username_entry = _entry("Username")
        self.password_entry = _entry("Password", secret=True)
        self.password2_entry = _entry("Confirm password", secret=True)
        self.hostname_entry = _entry("Computer name (hostname)")
        self.hostname_entry.set_text("axon")

        self.timezone_entry = _entry("Timezone (e.g. America/New_York)")
        self.timezone_entry.set_text("UTC")
        self.keymap_entry = _entry("Keyboard layout (e.g. us)")
        self.keymap_entry.set_text("us")

        for widget in (
            self.username_entry,
            self.password_entry,
            self.password2_entry,
            self.hostname_entry,
            self.timezone_entry,
            self.keymap_entry,
        ):
            form.append(widget)

        self.append(form)

    # ------------------------------------------------------------------
    def get_values(self) -> dict[str, str]:
        return {
            "username": self.username_entry.get_text(),
            "password": self.password_entry.get_text(),
            "password2": self.password2_entry.get_text(),
            "hostname": self.hostname_entry.get_text(),
            "timezone": self.timezone_entry.get_text(),
            "keymap": self.keymap_entry.get_text(),
        }

    def validate(self) -> str | None:
        """Return an error string, or None if the form is valid."""
        v = self.get_values()
        if not v["username"]:
            return "Username cannot be empty."
        if not re.match(r"^[a-z][a-z0-9-]{0,31}$", v["username"]):
            return "Username must be lowercase, start with a letter, and contain only a-z, 0-9, or hyphens (max 32 chars)."
        if not v["password"]:
            return "Password cannot be empty."
        if v["password"] != v["password2"]:
            return "Passwords do not match."
        if not v["hostname"]:
            return "Hostname cannot be empty."
        if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9.-]{0,61}[a-zA-Z0-9])?$", v["hostname"]):
            return "Hostname must be a valid RFC 1123 name (letters, digits, hyphens, dots)."
        if v["timezone"] and (
            ".." in v["timezone"] or not re.match(r"^[A-Za-z0-9_/.-]+$", v["timezone"])
        ):
            return "Invalid timezone format."
        if v["keymap"] and not re.match(r"^[A-Za-z0-9_-]+$", v["keymap"]):
            return "Keymap must contain only letters, digits, underscores, and hyphens."
        return None


class DiskRow(Gtk.ListBoxRow):
    def __init__(self, disk: DiskInfo) -> None:
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

        self.append(
            _label(
                "<span size='x-large' weight='bold'>Choose Installation Disk</span>",
                use_markup=True,
            )
        )

        scroll = Gtk.ScrolledWindow()
        scroll.set_size_request(-1, 120)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._on_disk_selected)

        self._populate()

        scroll.set_child(self.listbox)
        self.append(scroll)

        # Partitioning Options
        self.append(_label("<span weight='bold'>Partitioning Options</span>", use_markup=True))

        self.erase_radio = Gtk.CheckButton(label="Erase entire disk and install Axon OS")
        self.erase_radio.set_active(True)
        self.erase_radio.connect("toggled", self._on_type_toggled)
        self.append(self.erase_radio)

        self.alongside_radio = Gtk.CheckButton(
            label="Install alongside existing operating system (Dual Boot)"
        )
        self.alongside_radio.set_group(self.erase_radio)
        self.append(self.alongside_radio)

        # Alongside details box
        self.alongside_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.alongside_box.set_margin_start(24)
        self.alongside_box.set_sensitive(False)

        part_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        part_box.append(Gtk.Label(label="Target Partition to shrink:"))
        self.part_combo = Gtk.ComboBoxText()
        part_box.append(self.part_combo)
        self.alongside_box.append(part_box)

        slider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        slider_box.append(Gtk.Label(label="Space to allocate for Axon OS (GB):"))
        self.shrink_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 10, 100, 1)
        self.shrink_slider.set_value(30)
        self.shrink_slider.set_hexpand(True)
        slider_box.append(self.shrink_slider)
        self.alongside_box.append(slider_box)

        self.append(self.alongside_box)

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

    def _on_type_toggled(self, _btn) -> None:
        self.alongside_box.set_sensitive(self.alongside_radio.get_active())

    def _on_disk_selected(self, _listbox, row) -> None:
        self.part_combo.remove_all()
        disk_info = self.get_selected_disk()
        if not disk_info:
            return

        resizable_count = 0
        for p in disk_info.partitions:
            if p.fstype in ("ext4", "ext3", "ntfs", "fuseblk"):
                label = f"Partition {p.num} ({p.fstype}) — {p.size}"
                self.part_combo.append(str(p.num), label)
                resizable_count += 1

        if resizable_count > 0:
            self.part_combo.set_active(0)
            self.alongside_radio.set_sensitive(True)
        else:
            self.alongside_radio.set_sensitive(False)
            self.erase_radio.set_active(True)

    def get_selected_disk(self) -> DiskInfo | None:
        row = self.listbox.get_selected_row()
        if isinstance(row, DiskRow):
            return row.disk
        return None

    def get_installation_settings(self) -> dict:
        is_alongside = self.alongside_radio.get_active()
        if is_alongside:
            part_num_str = self.part_combo.get_active_id()
            part_num = int(part_num_str) if part_num_str else None
            shrink_val = int(self.shrink_slider.get_value())
            return {"mode": "alongside", "partition_num": part_num, "shrink_size_gb": shrink_val}
        else:
            return {"mode": "erase"}


class ProgressPage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_top(48)
        self.set_margin_bottom(48)
        self.set_margin_start(64)
        self.set_margin_end(64)

        self.append(
            _label("<span size='x-large' weight='bold'>Installing Axon OS…</span>", use_markup=True)
        )

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

PAGE_WELCOME = "welcome"
PAGE_USER = "user"
PAGE_DISK = "disk"
PAGE_PROGRESS = "progress"

PAGE_ORDER = [PAGE_WELCOME, PAGE_USER, PAGE_DISK, PAGE_PROGRESS]


class InstallerApp(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Axon OS Installer")
        self.set_default_size(700, 560)
        self.set_resizable(False)

        # Pages
        self._welcome_page = WelcomePage(
            on_try=self._on_try_clicked, on_install=self._on_install_clicked
        )
        self._user_page = UserSetupPage()
        self._disk_page = DiskPage()
        self._progress_page = ProgressPage()

        # Stack
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self._stack.set_transition_duration(300)
        self._stack.add_named(self._welcome_page, PAGE_WELCOME)
        self._stack.add_named(self._user_page, PAGE_USER)
        self._stack.add_named(self._disk_page, PAGE_DISK)
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

        self.btn_box = btn_box  # Store reference

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

        if page_name == PAGE_WELCOME:
            self.btn_box.set_visible(False)
        else:
            self.btn_box.set_visible(True)
            self._back_btn.set_sensitive(self._current_index > 1)  # Block backing into Welcome page

            if page_name == PAGE_DISK:
                self._next_btn.set_label("Install")
            elif page_name == PAGE_PROGRESS:
                self._next_btn.set_visible(False)
                self._back_btn.set_visible(False)
            else:
                self._next_btn.set_label("Next")
                self._next_btn.set_visible(True)
                self._back_btn.set_visible(True)

    def _on_try_clicked(self) -> None:
        from axon_logger import configure_app_logger

        logger = configure_app_logger(__name__)
        logger.info("Live Preview Mode triggered.")
        self.get_application().quit()

    def _on_install_clicked(self) -> None:
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self._current_index = PAGE_ORDER.index(PAGE_USER)
        self._update_nav()

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
            disk_settings = self._disk_page.get_installation_settings()
            if self._disk_page.get_selected_disk() is None:
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="No disk selected",
                    body="Please select a disk to install Axon OS.",
                )
                dialog.add_response("ok", "OK")
                dialog.present()
                return

            if disk_settings["mode"] == "erase":

                def on_confirm_response(dialog, response):
                    if response == "install":
                        self._current_index += 1
                        self._update_nav()
                        self._start_install()

                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="Erase Disk Confirmation",
                    body="All data on the selected disk will be permanently destroyed. Are you sure you want to continue?",
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("install", "Erase and Install")
                dialog.set_response_appearance("install", Adw.ResponseAppearance.DESTRUCTIVE)
                dialog.connect("response", on_confirm_response)
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
        disk = self._disk_page.get_selected_disk()
        disk_settings = self._disk_page.get_installation_settings()

        thread = threading.Thread(
            target=self._install_worker,
            args=(disk, disk_settings, user_info),
            daemon=True,
        )
        thread.start()

    def _set_progress(self, fraction: float, status: str) -> None:
        """Thread-safe progress update via GLib.idle_add."""
        GLib.idle_add(self._progress_page.set_progress, fraction, status)

    def _install_worker(
        self,
        disk: DiskInfo | None,
        disk_settings: dict,
        user_info: dict[str, str],
    ) -> None:
        try:
            if Partitioner is None:

                class MockPartitioner:
                    def __init__(self, dry_run=True):
                        self.dry_run = True

                    def list_disks(self):
                        return []

                    def get_partition_map(self, dev):
                        return []

                    def partition_disk(self, dev, mode):
                        raise RuntimeError("Partitioner not available")

                    def partition_alongside(self, dev, pnum, sz):
                        raise RuntimeError("Partitioner not available")

                    def format_partitions(self, dev):
                        raise RuntimeError("Partitioner not available")

                    def format_partitions_alongside(self, dev, rnum):
                        raise RuntimeError("Partitioner not available")

                    def mount_partitions(self, dev, mnt):
                        raise RuntimeError("Partitioner not available")

                    def mount_partitions_alongside(self, dev, enum, rnum, mnt):
                        raise RuntimeError("Partitioner not available")

                part = MockPartitioner()
            else:
                part = Partitioner(dry_run=False)

            device = disk.device if disk else "/dev/sda"
            mount = "/mnt/axon"
            mode = disk_settings["mode"]

            import os

            os.makedirs(mount, exist_ok=True)

            if mode == "erase":
                self._set_progress(0.05, "Partitioning disk…")
                part.partition_disk(device, mode="erase")

                self._set_progress(0.20, "Formatting partitions…")
                part.format_partitions(device)

                self._set_progress(0.35, "Mounting filesystems…")
                part.mount_partitions(device, mount)

                root_device = f"{device}p2" if device[-1].isdigit() else f"{device}2"
                efi_device = f"{device}p1" if device[-1].isdigit() else f"{device}1"
            else:
                part_num = disk_settings["partition_num"]
                shrink_gb = disk_settings["shrink_size_gb"]

                self._set_progress(0.05, f"Shrinking partition {part_num} by {shrink_gb} GB…")
                new_root_num = part.partition_alongside(device, part_num, shrink_gb)
                if not new_root_num:
                    raise RuntimeError("Failed to partition alongside existing operating system.")

                self._set_progress(0.20, "Formatting alongside root partition…")
                part.format_partitions_alongside(device, new_root_num)

                self._set_progress(0.35, "Mounting partitions (reusing EFI)…")

                # Fetch EFI partition index (e.g. fat/vfat formatted partition on disk)
                pmap = part.get_partition_map(device)
                efi_num = 1
                for p in pmap:
                    if p.get("fstype") in ("vfat", "fat32", "fat16"):
                        efi_num = p["num"]
                        break
                part.mount_partitions_alongside(device, efi_num, new_root_num, mount)

                root_device = (
                    f"{device}p{new_root_num}"
                    if device[-1].isdigit()
                    else f"{device}{new_root_num}"
                )
                efi_device = f"{device}p{efi_num}" if device[-1].isdigit() else f"{device}{efi_num}"

            import os
            import subprocess

            def get_uuid(dev_path):
                try:
                    res = subprocess.run(
                        ["blkid", "-o", "value", "-s", "UUID", dev_path],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    return res.stdout.strip()
                except Exception:
                    return None

            self._set_progress(0.50, "Installing base system…")
            if not part.dry_run:
                # Copy from /rofs if it exists, otherwise copy from / with exclusions
                if os.path.exists("/rofs"):
                    subprocess.run(["rsync", "-aHAXS", "/rofs/", mount + "/"], check=True)
                else:
                    subprocess.run(
                        [
                            "rsync",
                            "-aHAXS",
                            "--exclude=/proc/*",
                            "--exclude=/sys/*",
                            "--exclude=/dev/*",
                            "--exclude=/run/*",
                            "--exclude=/tmp/*",
                            "--exclude=/mnt/*",
                            "--exclude=/media/*",
                            "--exclude=/lost+found",
                            "--exclude=/cdrom/*",
                            "--exclude=/boot/*",
                            "--exclude=/home/*",
                            "/",
                            mount + "/",
                        ],
                        check=True,
                    )

            self._set_progress(0.75, "Configuring system…")
            if not part.dry_run:
                root_uuid = get_uuid(root_device)
                efi_uuid = get_uuid(efi_device)

                # Write fstab
                fstab_content = ""
                if root_uuid:
                    fstab_content += (
                        f"UUID={root_uuid} / btrfs subvol=@,defaults,noatime,space_cache=v2 0 0\n"
                    )
                    fstab_content += f"UUID={root_uuid} /home btrfs subvol=@home,defaults,noatime,space_cache=v2 0 0\n"
                else:
                    fstab_content += (
                        f"{root_device} / btrfs subvol=@,defaults,noatime,space_cache=v2 0 0\n"
                    )
                    fstab_content += f"{root_device} /home btrfs subvol=@home,defaults,noatime,space_cache=v2 0 0\n"
                if efi_uuid:
                    fstab_content += f"UUID={efi_uuid} /boot/efi vfat defaults 0 2\n"
                else:
                    fstab_content += f"{efi_device} /boot/efi vfat defaults 0 2\n"

                os.makedirs(os.path.join(mount, "etc"), exist_ok=True)
                with open(os.path.join(mount, "etc", "fstab"), "w") as f:
                    f.write(fstab_content)

                # Write hostname
                hostname = user_info.get("hostname", "axon-os")
                with open(os.path.join(mount, "etc", "hostname"), "w") as f:
                    f.write(hostname + "\n")

                # Write hosts
                hosts_content = f"127.0.0.1\tlocalhost\n127.0.1.1\t{hostname}\n\n::1\t\tip6-localhost ip6-loopback\nfe00::0\t\tip6-localnet\nff00::0\t\tip6-mcastprefix\nff02::1\t\tip6-allnodes\nff02::2\t\tip6-allrouters\n"
                with open(os.path.join(mount, "etc", "hosts"), "w") as f:
                    f.write(hosts_content)

                # Clean up live user if present
                subprocess.run(["chroot", mount, "userdel", "-r", "axon"], check=False)

                # Write timezone
                tz = user_info.get("timezone", "UTC")
                if tz:
                    subprocess.run(
                        [
                            "chroot",
                            mount,
                            "ln",
                            "-sf",
                            f"/usr/share/zoneinfo/{tz}",
                            "/etc/localtime",
                        ],
                        check=False,
                    )

                # Write keyboard
                keymap = user_info.get("keymap", "us")
                kbd_content = f'XKBMODEL="pc105"\nXKBLAYOUT="{keymap}"\nXKBVARIANT=""\nXKBOPTIONS=""\nBACKSPACE="guess"\n'
                os.makedirs(os.path.join(mount, "etc", "default"), exist_ok=True)
                with open(os.path.join(mount, "etc", "default", "keyboard"), "w") as f:
                    f.write(kbd_content)

                # Enable OS Prober for GRUB dual-boot detection
                # Add rootflags=subvol=@ so the kernel mounts the correct BTRFS subvolume
                with open(os.path.join(mount, "etc", "default", "grub"), "a") as f:
                    f.write("\nGRUB_DISABLE_OS_PROBER=false\n")
                    f.write(
                        'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash rootflags=subvol=@ console=tty0"\n'
                    )
                    f.write('GRUB_CMDLINE_LINUX=""\n')

                # Create user with password via chpasswd
                username = user_info["username"]
                password = user_info["password"]

                subprocess.run(
                    ["chroot", mount, "useradd", "-m", "-s", "/bin/bash", username], check=True
                )

                proc = subprocess.Popen(
                    ["chroot", mount, "chpasswd"], stdin=subprocess.PIPE, text=True
                )
                proc.communicate(f"{username}:{password}\n")
                if proc.returncode != 0:
                    raise RuntimeError("Failed to set user password.")

                subprocess.run(
                    ["chroot", mount, "usermod", "-aG", "sudo,adm,cdrom,dip,plugdev", username],
                    check=True,
                )
                subprocess.run(
                    ["chroot", mount, "chown", "-R", f"{username}:{username}", f"/home/{username}"],
                    check=True,
                )

                # Remove installer shortcut from new user desktop
                installer_desktop = os.path.join(
                    mount, "home", username, "Desktop", "install-axon-os.desktop"
                )
                if os.path.exists(installer_desktop):
                    os.remove(installer_desktop)

            self._set_progress(0.85, "Creating swapfile…")
            if not part.dry_run:
                subprocess.run(["chroot", mount, "chattr", "+C", "/swapfile"], check=False)
                subprocess.run(["chroot", mount, "fallocate", "-l", "2G", "/swapfile"], check=False)
                subprocess.run(["chroot", mount, "chmod", "600", "/swapfile"], check=False)
                subprocess.run(["chroot", mount, "mkswap", "/swapfile"], check=False)
                fstab_path = os.path.join(mount, "etc", "fstab")
                existing = ""
                if os.path.exists(fstab_path):
                    existing = open(fstab_path).read()
                if "/swapfile" not in existing:
                    with open(fstab_path, "a") as f:
                        f.write("/swapfile none swap sw 0 0\n")

            self._set_progress(0.90, "Installing bootloader (configuring dual boot GRUB)…")
            if not part.dry_run:
                # Mount bind virtual filesystems
                subprocess.run(["mount", "--bind", "/dev", os.path.join(mount, "dev")], check=True)
                subprocess.run(
                    ["mount", "--bind", "/proc", os.path.join(mount, "proc")], check=True
                )
                subprocess.run(["mount", "--bind", "/sys", os.path.join(mount, "sys")], check=True)
                subprocess.run(["mount", "--bind", "/run", os.path.join(mount, "run")], check=True)

                efi_vars_src = "/sys/firmware/efi/efivars"
                efi_vars_dest = os.path.join(mount, "sys/firmware/efi/efivars")
                mount_efivars = os.path.exists(efi_vars_src)
                if mount_efivars:
                    subprocess.run(["mount", "--bind", efi_vars_src, efi_vars_dest], check=True)

                try:
                    # Install grub
                    subprocess.run(
                        [
                            "chroot",
                            mount,
                            "grub-install",
                            "--target=x86_64-efi",
                            "--efi-directory=/boot/efi",
                            "--bootloader-id=Axon OS",
                            "--recheck",
                        ],
                        check=True,
                    )

                    # Update grub
                    subprocess.run(["chroot", mount, "update-grub"], check=True)
                finally:
                    # Unmount virtual filesystems
                    if mount_efivars:
                        subprocess.run(["umount", "-l", efi_vars_dest], check=False)
                    subprocess.run(["umount", "-l", os.path.join(mount, "dev")], check=False)
                    subprocess.run(["umount", "-l", os.path.join(mount, "proc")], check=False)
                    subprocess.run(["umount", "-l", os.path.join(mount, "sys")], check=False)
                    subprocess.run(["umount", "-l", os.path.join(mount, "run")], check=False)

            self._set_progress(1.00, "Installation complete!")
            GLib.idle_add(self._show_done_dialog)

        except Exception as exc:
            GLib.idle_add(self._show_error_dialog, str(exc))
        finally:
            # Clean up all mounts safely (order: deepest first)
            mount_targets = [
                os.path.join(mount, "sys/firmware/efi/efivars"),
                os.path.join(mount, "run"),
                os.path.join(mount, "sys"),
                os.path.join(mount, "proc"),
                os.path.join(mount, "dev"),
                os.path.join(mount, "boot/efi"),
                os.path.join(mount, "home"),
                mount,
            ]
            for target in mount_targets:
                try:
                    subprocess.run(["umount", "-l", target], check=False, capture_output=True)
                except Exception:
                    pass

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
