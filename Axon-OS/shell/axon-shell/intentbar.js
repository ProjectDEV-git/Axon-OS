import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

// ─── IntentBar ────────────────────────────────────────────────────────────────

export default class IntentBar {
    constructor(extension, spacesManager) {
        this._extension = extension;
        this._spacesManager = spacesManager;
        this._actor = null;
        this._entry = null;
        this._responseLabel = null;
        this._visible = false;
        this._session = null;
        this._keyPressId = null;
    }

    enable() {
        this._session = new Soup.Session();
        this._buildUI();
    }

    disable() {
        this.hide();

        if (this._actor) {
            this._actor.destroy();
            this._actor = null;
        }

        if (this._session) {
            this._session.abort();
            this._session = null;
        }

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
        this._callOllama(query);
    }

    _callOllama(prompt) {
        const currentSpace = this._spacesManager.getCurrentSpace();
        const spaceName = currentSpace ? currentSpace.name : 'Default';

        const systemPrompt =
            `You are Axon, an AI assistant integrated into the Axon OS desktop. ` +
            `The user is currently in the "${spaceName}" space. ` +
            `When the user asks to open an application or run a command, respond with a JSON object only: ` +
            `{"action":"open_app","app":"<app-name>"} or {"action":"run_command","command":"<shell-command>"}. ` +
            `For general questions or requests that are not app/command actions, respond with plain text.`;

        const body = JSON.stringify({
            model: 'llama3',
            prompt: prompt,
            system: systemPrompt,
            stream: false,
        });

        const message = Soup.Message.new('POST', 'http://localhost:11434/api/generate');
        message.set_request_body_from_bytes(
            'application/json',
            new GLib.Bytes(new TextEncoder().encode(body))
        );

        this._session.send_and_read_async(
            message,
            GLib.PRIORITY_DEFAULT,
            null,
            (session, result) => {
                try {
                    const bytes = session.send_and_read_finish(result);
                    const decoder = new TextDecoder('utf-8');
                    const text = decoder.decode(bytes.get_data());
                    const json = JSON.parse(text);
                    const responseText = json.response || '';

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

                    this._setResponse(responseText.trim() || '(no response)');
                } catch (e) {
                    logError(e, 'AxonShell: Ollama call failed');
                    this._setResponse(`Error: ${e.message}`);
                }
            }
        );
    }

    _executeAction(action) {
        try {
            if (action.action === 'open_app' && action.app) {
                GLib.spawn_command_line_async(`gtk-launch ${action.app}`);
                this._setResponse(`Opening ${action.app}…`);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1200, () => {
                    this.hide();
                    return GLib.SOURCE_REMOVE;
                });
            } else if (action.action === 'run_command' && action.command) {
                GLib.spawn_command_line_async(action.command);
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
