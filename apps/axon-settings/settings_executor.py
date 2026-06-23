import json
import subprocess

import dbus


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
            from axon_logger import configure_app_logger
            logger = configure_app_logger(__name__)
            logger.exception("[axon-settings] DBus connection error: %s", e)
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
            "5. Display settings:\n"
            "   {\"action\": \"set_display\", \"setting\": \"night_light\", \"value\": true | false}\n"
            "   {\"action\": \"set_display\", \"setting\": \"color_temperature\", \"value\": 1000-10000 (integer)}\n"
            "   {\"action\": \"set_display\", \"setting\": \"scaling\", \"value\": 0.5-3.0 (float)}\n\n"
            "6. Bluetooth controls:\n"
            "   {\"action\": \"set_bluetooth\", \"setting\": \"power\", \"value\": true | false}\n"
            "   {\"action\": \"set_bluetooth\", \"setting\": \"discoverable\", \"value\": true | false}\n\n"
            "7. Power settings:\n"
            "   {\"action\": \"set_power\", \"setting\": \"sleep_timeout\", \"value\": 0-3600 (seconds, 0=never)}\n"
            "   {\"action\": \"set_power\", \"setting\": \"power_button\", \"value\": \"suspend\" | \"hibernate\" | \"nothing\" | \"interactive\"}\n\n"
            "8. Input settings:\n"
            "   {\"action\": \"set_input\", \"setting\": \"natural_scroll\", \"value\": true | false}\n"
            "   {\"action\": \"set_input\", \"setting\": \"tap_to_click\", \"value\": true | false}\n"
            "   {\"action\": \"set_input\", \"setting\": \"mouse_speed\", \"value\": -1.0 to 1.0 (float)}\n\n"
            "9. Lock screen settings:\n"
            "   {\"action\": \"set_lockscreen\", \"setting\": \"auto_lock\", \"value\": true | false}\n"
            "   {\"action\": \"set_lockscreen\", \"setting\": \"lock_delay\", \"value\": 0-3600 (seconds)}\n\n"
            "10. Notification settings:\n"
            "   {\"action\": \"set_notifications\", \"setting\": \"dnd\", \"value\": true | false}\n\n"
            "11. Unrecognized queries:\n"
            "   {\"action\": \"unknown\", \"message\": \"Brief explanation of what commands are supported\"}\n\n"
            "Examples:\n"
            "- \"make it dark\" -> {\"action\": \"set_theme\", \"value\": \"dark\"}\n"
            "- \"turn off wifi\" -> {\"action\": \"set_wifi\", \"value\": false}\n"
            "- \"mute sound\" -> {\"action\": \"set_volume\", \"value\": \"mute\"}\n"
            "- \"enable night light\" -> {\"action\": \"set_display\", \"setting\": \"night_light\", \"value\": true}\n"
            "- \"turn on bluetooth\" -> {\"action\": \"set_bluetooth\", \"setting\": \"power\", \"value\": true}\n"
            "- \"set sleep timeout to 5 minutes\" -> {\"action\": \"set_power\", \"setting\": \"sleep_timeout\", \"value\": 300}\n"
            "- \"enable natural scrolling\" -> {\"action\": \"set_input\", \"setting\": \"natural_scroll\", \"value\": true}\n"
            "- \"enable do not disturb\" -> {\"action\": \"set_notifications\", \"setting\": \"dnd\", \"value\": true}\n"
            "- \"disable auto lock\" -> {\"action\": \"set_lockscreen\", \"setting\": \"auto_lock\", \"value\": false}"
        )

        try:
            response_str = str(
                self._brain.Generate(f"{system_prompt}\n\nUser request: {query}", "", "", False)
            )
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
            elif action == "set_display":
                return self._set_display(data.get("setting"), value)
            elif action == "set_bluetooth":
                return self._set_bluetooth(data.get("setting"), value)
            elif action == "set_power":
                return self._set_power(data.get("setting"), value)
            elif action == "set_input":
                return self._set_input(data.get("setting"), value)
            elif action == "set_lockscreen":
                return self._set_lockscreen(data.get("setting"), value)
            elif action == "set_notifications":
                return self._set_notifications(data.get("setting"), value)
            else:
                msg = data.get("message", "I can help you adjust theme, volume, Wi-Fi, brightness, display, bluetooth, power, input, lock screen, and notifications. Try saying 'turn off wifi' or 'enable night light'.")
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
        except Exception:
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

    def _set_display(self, setting: str, value) -> dict:
        try:
            if setting == "night_light":
                state = "true" if value else "false"
                subprocess.run(
                    ["gsettings", "set", "org.gnome.settings-daemon.plugins.color",
                     "night-light-enabled", state],
                    check=True,
                )
                label = "enabled" if value else "disabled"
                return {"success": True, "message": f"Night light {label}."}

            elif setting == "color_temperature":
                temp = int(value)
                if not (1000 <= temp <= 10000):
                    return {"success": False, "message": "Color temperature must be between 1000 and 10000."}
                subprocess.run(
                    ["gsettings", "set", "org.gnome.settings-daemon.plugins.color",
                     "night-light-temperature", str(temp)],
                    check=True,
                )
                return {"success": True, "message": f"Color temperature set to {temp}K."}

            elif setting == "scaling":
                factor = float(value)
                if not (0.5 <= factor <= 3.0):
                    return {"success": False, "message": "Scaling factor must be between 0.5 and 3.0."}
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.interface",
                     "text-scaling-factor", str(factor)],
                    check=True,
                )
                return {"success": True, "message": f"Text scaling factor set to {factor}."}

            else:
                return {"success": False, "message": "Unknown display setting. Supported: night_light, color_temperature, scaling."}
        except subprocess.SubprocessError as e:
            return {"success": False, "message": f"Failed to modify display setting: {e}"}

    def _set_bluetooth(self, setting: str, value) -> dict:
        try:
            if setting == "power":
                state = "on" if value else "off"
                subprocess.run(["bluetoothctl", "power", state], check=True)
                return {"success": True, "message": f"Bluetooth powered {state}."}

            elif setting == "discoverable":
                state = "on" if value else "off"
                subprocess.run(["bluetoothctl", "discoverable", state], check=True)
                label = "enabled" if value else "disabled"
                return {"success": True, "message": f"Bluetooth discoverability {label}."}

            else:
                return {"success": False, "message": "Unknown bluetooth setting. Supported: power, discoverable."}
        except subprocess.SubprocessError as e:
            # Fallback: try rfkill for power toggling
            if setting == "power":
                try:
                    action = "unblock" if value else "block"
                    subprocess.run(["rfkill", action, "bluetooth"], check=True)
                    state = "on" if value else "off"
                    return {"success": True, "message": f"Bluetooth powered {state} (via rfkill)."}
                except subprocess.SubprocessError:
                    pass
            return {"success": False, "message": f"Failed to modify bluetooth setting: {e}"}

    def _set_power(self, setting: str, value) -> dict:
        try:
            if setting == "sleep_timeout":
                seconds = int(value)
                if not (0 <= seconds <= 3600):
                    return {"success": False, "message": "Sleep timeout must be between 0 and 3600 seconds."}
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.session",
                     "idle-delay", str(seconds)],
                    check=True,
                )
                label = "disabled" if seconds == 0 else f"set to {seconds} seconds"
                return {"success": True, "message": f"Sleep timeout {label}."}

            elif setting == "power_button":
                valid_actions = ("suspend", "hibernate", "nothing", "interactive")
                if value not in valid_actions:
                    return {"success": False, "message": f"Invalid power button action. Choose from: {', '.join(valid_actions)}."}
                subprocess.run(
                    ["gsettings", "set", "org.gnome.settings-daemon.plugins.power",
                     "power-button-action", value],
                    check=True,
                )
                return {"success": True, "message": f"Power button action set to '{value}'."}

            else:
                return {"success": False, "message": "Unknown power setting. Supported: sleep_timeout, power_button."}
        except subprocess.SubprocessError as e:
            return {"success": False, "message": f"Failed to modify power setting: {e}"}

    def _set_input(self, setting: str, value) -> dict:
        try:
            if setting == "natural_scroll":
                state = "true" if value else "false"
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.peripherals.touchpad",
                     "natural-scroll", state],
                    check=True,
                )
                label = "enabled" if value else "disabled"
                return {"success": True, "message": f"Natural scrolling {label}."}

            elif setting == "tap_to_click":
                state = "true" if value else "false"
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.peripherals.touchpad",
                     "tap-to-click", state],
                    check=True,
                )
                label = "enabled" if value else "disabled"
                return {"success": True, "message": f"Tap-to-click {label}."}

            elif setting == "mouse_speed":
                speed = float(value)
                if not (-1.0 <= speed <= 1.0):
                    return {"success": False, "message": "Mouse speed must be between -1.0 and 1.0."}
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.peripherals.mouse",
                     "speed", str(speed)],
                    check=True,
                )
                return {"success": True, "message": f"Mouse speed set to {speed}."}

            else:
                return {"success": False, "message": "Unknown input setting. Supported: natural_scroll, tap_to_click, mouse_speed."}
        except subprocess.SubprocessError as e:
            return {"success": False, "message": f"Failed to modify input setting: {e}"}

    def _set_lockscreen(self, setting: str, value) -> dict:
        try:
            if setting == "auto_lock":
                state = "true" if value else "false"
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.screensaver",
                     "lock-enabled", state],
                    check=True,
                )
                label = "enabled" if value else "disabled"
                return {"success": True, "message": f"Auto-lock {label}."}

            elif setting == "lock_delay":
                seconds = int(value)
                if not (0 <= seconds <= 3600):
                    return {"success": False, "message": "Lock delay must be between 0 and 3600 seconds."}
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.screensaver",
                     "lock-delay", str(seconds)],
                    check=True,
                )
                return {"success": True, "message": f"Lock delay set to {seconds} seconds."}

            else:
                return {"success": False, "message": "Unknown lock screen setting. Supported: auto_lock, lock_delay."}
        except subprocess.SubprocessError as e:
            return {"success": False, "message": f"Failed to modify lock screen setting: {e}"}

    def _set_notifications(self, setting: str, value) -> dict:
        try:
            if setting == "dnd":
                # DND = hide banners, so invert: dnd=true means show-banners=false
                state = "false" if value else "true"
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.notifications",
                     "show-banners", state],
                    check=True,
                )
                label = "enabled" if value else "disabled"
                return {"success": True, "message": f"Do Not Disturb {label}."}

            else:
                return {"success": False, "message": "Unknown notification setting. Supported: dnd."}
        except subprocess.SubprocessError as e:
            return {"success": False, "message": f"Failed to modify notification setting: {e}"}
