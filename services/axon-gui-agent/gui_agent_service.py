#!/usr/bin/env python3
"""Axon GUI Agent — the desktop drives itself (org.axonos.GuiAgent).

Takes a natural-language instruction ("turn on night light and make the
font bigger"), asks the Brain to compile it into a JSON plan of atomic
desktop operations, validates every operation against a strict allowlist
(plan.py), then executes them through `gsettings`/`gtk-launch` in
milliseconds — no screenshot pipelines, no synthetic clicks.
"""

import json
import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axon_logger import configure_app_logger

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan as plan_mod

log = configure_app_logger("axon-gui-agent", level=logging.INFO)

PLANNER_PROMPT = """\
You are the desktop automation planner for Axon OS (GNOME). Convert the
user's request into a JSON array of operations. Allowed operation shapes:

  {"type": "gsettings_set", "schema": "<schema>", "key": "<key>", "value": <value>}
  {"type": "launch_app", "app": "<desktop-file-or-executable-name>"}
  {"type": "notify", "message": "<text shown to the user>"}

Useful schemas: org.gnome.desktop.interface (font-name, color-scheme,
text-scaling-factor, enable-animations, clock-show-seconds),
org.gnome.desktop.background (picture-uri, picture-uri-dark),
org.gnome.settings-daemon.plugins.color (night-light-enabled,
night-light-temperature), org.gnome.desktop.wm.preferences (button-layout,
num-workspaces), org.gnome.desktop.peripherals.touchpad (tap-to-click,
natural-scroll), org.gnome.mutter (edge-tiling, dynamic-workspaces).

Respond with ONLY the JSON array. No markdown, no commentary.

User request: """


class GuiAgentService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        try:
            self.bus_name = dbus.service.BusName("org.axonos.GuiAgent", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            log.error("org.axonos.GuiAgent service is already running.")
            sys.exit(1)
        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/GuiAgent")
        log.info("Axon GUI Agent registered at /org/axonos/GuiAgent")

    # ------------------------------------------------------------------
    # D-Bus API
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.GuiAgent", in_signature="s", out_signature="s")
    def Execute(self, instruction):
        """Plan + run a desktop instruction synchronously; JSON report."""
        return json.dumps(self._run(str(instruction)))

    @dbus.service.method("org.axonos.GuiAgent", in_signature="s", out_signature="s")
    def ExecuteAsync(self, instruction):
        """Fire-and-forget variant; completion announced via ActionsDone."""
        text = str(instruction)

        def worker():
            report = self._run(text)
            GLib.idle_add(self.ActionsDone, json.dumps(report))

        threading.Thread(target=worker, daemon=True).start()
        return "started"

    @dbus.service.signal("org.axonos.GuiAgent", signature="s")
    def ActionsDone(self, report_json):
        pass

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def _run(self, instruction):
        raw = self._ask_brain(instruction)
        if raw is None:
            return {"ok": False, "error": "AI brain unavailable", "executed": [], "skipped": []}
        ops, errors = plan_mod.validate_plan(raw)
        executed: list[str] = []
        failed: list[str] = []
        for op in ops:
            ok, detail = self._apply(op)
            (executed if ok else failed).append(detail)
        report = {
            "ok": bool(executed) and not failed,
            "executed": executed,
            "failed": failed,
            "skipped": errors,
        }
        summary = f"{len(executed)} change(s) applied"
        if failed or errors:
            summary += f", {len(failed) + len(errors)} skipped"
        self._notify("Axon GUI Agent", summary)
        return report

    def _ask_brain(self, instruction):
        try:
            obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            brain = dbus.Interface(obj, "org.axonos.Brain")
            return str(brain.Generate(PLANNER_PROMPT + instruction, "", "", False, timeout=60))
        except dbus.exceptions.DBusException:
            return None

    def _apply(self, op):
        op_type = op["type"]
        try:
            if op_type == "gsettings_set":
                value = plan_mod.to_gvariant(op["value"])
                # Validate value field for dangerous characters
                if any(c in str(value) for c in ";|&$`\n\\"):
                    return False, "rejected: suspicious characters in value"
                proc = subprocess.run(
                    ["gsettings", "set", str(op["schema"]), str(op["key"]), value],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                detail = f"gsettings {op['schema']} {op['key']} = {value}"
                if proc.returncode != 0:
                    return False, f"{detail} ({proc.stderr.strip()[:120]})"
                return True, detail
            if op_type == "launch_app":
                app = str(op["app"])
                launcher = ["gtk-launch", app] if shutil.which("gtk-launch") else [app]
                subprocess.Popen(launcher, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True, f"launched {app}"
            if op_type == "notify":
                self._notify("Axon", str(op["message"]))
                return True, "notified"
        except Exception as exc:
            return False, f"{op_type}: {exc}"
        return False, f"unhandled op {op_type}"

    def _notify(self, title, body):
        if shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", "-i", "preferences-system", title, body[:300]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    loop = GLib.MainLoop()
    service = GuiAgentService()
    try:
        loop.run()
    except KeyboardInterrupt:
        loop.quit()
