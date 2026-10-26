import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Atk from 'gi://Atk';
import Pango from 'gi://Pango';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {confirmAndRun, launchAppById} from './ai-actions.js';
import Shell from 'gi://Shell';
import {
    EXAMPLE_PROMPTS,
    RequestTracker,
    SETTINGS_PANELS,
    STAGES,
    buildSuggestions,
    defaultSuggestionIndex,
    describeAction,
    describeSuggestion,
    escapeAction,
    formatCommandOutput,
    intentBarWidth,
    motionDuration,
    parseAction,
    pushHistory,
    recentForEmptyState,
    routingOrigin,
    splitError,
} from './ui-logic.js';

// Animation length; 0 when the user turned animations off (reduced motion)
const motion = ms => motionDuration(ms, St.Settings.get().enable_animations);

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
        this._emptyBox = null;
        this._originLabel = null;
        this._suggestBox = null;
        this._previewLabel = null;
        // Suggestions for what is typed, the selected row, and an AI action
        // waiting for Enter
        this._suggestions = [];
        this._selected = -1;
        this._apps = [];
        this._pendingAction = null;
        // Queries from this session, newest first, for the empty state
        this._history = [];
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
        this._emptyBox = null;
        this._originLabel = null;
        this._suggestBox = null;
        this._previewLabel = null;
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

        // Where requests go ("Local · llama3.2:3b" / "Cloud · OpenAI · gpt-4.1").
        // Refreshed on every show(); never assumed local.
        this._originLabel = new St.Label({
            style_class: 'axon-origin-label',
            text: 'Model: checking…',
            x_align: Clutter.ActorAlign.END,
        });
        this._actor.add_child(this._originLabel);

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
                this._onEnter();
                return Clutter.EVENT_STOP;
            }
            if ((key === Clutter.KEY_Up || key === Clutter.KEY_Down) && this._suggestions.length) {
                this._moveSelection(key === Clutter.KEY_Up ? -1 : 1);
                return Clutter.EVENT_STOP;
            }
            if (key === Clutter.KEY_Escape) {
                // Escape drops a waiting action, then stops a running request;
                // only after that does it close the bar
                if (this._pendingAction) {
                    this._pendingAction = null;
                    this._setResponse('Cancelled');
                } else if (escapeAction(this._requests.busy) === 'cancel') {
                    this._cancelRequest();
                } else {
                    this.hide();
                }
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

        // Shown before the user types: examples, recent queries, shortcuts
        this._emptyBox = new St.BoxLayout({
            style_class: 'axon-intentbar-empty',
            vertical: true,
            x_expand: true,
        });
        this._entry.clutter_text.connect('text-changed', () => {
            this._pendingAction = null; // typing changes the request
            this._updateSuggestions();
            this._updateEmptyState();
        });

        // Suggestions while typing, and what Enter will do
        this._suggestBox = new St.BoxLayout({
            style_class: 'axon-suggestions',
            vertical: true,
            x_expand: true,
            visible: false,
            accessible_name: 'Suggestions',
        });
        this._previewLabel = new St.Label({
            style_class: 'axon-hint-label',
            text: '',
            visible: false,
        });

        this._actor.add_child(entryRow);
        this._actor.add_child(this._suggestBox);
        this._actor.add_child(this._previewLabel);
        this._actor.add_child(this._emptyBox);
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
        this._pendingAction = null;
        this._apps = this._installedApps();
        this._buildEmptyState();
        this._updateEmptyState();
        this._refreshOrigin();

        this._positionCenter();
        this._actor.show();

        // Animate in: fade + slight scale
        this._actor.set_opacity(0);
        this._actor.ease({
            opacity: 255,
            duration: motion(150),
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
            duration: motion(100),
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
        // The monitor the user is on (pointer/focus), not always the primary
        const monitor = Main.layoutManager.currentMonitor || Main.layoutManager.primaryMonitor;
        if (!monitor) return;

        const width = intentBarWidth(monitor.width);
        const x = monitor.x + Math.floor((monitor.width - width) / 2);
        const y = monitor.y + Math.floor(monitor.height * 0.28);

        this._actor.set_position(x, y);
        this._actor.set_width(width);
    }

    // ── Submit / AI call ───────────────────────────────────────────────────────

    // ── Suggestions ────────────────────────────────────────────────────────────

    _installedApps() {
        try {
            return Shell.AppSystem.get_default().get_installed()
                .filter(info => info.should_show())
                .map(info => [info.get_id(), info.get_display_name() || info.get_name(),
                    typeof info.get_keywords === 'function' ? (info.get_keywords() || []).join(' ') : '']);
        } catch (e) {
            logError(e, 'AxonShell: could not list apps');
            return [];
        }
    }

    _updateSuggestions() {
        if (!this._suggestBox)
            return;
        const spaces = this._spacesManager.getSpaces().map(s => s.name);
        this._suggestions = this._requests.busy
            ? [] : buildSuggestions(this._entry.get_text(), this._apps, spaces);
        this._selected = this._suggestions.length ? defaultSuggestionIndex(this._suggestions) : -1;
        this._renderSuggestions();
    }

    _renderSuggestions() {
        this._suggestBox.destroy_all_children();
        let category = null;
        this._suggestions.forEach((s, i) => {
            if (s.category !== category) {
                category = s.category;
                this._suggestBox.add_child(new St.Label({style_class: 'axon-hint-label', text: category}));
            }
            const row = new St.BoxLayout({vertical: false, x_expand: true});
            row.add_child(new St.Label({text: s.title, x_expand: true, style_class: 'axon-suggestion-title'}));
            row.add_child(new St.Label({text: s.subtitle, style_class: 'axon-hint-label'}));
            const button = new St.Button({
                style_class: i === this._selected ? 'axon-suggestion axon-suggestion-selected' : 'axon-suggestion',
                child: row,
                x_expand: true,
                accessible_name: `${s.category}: ${s.title}. ${s.subtitle}`,
            });
            button.connect('clicked', () => {
                this._selected = i;
                this._onEnter();
            });
            this._suggestBox.add_child(button);
        });
        this._suggestBox.visible = this._suggestions.length > 0;
        const selected = this._suggestions[this._selected];
        this._previewLabel.visible = Boolean(selected);
        this._previewLabel.set_text(selected ? `Enter: ${describeSuggestion(selected)}` : '');
    }

    _moveSelection(step) {
        this._selected = Math.max(0, Math.min(this._selected + step, this._suggestions.length - 1));
        this._renderSuggestions();
    }

    _hideSuggestions() {
        this._suggestions = [];
        this._selected = -1;
        if (this._suggestBox) {
            this._suggestBox.destroy_all_children();
            this._suggestBox.visible = false;
            this._previewLabel.visible = false;
        }
    }

    _onEnter() {
        // Enter confirms an AI action shown as a preview
        if (this._pendingAction) {
            const action = this._pendingAction;
            this._pendingAction = null;
            this._runConfirmedAction(action);
            return;
        }
        const selected = this._suggestions[this._selected];
        if (selected && selected.action.type !== 'ask') {
            this._history = pushHistory(this._history, this._entry.get_text());
            this._runSuggestion(selected);
            return;
        }
        this._hideSuggestions();
        this._onSubmit();
    }

    _runSuggestion(s) {
        const a = s.action;
        this._hideSuggestions();
        if (a.type === 'open_app') {
            if (launchAppById(a.app_id))
                this.hide();
            else
                this._setResponse(`Couldn't open ${s.title}.`, {error: true});
        } else if (a.type === 'settings') {
            // Only the fixed panel names, never text from the query
            if (!SETTINGS_PANELS.some(([, panel]) => panel === a.panel))
                return;
            try {
                Gio.Subprocess.new(['gnome-control-center', a.panel], Gio.SubprocessFlags.NONE);
                this.hide();
            } catch (e) {
                this._setResponse("GNOME Settings isn't available.", {error: true});
            }
        } else if (a.type === 'space') {
            this.hide();
            this._spacesManager.switchToSpace(a.index);
        } else if (a.type === 'command') {
            this._runCommand(a.command);
        } else if (a.type === 'files') {
            this._searchFiles(a.query);
        }
    }

    _runCommand(command) {
        const shown = confirmAndRun(command, '', out => {
            this._setResponse(out === null ? 'Not run.' : formatCommandOutput(out));
        });
        if (!shown) {
            this._setResponse('Blocked: command not in allowlist or contains unsafe characters.',
                {error: true});
            return;
        }
        this._setResponse(`${STAGES.confirm}: ${command}`);
    }

    // Semantic file search through org.axonos.Search; results open on click.
    _searchFiles(query) {
        const requestId = this._requests.start();
        this._setBusy(true);
        this._setResponse(STAGES.searching);
        Gio.DBus.session.call(
            'org.axonos.Search', '/org/axonos/Search', 'org.axonos.Search', 'Query',
            new GLib.Variant('(si)', [query, 6]), null, Gio.DBusCallFlags.NONE, 30000, null,
            (conn, res) => {
                if (!this._requests.finish(requestId))
                    return;
                this._setBusy(false);
                let results = [];
                try {
                    const [json] = conn.call_finish(res).deepUnpack();
                    results = JSON.parse(json);
                } catch (e) {
                    this._setResponse(`Semantic search unavailable: ${e.message}`, {error: true});
                    return;
                }
                this._showFileResults(query, results);
            });
    }

    _showFileResults(query, results) {
        if (!results.length) {
            this._setResponse(`No matching files for "${query}".`);
            return;
        }
        this._setResponse(`Top ${results.length} matches. Click to open:`);
        const home = GLib.get_home_dir();
        this._suggestBox.destroy_all_children();
        for (const item of results) {
            const path = String(item.path ?? '');
            const shownPath = home && path.startsWith(home) ? `~${path.slice(home.length)}` : path;
            const button = new St.Button({
                style_class: 'axon-suggestion',
                label: shownPath,
                x_expand: true,
                accessible_name: `Open ${shownPath}`,
            });
            button.connect('clicked', () => {
                try {
                    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(),
                        global.create_app_launch_context(0, -1));
                    this.hide();
                } catch (e) {
                    this._setResponse(`Couldn't open ${shownPath}: ${e.message}`, {error: true});
                }
            });
            this._suggestBox.add_child(button);
        }
        this._suggestBox.visible = true;
    }

    _onSubmit() {
        const query = this._entry.get_text().trim();
        if (!query) return;

        this._history = pushHistory(this._history, query);
        const requestId = this._requests.start();
        this._setBusy(true);
        // Only stages this code reaches: the proxy may still need creating
        const connected = this._brainProxy || this._extension._brainProxy;
        this._setResponse(connected ? STAGES.waiting : STAGES.connecting);
        this._callBrain(query, requestId);
    }

    _refreshOrigin() {
        const proxy = this._extension._brainProxy;
        const show = origin => {
            if (this._originLabel)
                this._originLabel.set_text(origin.label);
        };
        if (!proxy || typeof proxy.GetModelSettingsRemote !== 'function') {
            show(routingOrigin('', {}, null));
            return;
        }
        proxy.GetModelSettingsRemote((tiersResult, tiersError) => {
            let tiers = {};
            try {
                if (!tiersError && tiersResult)
                    tiers = JSON.parse(tiersResult[0]);
            } catch (_e) {
                tiers = {};
            }
            proxy.ListProvidersRemote((provResult, provError) => {
                let providers = null;
                try {
                    if (!provError && provResult)
                        providers = JSON.parse(provResult[0]).providers ?? null;
                } catch (_e) {
                    providers = null;
                }
                // The Shell bar sends model '' so the Brain picks a tier
                show(routingOrigin('', tiers, providers));
            });
        });
    }

    _buildEmptyState() {
        this._emptyBox.destroy_all_children();
        const sections = [['Try', EXAMPLE_PROMPTS], ['Recent', recentForEmptyState(this._history)]];
        for (const [title, items] of sections) {
            if (items.length === 0)
                continue;
            this._emptyBox.add_child(new St.Label({style_class: 'axon-hint-label', text: title}));
            const row = new St.BoxLayout({style_class: 'axon-chips-row', vertical: false});
            for (const text of items) {
                const chip = new St.Button({
                    style_class: 'axon-chip',
                    label: text,
                    can_focus: true,
                    accessible_name: `Use example: ${text}`,
                });
                chip.connect('clicked', () => {
                    this._entry.set_text(text);
                    this._entry.grab_key_focus();
                    this._entry.clutter_text.set_cursor_position(-1);
                });
                row.add_child(chip);
            }
            this._emptyBox.add_child(row);
        }
        this._emptyBox.add_child(new St.Label({
            style_class: 'axon-hint-label axon-hint-bar',
            text: 'Enter  Run    Esc  Stop / Close',
        }));
    }

    _updateEmptyState() {
        if (!this._emptyBox)
            return;
        const idle = !this._entry.get_text() && !this._requests.busy &&
            !this._responseScroll.visible;
        this._emptyBox.visible = idle;
    }

    _setBusy(busy) {
        if (this._stopButton)
            this._stopButton.visible = busy;
        if (busy)
            this._hideSuggestions();
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
                    if (this._requests.isCurrent(requestId))
                        this._setResponse(STAGES.waiting);
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
                // Show what will happen and wait for Enter (Esc cancels)
                this._pendingAction = action;
                this._setResponse(`${describeAction(action)} Press Enter to confirm, Esc to cancel.`);
            } else if (action.action === 'run_command' && action.command) {
                // The confirmation card is the preview; output shows here
                this._runCommand(action.command);
            } else {
                this._setResponse('Unknown action received from AI.', {error: true});
            }
        } catch (e) {
            logError(e, 'AxonShell: could not execute action');
            this._setResponse(`Failed to execute: ${e.message}`, {error: true});
        }
    }

    _runConfirmedAction(action) {
        const appName = String(action.app ?? '').trim();
        if (!launchAppById(appName)) {
            this._setResponse(`No installed app called "${appName}".`, {error: true});
            return;
        }
        this.hide();
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
        this._updateEmptyState();
        // Re-center after content change
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._positionCenter();
            return GLib.SOURCE_REMOVE;
        });
    }
}
