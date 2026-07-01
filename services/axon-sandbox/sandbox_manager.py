#!/usr/bin/env python3
import json
import sys
import threading
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

# Ensure we can load axon_logger
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from axon_logger import configure_app_logger

    logger = configure_app_logger(__name__)
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("axon-sandbox")


class SandboxPromptDialog(Gtk.Window):
    def __init__(self, script_name, warnings, callback):
        super().__init__()
        self.callback = callback
        self.set_title("Axon Rogue Shield")
        self.set_default_size(520, 360)
        self.set_decorated(True)
        self.set_keep_above(True)

        # UI Styling
        self.add_css_class("sandbox-dialog")
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            """
            .sandbox-dialog {
                background-color: #0b0b12;
            }
            .title-label {
                font-family: "Inter", sans-serif;
                font-size: 18px;
                font-weight: bold;
                color: #fca5a5;
                margin-bottom: 8px;
            }
            .subtitle-label {
                font-size: 13px;
                color: #e4e4e8;
                margin-bottom: 16px;
            }
            .warning-item {
                font-size: 13px;
                color: #f87171;
                margin-bottom: 4px;
            }
            .btn-sandbox {
                background-color: #5b21b6;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 8px;
            }
            .btn-allow {
                background-color: #374151;
                color: #e5e7eb;
                padding: 8px 16px;
                border-radius: 8px;
            }
            .btn-block {
                background-color: #991b1b;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 8px;
            }
        """,
            -1,
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Main Layout
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self.set_child(root)

        # Alert header
        title = Gtk.Label(label="🛡️ Axon Rogue Software Shield")
        title.add_css_class("title-label")
        title.set_xalign(0.0)
        root.append(title)

        subtitle = Gtk.Label(label=f"Suspicious operations detected in: {script_name}")
        subtitle.add_css_class("subtitle-label")
        subtitle.set_xalign(0.0)
        subtitle.set_wrap(True)
        root.append(subtitle)

        # Warnings scroll area
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.append(scroll)

        warnings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll.set_child(warnings_box)

        if not warnings:
            lbl = Gtk.Label(label="• Script accessed direct execution parameters.")
            lbl.add_css_class("warning-item")
            lbl.set_xalign(0.0)
            warnings_box.append(lbl)
        else:
            for w in warnings:
                lbl = Gtk.Label(label=f"⚠️ {w}")
                lbl.add_css_class("warning-item")
                lbl.set_xalign(0.0)
                lbl.set_wrap(True)
                warnings_box.append(lbl)

        # Buttons row
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons_box.set_margin_top(16)
        buttons_box.set_halign(Gtk.Align.END)
        root.append(buttons_box)

        btn_sandbox = Gtk.Button(label="Run Sandboxed (Secure)")
        btn_sandbox.add_css_class("btn-sandbox")
        btn_sandbox.connect("clicked", self.on_sandbox_clicked)
        buttons_box.append(btn_sandbox)

        btn_allow = Gtk.Button(label="Allow Normally")
        btn_allow.add_css_class("btn-allow")
        btn_allow.connect("clicked", self.on_allow_clicked)
        buttons_box.append(btn_allow)

        btn_block = Gtk.Button(label="Block")
        btn_block.add_css_class("btn-block")
        btn_block.connect("clicked", self.on_block_clicked)
        buttons_box.append(btn_block)

        self.connect("close-request", self.on_close_request)

    def on_sandbox_clicked(self, btn):
        self.callback("sandbox")
        self.destroy()

    def on_allow_clicked(self, btn):
        self.callback("allow")
        self.destroy()

    def on_block_clicked(self, btn):
        self.callback("block")
        self.destroy()

    def on_close_request(self, win):
        self.callback("block")
        return False


class SandboxManager(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()

        try:
            self.bus_name = dbus.service.BusName("org.axonos.Sandbox", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            logger.error("org.axonos.Sandbox service is already running.")
            sys.exit(1)

        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/Sandbox")
        logger.info(
            "Axon Sandbox Manager D-Bus Service registered successfully at /org/axonos/Sandbox"
        )

    @dbus.service.method(
        "org.axonos.Sandbox",
        in_signature="s",
        out_signature="s",
        async_callbacks=("dbus_ok", "dbus_err"),
    )
    def AuditAndPrompt(self, script_path, dbus_ok, dbus_err):
        """Asynchronously audits a script and prompts the user for sandbox choice."""
        logger.info(f"Received Sandbox audit request for: {script_path}")
        threading.Thread(
            target=self._do_audit_and_prompt, args=(script_path, dbus_ok, dbus_err), daemon=True
        ).start()

    def _do_audit_and_prompt(self, script_path, dbus_ok, dbus_err):
        try:
            p = Path(script_path)
            if not p.exists() or not p.is_file():
                logger.warning(
                    "Sandbox audit: file not found or not a regular file: %s", script_path
                )
                dbus_ok("deny")
                return

            # Read script content
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")[:3000].strip()
            except Exception as e:
                logger.warning("Sandbox audit: failed to read script %s: %s", script_path, e)
                dbus_ok("deny")
                return

            # Call Brain service to check warnings
            warnings = []
            try:
                brain_obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
                brain_interface = dbus.Interface(brain_obj, "org.axonos.Brain")

                prompt = (
                    f"Read this script path: {script_path}\n"
                    "Script content:\n"
                    "---BEGIN SCRIPT---\n"
                    f"{content}\n"
                    "---END SCRIPT---\n\n"
                    "Does this script access SSH keys, steal cookies, wipe folders, edit system files, "
                    "or make suspicious cURL requests? Respond ONLY as a JSON list of strings detailing "
                    "the security warning flags (e.g. ['Attempts to write to /etc', 'Accesses private ssh keys']). "
                    "If the script is entirely safe, respond with an empty list []. Do not include markdown codeblocks or other text."
                )

                resp_json = brain_interface.Generate(prompt, "", "", False)
                clean_json = resp_json.strip()
                if clean_json.startswith("```"):
                    clean_json = clean_json.replace("```json", "").replace("```", "").strip()
                warnings = json.loads(clean_json)
            except Exception as e:
                logger.error(f"Failed to fetch AI sandbox analysis: {e}")
                # Simple static parsing backup
                if "ssh" in content.lower():
                    warnings.append("Accesses ssh parameters")
                if "rm -rf" in content.lower():
                    warnings.append("Performs directory wipe commands (rm -rf)")
                if "curl" in content.lower() or "wget" in content.lower():
                    warnings.append("Downloads or posts web payloads")

            # Open warning prompt if warnings exist, otherwise run normally
            if warnings:
                logger.info(f"Script {script_path} flagged. Displaying warning prompt...")

                def launch_dialog():
                    dialog = SandboxPromptDialog(
                        script_name=p.name,
                        warnings=warnings,
                        callback=lambda decision: dbus_ok(decision),
                    )
                    dialog.present()

                GLib.idle_add(launch_dialog)
            else:
                logger.info(f"Script {script_path} is marked clean. Allow execution.")
                dbus_ok("allow")

        except Exception:
            logger.exception("Error in sandbox manager:")
            try:
                dbus_ok("deny")
            except Exception:
                pass


if __name__ == "__main__":
    import signal

    # Initialize GTK before creating any GTK widgets
    Gtk.init()
    loop = GLib.MainLoop()
    service = SandboxManager()

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        loop.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Stopping Axon Sandbox Manager...")
        loop.quit()
