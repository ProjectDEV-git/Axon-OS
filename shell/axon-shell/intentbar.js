import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Atk from 'gi://Atk';
import Pango from 'gi://Pango';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {confirmAndRun, launchAppById} from './ai-actions.js';
import {RequestTracker, escapeAction, parseAction, splitError} from './ui-logic.js';

function logError(error, message) {
    if (message) {
        console.error(`${message}: ${error.message || error}`);
    } else {
        console.error(error.message || error);
    }
}

// Opens Axon Settings → Diagnostics (desktop action "diagnostics").
function openDiagnostics() {
    try {
        const info = Gio.DesktopAppInfo.new('axon-settings.desktop');
        if (info)
            info.launch_action('diagnostics', global.create_app_launch_context(0, -1));
    } catch (e) {
        logError(e, 'AxonShell: could not open Diagnostics');
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
        this._responseScroll = null;
        this._visible = false;
        this._keyPressId = null;
        this._brainProxy = null;
        this._stopButton = null;
        // The running Brain request; Escape or Stop cancels it
        this._requests = new RequestTracker();
    }

    enable() {
        this._buildUI();
    }

    disable() {
        this.hide();

        if (this._actor) {
            this._actor.destroy();
            this._actor = null;
        }

        this._brainProxy = null;
        this._stopButton = null;
        this._entry = null;
        this._responseLabel = null;
        this._responseScroll = null;
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
                // First Escape stops a running request; the next one closes
                if (escapeAction(this._requests.busy) === 'cancel')
                    this._cancelRequest();
                else
                    this.hide();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        // Visible only while a request is running
        this._stopButton = new St.Button({
            style_class: 'axon-intentbar-stop',
            child: new St.Icon({icon_name: 'process-stop-symbolic', icon_size: 16}),
            can_focus: true,
            visible: false,
            accessible_name: 'Stop request',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._stopButton.connect('clicked', () => this._cancelRequest());
        const entryRow = new St.BoxLayout({vertical: false, x_expand: true});
        entryRow.add_child(this._entry);
        entryRow.add_child(this._stopButton);

        // Response area
        this._responseLabel = new St.Label({
            style_class: 'axon-response',
            text: '',
            x_expand: true,
            accessible_name: 'AI response',
            accessible_role: Atk.Role.LABEL,
        });
        this._responseLabel.clutter_text.set_line_wrap(true);
        this._responseLabel.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;

        // Long answers scroll inside the bar (max-height in the stylesheet)
        // instead of growing it off the bottom of the screen.
        // Errors get an icon and a recovery button, not just a colour
        this._errorIcon = new St.Icon({
            icon_name: 'dialog-error-symbolic',
            icon_size: 16,
            style_class: 'axon-response-error-icon',
            visible: false,
            y_align: Clutter.ActorAlign.START,
        });
        this._recoveryButton = new St.Button({
            style_class: 'axon-chip',
            label: 'Diagnostics',
            can_focus: true,
            visible: false,
            y_align: Clutter.ActorAlign.START,
        });
        this._recoveryButton.connect('clicked', () => {
            this.hide();
            openDiagnostics();
        });
        const responseRow = new St.BoxLayout({vertical: false, x_expand: true});
        responseRow.add_child(this._errorIcon);
        responseRow.add_child(this._responseLabel);
        responseRow.add_child(this._recoveryButton);
        const responseBox = new St.BoxLayout({vertical: true, x_expand: true});
        responseBox.add_child(responseRow);
        this._responseScroll = new St.ScrollView({
            style_class: 'axon-response-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            x_expand: true,
        });
        this._responseScroll.set_child(responseBox);
        this._responseScroll.hide();

        this._actor.add_child(entryRow);
        this._actor.add_child(this._responseScroll);

        // Add to UI group (above all windows)
        Main.uiGroup.add_child(this._actor);
    }

    // ── Visibility ─────────────────────────────────────────────────────────────

    show() {
        if (this._visible) return;
        this._visible = true;

        this._responseLabel.set_text('');
        this._responseScroll.hide();
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
        // A reply that arrives after closing must not reopen or act
        if (this._requests.cancel())
            this._setBusy(false);

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
        if (!this._actor) return;
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

        const requestId = this._requests.start();
        this._setBusy(true);
        this._setResponse('Thinking…');
        this._callBrain(query, requestId);
    }

    _setBusy(busy) {
        if (this._stopButton)
            this._stopButton.visible = busy;
    }

    _cancelRequest() {
        // Generate(stream=false) can't be stopped server-side; the late
        // reply is dropped by the request id check instead.
        if (!this._requests.cancel())
            return;
        this._setBusy(false);
        this._setResponse('Cancelled');
    }

    // Wraps a Brain callback so replies to cancelled requests are ignored.
    _onBrainReply(requestId, result, error) {
        if (!this._requests.finish(requestId))
            return;
        this._setBusy(false);
        if (error) {
            logError(error, 'AxonShell: Brain Generate failed');
            this._setResponse(`Axon Brain failed: ${error.message}`, {error: true, recovery: true});
            return;
        }
        this._handleResponse(result ? result[0] : null);
    }

    _callBrain(prompt, requestId) {
        const currentSpace = this._spacesManager.getCurrentSpace();
        const spaceName = currentSpace ? currentSpace.name : 'Default';

        const systemPrompt =
            `You are Axon, an AI assistant integrated into the Axon OS desktop. ` +
            `The user is currently in the "${spaceName}" space. ` +
            `When the user asks to open an application or run a command, respond with a JSON object only: ` +
            `{"action":"open_app","app":"<app-name>"} or {"action":"run_command","command":"<shell-command>"}. ` +
            `For general questions or requests that are not app/command actions, respond with plain text.`;

        // Read the proxy at call time: the extension initializes it
        // asynchronously, so it may not exist yet when enable() ran.
        const brainProxy = this._brainProxy || this._extension._brainProxy || null;
        if (brainProxy) {
            try {
                brainProxy.GenerateRemote(
                    prompt, systemPrompt, '', false,
                    (result, error) => this._onBrainReply(requestId, result, error)
                );
                return;
            } catch (e) {
                logError(e, 'AxonShell: Brain proxy call failed, falling back');
            }
        }

        // Fallback: create a proxy on the fly (async — a sync constructor
        // would freeze the compositor while the service starts).
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
            new BrainProxy(
                Gio.DBus.session,
                'org.axonos.Brain',
                '/org/axonos/Brain',
                (proxy, error) => {
                    if (error) {
                        logError(error, 'AxonShell: could not reach Brain service');
                        if (this._requests.finish(requestId)) {
                            this._setBusy(false);
                            this._setResponse('AI service unavailable. Is Axon Brain running?',
                                {error: true, recovery: true});
                        }
                        return;
                    }
                    this._brainProxy = proxy;
                    proxy.GenerateRemote(
                        prompt, systemPrompt, '', false,
                        (result, err) => this._onBrainReply(requestId, result, err)
                    );
                }
            );
        } catch (e) {
            logError(e, 'AxonShell: could not reach Brain service');
            if (this._requests.finish(requestId)) {
                this._setBusy(false);
                this._setResponse('AI service unavailable. Is Axon Brain running?',
                                {error: true, recovery: true});
            }
        }
    }

    _handleResponse(responseText) {
        if (!responseText) {
            this._setResponse('(no response)');
            return;
        }

        // Actions may arrive bare or inside a ```json fence; never show the JSON
        const action = parseAction(responseText);
        if (action) {
            this._executeAction(action);
            return;
        }

        const {body, error} = splitError(responseText);
        if (error)
            this._setResponse(body ? `${body}\n\n${error}` : error, {error: true, recovery: true});
        else
            this._setResponse(body);
    }

    _executeAction(action) {
        try {
            if (action.action === 'open_app' && action.app) {
                const appName = String(action.app).trim();
                if (!launchAppById(appName)) {
                    this._setResponse(`No installed app called "${appName}".`, {error: true});
                    return;
                }
                this._setResponse(`Opening ${appName}…`);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1200, () => {
                    this.hide();
                    return GLib.SOURCE_REMOVE;
                });
            } else if (action.action === 'run_command' && action.command) {
                if (!confirmAndRun(action.command)) {
                    this._setResponse('Blocked: command not in allowlist or contains unsafe characters.',
                        {error: true});
                    return;
                }
                this._setResponse(`Asking before running: ${action.command}`);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1200, () => {
                    this.hide();
                    return GLib.SOURCE_REMOVE;
                });
            } else {
                this._setResponse('Unknown action received from AI.', {error: true});
            }
        } catch (e) {
            logError(e, 'AxonShell: could not execute action');
            this._setResponse(`Failed to execute: ${e.message}`, {error: true});
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    _setResponse(text, {error = false, recovery = false} = {}) {
        if (!this._responseLabel) return;
        this._responseLabel.set_text(text);
        this._errorIcon.visible = error;
        this._recoveryButton.visible = error && recovery;
        if (error)
            this._responseLabel.add_style_class_name('axon-response-error');
        else
            this._responseLabel.remove_style_class_name('axon-response-error');
        this._responseScroll.show();
        // Re-center after content change
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._positionCenter();
            return GLib.SOURCE_REMOVE;
        });
    }
}
