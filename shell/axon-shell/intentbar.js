import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Atk from 'gi://Atk';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

function logError(error, message) {
    if (message) {
        console.error(`${message}: ${error.message || error}`);
    } else {
        console.error(error.message || error);
    }
}

// ─── IntentBar ────────────────────────────────────────────────────────────────

export default class IntentBar {
    constructor(extension, spacesManager) {
        this._extension = extension;
        this._spacesManager = spacesManager;
        this._actor = null;
        this._entry = null;
        this._responseLabel = null;
        this._visible = false;
        this._keyPressId = null;
        this._brainProxy = null;
    }

    enable() {
        this._brainProxy = this._extension._brainProxy || null;
        this._buildUI();
    }

    disable() {
        this.hide();

        if (this._actor) {
            this._actor.destroy();
            this._actor = null;
        }

        this._brainProxy = null;
        this._entry = null;
        this._responseLabel = null;
    }

    // ── UI construction ────────────────────────────────────────────────────────

    _buildUI() {
        // Outer container
        this._actor = new St.BoxLayout({
            style_class: 'axon-intentbar',
            vertical: true,
            reactive: true,
            visible: false,
            track_hover: true,
        });

        // Input field
        this._entry = new St.Entry({
            style_class: 'axon-intentbar-input',
            hint_text: 'What do you want to do?',
            can_focus: true,
            x_expand: true,
            accessible_name: 'Intent bar input',
            accessible_role: Atk.Role.ENTRY,
        });

        this._entry.clutter_text.connect('key-press-event', (actor, event) => {
            const key = event.get_key_symbol();
            if (key === Clutter.KEY_Return || key === Clutter.KEY_KP_Enter) {
                this._onSubmit();
                return Clutter.EVENT_STOP;
            }
            if (key === Clutter.KEY_Escape) {
                this.hide();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        // Response area
        this._responseLabel = new St.Label({
            style_class: 'axon-response',
            text: '',
            x_expand: true,
            accessible_name: 'AI response',
            accessible_role: Atk.Role.LABEL,
        });
        this._responseLabel.clutter_text.set_line_wrap(true);
        this._responseLabel.hide();

        this._actor.add_child(this._entry);
        this._actor.add_child(this._responseLabel);

        // Add to UI group (above all windows)
        Main.uiGroup.add_child(this._actor);
    }

    // ── Visibility ─────────────────────────────────────────────────────────────

    show() {
        if (this._visible) return;
        this._visible = true;

        this._responseLabel.set_text('');
        this._responseLabel.hide();
        this._entry.set_text('');

        this._positionCenter();
        this._actor.show();

        // Animate in: fade + slight scale
        this._actor.set_opacity(0);
        this._actor.ease({
            opacity: 255,
            duration: 150,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });

        // Grab keyboard focus
        this._actor.grab_key_focus();
        this._entry.grab_key_focus();
    }

    hide() {
        if (!this._visible) return;
        this._visible = false;

        this._actor.ease({
            opacity: 0,
            duration: 100,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                if (this._actor) this._actor.hide();
            },
        });
    }

    toggle() {
        if (this._visible) {
            this.hide();
        } else {
            this.show();
        }
    }

    _positionCenter() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;

        // Let the actor measure itself first
        const [minW, natW] = this._actor.get_preferred_width(-1);
        const [minH, natH] = this._actor.get_preferred_height(natW);

        const width = Math.max(natW, 600);
        const x = monitor.x + Math.floor((monitor.width - width) / 2);
        const y = monitor.y + Math.floor(monitor.height * 0.28);

        this._actor.set_position(x, y);
        this._actor.set_width(width);
    }

    // ── Submit / AI call ───────────────────────────────────────────────────────

    _onSubmit() {
        const query = this._entry.get_text().trim();
        if (!query) return;

        this._setResponse('Thinking…');
        this._callBrain(query);
    }

    _callBrain(prompt) {
        const currentSpace = this._spacesManager.getCurrentSpace();
        const spaceName = currentSpace ? currentSpace.name : 'Default';

        const systemPrompt =
            `You are Axon, an AI assistant integrated into the Axon OS desktop. ` +
            `The user is currently in the "${spaceName}" space. ` +
            `When the user asks to open an application or run a command, respond with a JSON object only: ` +
            `{"action":"open_app","app":"<app-name>"} or {"action":"run_command","command":"<shell-command>"}. ` +
            `For general questions or requests that are not app/command actions, respond with plain text.`;

        // Try Brain D-Bus proxy first
        if (this._brainProxy) {
            try {
                this._brainProxy.Generate(
                    prompt, systemPrompt, '', false,
                    (result, error) => {
                        if (error) {
                            logError(error, 'AxonShell: Brain Generate failed');
                            this._setResponse(`Error: ${error.message}`);
                            return;
                        }
                        this._handleResponse(result);
                    }
                );
                return;
            } catch (e) {
                logError(e, 'AxonShell: Brain proxy call failed, falling back');
            }
        }

        // Fallback: try to create proxy on the fly
        try {
            const BrainInterface = `
<node>
  <interface name="org.axonos.Brain">
    <method name="Generate">
      <arg type="s" name="prompt" direction="in"/>
      <arg type="s" name="context" direction="in"/>
      <arg type="s" name="model" direction="in"/>
      <arg type="b" name="stream" direction="in"/>
      <arg type="s" name="response" direction="out"/>
    </method>
  </interface>
</node>`;
            const BrainProxy = Gio.DBusProxy.makeProxyWrapper(BrainInterface);
            const proxy = new BrainProxy(
                Gio.DBus.session,
                'org.axonos.Brain',
                '/org/axonos/Brain'
            );
            proxy.Generate(
                prompt, systemPrompt, '', false,
                (result, error) => {
                    if (error) {
                        logError(error, 'AxonShell: Brain Generate failed');
                        this._setResponse(`Error: ${error.message}`);
                        return;
                    }
                    this._handleResponse(result);
                }
            );
        } catch (e) {
            logError(e, 'AxonShell: could not reach Brain service');
            this._setResponse('AI service unavailable. Is Axon Brain running?');
        }
    }

    _handleResponse(responseText) {
        if (!responseText) {
            this._setResponse('(no response)');
            return;
        }

        // Try to parse as action JSON
        try {
            const action = JSON.parse(responseText.trim());
            if (action && typeof action.action === 'string') {
                this._executeAction(action);
                return;
            }
        } catch (_) {
            // Not JSON — display as plain text
        }

        this._setResponse(responseText.trim());
    }

    _isCommandSafe(command) {
        if (!command || typeof command !== 'string') return false;
        const forbidden = ['|', ';', '&', '$', '`', '\\', '(', ')', '{', '}',
                           '<', '>', '*', '?', '~', '#', '!', '\n', '\r'];
        for (const ch of forbidden) {
            if (command.includes(ch)) return false;
        }
        const allowedBinaries = [
            'ls', 'cat', 'grep', 'find', 'echo', 'date', 'whoami', 'hostname',
            'uname', 'df', 'du', 'free', 'uptime', 'ps', 'pwd', 'wc', 'head',
            'tail', 'sort', 'uniq', 'diff', 'file', 'stat', 'xdg-open',
            'gtk-launch', 'notify-send', 'zenity', 'apt', 'apt-get', 'git',
            'make', 'systemctl', 'journalctl', 'nmcli', 'bluetoothctl', 'pactl',
        ];
        const parts = command.trim().split(/\s+/);
        const binary = parts[0];
        if (!binary) return false;
        return allowedBinaries.includes(binary);
    }

    _executeAction(action) {
        try {
            if (action.action === 'open_app' && action.app) {
                const appName = String(action.app).trim();
                if (!appName || /[;&$`\\(){}<>*?~#!]/.test(appName)) {
                    this._setResponse('Blocked: unsafe app name.');
                    return;
                }
                try {
                    let proc = new Gio.Subprocess({
                        argv: ['gtk-launch', appName],
                        flags: Gio.SubprocessFlags.NONE
                    });
                    proc.init(null);
                } catch(e) {
                    console.error(`Failed to launch ${appName}: ${e.message}`);
                }
                this._setResponse(`Opening ${appName}…`);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1200, () => {
                    this.hide();
                    return GLib.SOURCE_REMOVE;
                });
            } else if (action.action === 'run_command' && action.command) {
                if (!this._isCommandSafe(action.command)) {
                    this._setResponse('Blocked: command not in allowlist or contains unsafe characters.');
                    return;
                }
                try {
                    let [, argv] = GLib.shell_parse_argv(action.command);
                    if (argv) {
                        let proc = new Gio.Subprocess({
                            argv: argv,
                            flags: Gio.SubprocessFlags.NONE
                        });
                        proc.init(null);
                    }
                } catch(e) {
                    console.error(`Failed to run command: ${e.message}`);
                }
                this._setResponse(`Running: ${action.command}`);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1200, () => {
                    this.hide();
                    return GLib.SOURCE_REMOVE;
                });
            } else {
                this._setResponse('Unknown action received from AI.');
            }
        } catch (e) {
            logError(e, 'AxonShell: could not execute action');
            this._setResponse(`Failed to execute: ${e.message}`);
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    _setResponse(text) {
        if (!this._responseLabel) return;
        this._responseLabel.set_text(text);
        this._responseLabel.show();
        // Re-center after content change
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._positionCenter();
            return GLib.SOURCE_REMOVE;
        });
    }
}
