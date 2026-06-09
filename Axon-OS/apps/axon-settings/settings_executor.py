import json
import subprocess
import dbus
from pathlib import Path

class SettingsExecutor:
    def __init__(self):
        self._bus = None
        self._brain = None
        self._connect()

    def _connect(self):
        try:
            self._bus = dbus.SessionBus()
            brain_obj = self._bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            self._brain = dbus.Interface(brain_obj, "org.axonos.Brain")
        except Exception as e:
            print(f"[axon-settings] DBus connection error: {e}")
            self._brain = None

    def execute_command(self, query: str) -> dict:
        """Parses the natural language query using the Brain service and executes system modifications."""
        if not self._brain:
            self._connect()
            if not self._brain:
                return {"success": False, "message": "Brain service offline. Cannot process natural language."}

        system_prompt = (
            "You are the Axon OS Settings Router. Analyze the user request and map it to a structured system action.\n"
            "Respond ONLY with a valid JSON object matching one of the following schemas:\n\n"
            "1. Theme modification:\n"
            "   {\"action\": \"set_theme\", \"value\": \"dark\" | \"light\"}\n\n"
            "2. Volume adjustments:\n"
            "   {\"action\": \"set_volume\", \"value\": 0-100 (integer) | \"mute\" | \"unmute\"}\n\n"
            "3. Wi-Fi controls:\n"
            "   {\"action\": \"set_wifi\", \"value\": true | false}\n\n"
            "4. Brightness controls:\n"
            "   {\"action\": \"set_brightness\", \"value\": 0-100 (integer)}\n\n"
            "5. Unrecognized queries:\n"
            "   {\"action\": \"unknown\", \"message\": \"Brief explanation of what commands are supported\"}\n\n"
            "Examples:\n"
            "- \"make it dark\" -> {\"action\": \"set_theme\", \"value\": \"dark\"}\n"
            "- \"turn off wifi\" -> {\"action\": \"set_wifi\", \"value\": false}\n"
            "- \"mute sound\" -> {\"action\": \"set_volume\", \"value\": \"mute\"}"
        )

        try:
            response_str = str(self._brain.Generate(query, "", "", False))
            response_str = response_str.strip()
            if response_str.startswith("```"):
                response_str = response_str.replace("```json", "").replace("```", "").strip()
            
            data = json.loads(response_str)
            action = data.get("action")
            value = data.get("value")

            if action == "set_theme":
                return self._set_theme(value)
            elif action == "set_volume":
                return self._set_volume(value)
            elif action == "set_wifi":
                return self._set_wifi(value)
            elif action == "set_brightness":
                return self._set_brightness(value)
            else:
                msg = data.get("message", "I can help you adjust theme (dark/light), volume, Wi-Fi, and screen brightness. Try saying 'turn off wifi' or 'make it dark'.")
                return {"success": False, "message": msg}

        except Exception as e:
            return {"success": False, "message": f"Failed to parse action: {e}"}

    def _set_theme(self, value: str) -> dict:
        if value not in ("dark", "light"):
            return {"success": False, "message": "Invalid theme setting. Use 'dark' or 'light'."}

        scheme = "prefer-dark" if value == "dark" else "prefer-light"
        gtk_theme = "axon-gtk" if value == "dark" else "Adwaita" # Fallback to default light theme

        try:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme], check=True)
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", gtk_theme], check=True)
            return {"success": True, "message": f"System theme switched to {value} mode successfully."}
        except subprocess.SubprocessError as e:
            return {"success": False, "message": f"Failed to modify theme setting: {e}"}

    def _set_volume(self, value) -> dict:
        try:
            if value == "mute":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], check=True)
                return {"success": True, "message": "System audio output muted."}
            elif value == "unmute":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=True)
                return {"success": True, "message": "System audio output unmuted."}
            else:
                vol = int(value)
                if not (0 <= vol <= 100):
                    raise ValueError("Volume must be between 0 and 100")
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{vol}%"], check=True)
                return {"success": True, "message": f"Volume adjusted to {vol}%."}
        except Exception as e:
            # Fallback to amixer if pactl fails
            try:
                if value == "mute":
                    subprocess.run(["amixer", "set", "Master", "mute"], check=True)
                    return {"success": True, "message": "System audio output muted."}
                elif value == "unmute":
                    subprocess.run(["amixer", "set", "Master", "unmute"], check=True)
                    return {"success": True, "message": "System audio output unmuted."}
                else:
                    vol = int(value)
                    subprocess.run(["amixer", "set", "Master", f"{vol}%"], check=True)
                    return {"success": True, "message": f"Volume adjusted to {vol}%."}
            except subprocess.SubprocessError as ae:
                return {"success": False, "message": f"Failed to modify audio settings: {ae}"}

    def _set_wifi(self, value: bool) -> dict:
        state = "on" if value else "off"
        try:
            subprocess.run(["nmcli", "radio", "wifi", state], check=True)
            return {"success": True, "message": f"Wi-Fi connection turned {state}."}
        except subprocess.SubprocessError as e:
            return {"success": False, "message": f"Failed to toggle Wi-Fi radio: {e}"}

    def _set_brightness(self, value: int) -> dict:
        try:
            vol = int(value)
            if not (0 <= vol <= 100):
                raise ValueError("Brightness must be between 0 and 100")
            
            # Try brightnessctl first
            try:
                subprocess.run(["brightnessctl", "set", f"{vol}%"], check=True)
                return {"success": True, "message": f"Display brightness set to {vol}%."}
            except (subprocess.SubprocessError, FileNotFoundError):
                pass

            # Try GNOME power settings D-Bus interface
            bus = dbus.SessionBus()
            power_obj = bus.get_object("org.gnome.SettingsDaemon.Power", "/org/gnome/SettingsDaemon/Power")
            screen_iface = dbus.Interface(power_obj, "org.gnome.SettingsDaemon.Power.Screen")
            screen_iface.SetPercentage(vol)
            return {"success": True, "message": f"Display brightness adjusted to {vol}%."}
        except Exception as e:
            return {"success": False, "message": f"Failed to adjust screen brightness: {e}"}
