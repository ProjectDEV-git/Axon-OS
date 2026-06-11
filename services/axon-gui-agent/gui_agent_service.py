#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import threading
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

# Ensure we can load axon_logger
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from axon_logger import configure_app_logger
    logger = configure_app_logger(__name__)
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("axon-gui-agent")

class GuiAgentService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        
        try:
            self.bus_name = dbus.service.BusName('org.axonos.GuiAgent', bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            logger.error("org.axonos.GuiAgent service is already running.")
            sys.exit(1)
            
        dbus.service.Object.__init__(self, self.session_bus, '/org/axonos/GuiAgent')
        logger.info("Axon GUI Agent registered successfully at /org/axonos/GuiAgent")

    @dbus.service.method('org.axonos.GuiAgent', in_signature='s', out_signature='b')
    def ExecuteInstruction(self, instruction):
        """Asynchronously translates and executes natural language instruction."""
        logger.info(f"Received GUI Agent instruction: '{instruction}'")
        threading.Thread(target=self._do_execute, args=(instruction,), daemon=True).start()
        return True

    def _do_execute(self, instruction):
        try:
            # Query Brain D-Bus service
            brain_obj = self.session_bus.get_object('org.axonos.Brain', '/org/axonos/Brain')
            brain_interface = dbus.Interface(brain_obj, 'org.axonos.Brain')
            
            system_prompt = (
                "You are the desktop automation engine for Axon OS. Convert the user's natural language request "
                "into a JSON list of configuration steps to execute. Valid actions are:\n"
                "1. {'action': 'gsettings', 'schema': '<schema_name>', 'key': '<key_name>', 'value': '<value_string_or_boolean>'}\n"
                "2. {'action': 'dbus', 'destination': '<service>', 'path': '<path>', 'interface': '<iface>', 'method': '<method>', 'args': [<args>]}\n"
                "3. {'action': 'shell', 'command': '<executable_or_shell_command>'}\n\n"
                "Respond ONLY with a valid JSON array of these objects. Do not include markdown codeblocks or explanations. "
                "Example: [{\"action\": \"gsettings\", \"schema\": \"org.gnome.desktop.interface\", \"key\": \"font-name\", \"value\": \"'Inter 11'\"}]"
            )
            
            resp = brain_interface.Generate(instruction, "", "", False)
            clean_resp = resp.strip()
            if clean_resp.startswith("```"):
                clean_resp = clean_resp.replace("```json", "").replace("```", "").strip()
                
            actions = json.loads(clean_resp)
            if not isinstance(actions, list):
                logger.warning("GUI Agent received non-list output from Brain.")
                return

            for act in actions:
                action_type = act.get("action")
                if action_type == "gsettings":
                    schema = act.get("schema")
                    key = act.get("key")
                    val = str(act.get("value"))
                    logger.info(f"Running GSettings: set {schema} {key} {val}")
                    subprocess.run(["gsettings", "set", schema, key, val], capture_output=True)
                elif action_type == "shell":
                    cmd = act.get("command")
                    logger.info(f"Running shell command: {cmd}")
                    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif action_type == "dbus":
                    dest = act.get("destination")
                    path = act.get("path")
                    iface = act.get("interface")
                    method = act.get("method")
                    args = act.get("args", [])
                    logger.info(f"Invoking D-Bus call: {dest} {path} {iface}.{method}({args})")
                    try:
                        obj = self.session_bus.get_object(dest, path)
                        dbus_iface = dbus.Interface(obj, iface)
                        getattr(dbus_iface, method)(*args)
                    except Exception as de:
                        logger.error(f"D-Bus call execution failed: {de}")

        except Exception as e:
            logger.exception("Error executing GUI Agent instruction:")

if __name__ == '__main__':
    loop = GLib.MainLoop()
    service = GuiAgentService()
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Stopping Axon GUI Agent...")
        loop.quit()
