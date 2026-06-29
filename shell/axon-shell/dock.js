/**
 * Axon OS — Windows 10/11-style Taskbar
 * dock.js
 *
 * Transforms the floating dock into a full-width taskbar at the bottom.
 * Shows:
 *   - Start button linking to StartMenuPopup
 *   - Search button linking to IntentBar
 *   - Center-aligned app icons row (pinned and running) with underline active indicator
 *   - Right-aligned tray with native clock/calendar and Quick Settings (status menu)
 *   - Struts registration to prevent windows overlapping it
 *
 * GNOME Shell 45+ / GJS ESM
 */

import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Atk from 'gi://Atk';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import StartMenuPopup from './startmenu.js';

// ─── D-Bus interfaces ─────────────────────────────────────────────────────────

const BrainInterface = `
<node>
  <interface name="org.axonos.Brain">
    <method name="ClassifyIntent">
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="response" direction="out"/>
    </method>
    <method name="Generate">
      <arg type="s" name="prompt" direction="in"/>
      <arg type="s" name="context" direction="in"/>
      <arg type="s" name="model" direction="in"/>
      <arg type="b" name="stream" direction="in"/>
      <arg type="s" name="response" direction="out"/>
    </method>
  </interface>
</node>
`;

const ContextInterface = `
<node>
  <interface name="org.axonos.Context">
    <method name="GetContextString">
      <arg type="s" name="context_string" direction="out"/>
    </method>
  </interface>
</node>
`;

const BrainProxy = Gio.DBusProxy.makeProxyWrapper(BrainInterface);
const ContextProxy = Gio.DBusProxy.makeProxyWrapper(ContextInterface);

// ─── Constants ─────────────────────────────────────────────────────────────────

const TASKBAR_HEIGHT     = 48;   // px, taskbar height
const ICON_SIZE          = 32;   // px, taskbar icon size
const BOUNCE_PEAK        = 1.25; // bounce scale
const BOUNCE_DURATION    = 150;  // ms
const TRAMPOLINE_SCALE   = 0.90; // press scale
const TRAMPOLINE_DURATION = 100; // ms

// ─── Clock Label ───────────────────────────────────────────────────────────────

const TaskbarClock = GObject.registerClass(
class TaskbarClock extends St.Label {
    _init() {
        super._init({
            style_class: 'axon-taskbar-clock',
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
            accessible_name: 'Clock',
            accessible_role: Atk.Role.LABEL,
        });
        this._updateTime();
        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._updateTime();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _updateTime() {
        let now = GLib.DateTime.new_now_local();
        // Two-line layout: HH:MM AM/PM \n MM/DD/YYYY
        this.set_text(now.format('%I:%M %p\n%m/%d/%Y'));
    }

    destroy() {
        if (this._timerId) {
            GLib.source_remove(this._timerId);
            this._timerId = null;
        }
        super.destroy();
    }
});

// ─── DockIcon ──────────────────────────────────────────────────────────────────

const DockIcon = GObject.registerClass(
class DockIcon extends St.Button {
    _init(opts) {
        super._init({
            style_class: 'axon-dock-icon',
            reactive: true,
            track_hover: true,
            can_focus: true,
            accessible_name: opts.label ?? '',
        });

        this._app      = opts.app      ?? null;
        this._label    = opts.label    ?? '';
        this._callback = opts.callback ?? (() => {});
        this._dockManager = opts.dockManager ?? null;
        this._tooltipId = null;
        this._tooltip   = null;

        // Icon Image
        let iconWidget;
        if (this._app) {
            iconWidget = this._app.create_icon_texture(ICON_SIZE);
        } else if (opts.icon) {
            iconWidget = new St.Icon({
                gicon:     opts.icon,
                icon_size: ICON_SIZE,
                style_class: 'axon-taskbar-icon-svg',
            });
        } else {
            iconWidget = new St.Icon({
                icon_name: 'application-x-executable-symbolic',
                icon_size: ICON_SIZE,
            });
        }

        // Active/Running Indicator (Underline bar instead of dot)
        this._dot = new St.Widget({
            style_class: 'axon-dock-dot',
            visible: this._app !== null,
        });

        // Column cell layout: icon stacked above underline dot
        const cell = new St.BoxLayout({
            vertical:     true,
            x_align:      Clutter.ActorAlign.CENTER,
            y_align:      Clutter.ActorAlign.CENTER,
            x_expand:     true,
            y_expand:     true,
        });
        cell.add_child(iconWidget);
        cell.add_child(this._dot);
        this.set_child(cell);

        this.connect('clicked',      this._onClick.bind(this));
        this.connect('notify::hover', this._onHoverChanged.bind(this));
        this.connect('destroy',      this._onDestroy.bind(this));

        this.connect('button-press-event', (actor, event) => {
            let button = event.get_button();
            if (button === 3) { // Right click
                if (this._dockManager) {
                    this._dockManager.showIconContextMenu(this, event);
                }
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
    }

    setRunning(running) {
        this._dot.visible = running;
    }

    bounce() {
        this.ease({
            scale_y:  BOUNCE_PEAK,
            scale_x:  BOUNCE_PEAK,
            duration: BOUNCE_DURATION,
            mode:     Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                this.ease({
                    scale_y:  1.0,
                    scale_x:  1.0,
                    duration: BOUNCE_DURATION,
                    mode:     Clutter.AnimationMode.EASE_IN_OUT_BOUNCE,
                });
            },
        });
    }

    _onClick() {
        this.ease({
            scale_x:  TRAMPOLINE_SCALE,
            scale_y:  TRAMPOLINE_SCALE,
            duration: TRAMPOLINE_DURATION,
            mode:     Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                this.ease({
                    scale_x:  1.0,
                    scale_y:  1.0,
                    duration: TRAMPOLINE_DURATION,
                    mode:     Clutter.AnimationMode.EASE_OUT_BACK,
                });
            },
        });
        this._callback();
    }

    _onHoverChanged() {
        if (this.hover) {
            this._tooltipId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
                this._showTooltip();
                this._tooltipId = null;
                return GLib.SOURCE_REMOVE;
            });
        } else {
            if (this._tooltipId) {
                GLib.source_remove(this._tooltipId);
                this._tooltipId = null;
            }
            this._hideTooltip();
        }
    }

    _showTooltip() {
        if (this._tooltip) return;
        if (!this._label) return;

        this._tooltip = new St.Label({
            style_class: 'axon-dock-tooltip',
            text:        this._label,
        });
        Main.uiGroup.add_child(this._tooltip);

        const [iconX, iconY] = this.get_transformed_position();
        const [, natW]       = this._tooltip.get_preferred_width(-1);
        const [, natH]       = this._tooltip.get_preferred_height(-1);
        const x = Math.round(iconX + (this.width - natW) / 2);
        const y = Math.round(iconY - natH - 6);

        this._tooltip.set_position(x, y);
        this._tooltip.set_opacity(0);
        this._tooltip.ease({
            opacity:  255,
            duration: 120,
            mode:     Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    _hideTooltip() {
        if (!this._tooltip) return;
        const t = this._tooltip;
        this._tooltip = null;
        t.ease({
            opacity:    0,
            duration:   80,
            mode:       Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => t.destroy(),
        });
    }

    _onDestroy() {
        if (this._tooltipId) {
            GLib.source_remove(this._tooltipId);
            this._tooltipId = null;
        }
        this._hideTooltip();
    }
});

// ─── DockManager / TaskbarManager ──────────────────────────────────────────────

export default class DockManager {
    constructor(extension, intentBar) {
        this._extension  = extension;
        this._intentBar  = intentBar;

        this._actor         = null;
        this._iconRow       = null;
        this._iconMap       = new Map();  // Shell.App → DockIcon
        this._visible       = false;

        this._appSystem          = Shell.AppSystem.get_default();
        this._appsChangedId      = null;
        this._windowCreatedId    = null;

        this._contextMenu        = null;
        this._clickDismissId     = null;
        this._brainProxy         = null;
        this._contextProxy       = null;

        this._startMenuPopup     = null;
    }

    enable() {
        try {
            this._brainProxy = new BrainProxy(
                Gio.DBus.session,
                'org.axonos.Brain',
                '/org/axonos/Brain'
            );
        } catch (e) {
            console.warn('AxonTaskbar: could not create BrainProxy:', e.message);
        }

        try {
            this._contextProxy = new ContextProxy(
                Gio.DBus.session,
                'org.axonos.Context',
                '/org/axonos/Context'
            );
        } catch (e) {
            console.warn('AxonTaskbar: could not create ContextProxy:', e.message);
        }

        // Create Start Menu popup instance
        this._startMenuPopup = new StartMenuPopup(
            this._extension,
            this._extension._spacesManager,
            this._intentBar
        );

        this._buildUI();
        this._populate();
        this._show();
        this._connectSignals();

        // Reposition whenever workspace or layout changes
        this._monitorsChangedId = Main.layoutManager.connect(
            'monitors-changed',
            this._reposition.bind(this)
        );
    }

    disable() {
        this._disconnectSignals();
        this.closeActiveMenu();

        if (this._monitorsChangedId) {
            Main.layoutManager.disconnect(this._monitorsChangedId);
            this._monitorsChangedId = null;
        }

        // Restore native GNOME panel items
        const quickSettings = this._actor ? this._actor._quickSettingsHolder : null;
        if (quickSettings && Main.panel) {
            const qsParent = quickSettings.get_parent();
            if (qsParent) qsParent.remove_child(quickSettings);
            Main.panel._rightBox.add_child(quickSettings);
        }
        const dateMenu = this._actor ? this._actor._dateMenuHolder : null;
        if (dateMenu && Main.panel) {
            const dmParent = dateMenu.get_parent();
            if (dmParent) dmParent.remove_child(dateMenu);
            Main.panel._centerBox.add_child(dateMenu);
        }

        if (this._actor) {
            Main.layoutManager.removeChrome(this._actor);
            this._actor.destroy();
            this._actor = null;
        }

        if (this._startMenuPopup) {
            this._startMenuPopup.destroy();
            this._startMenuPopup = null;
        }

        this._iconRow  = null;
        this._iconMap.clear();
        this._visible    = false;
        this._brainProxy = null;
        this._contextProxy = null;
    }

    _buildUI() {
        // Full width bottom taskbar container
        this._actor = new St.BoxLayout({
            style_class: 'axon-taskbar',
            vertical:    false,
            reactive:    true,
            track_hover: true,
        });

        // 1. Left container: Start button and Search/Intent icon
        const leftBox = new St.BoxLayout({
            style_class: 'axon-taskbar-left',
            vertical: false,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._actor.add_child(leftBox);

        // Start Menu button
        const startBtn = new DockIcon({
            icon: Gio.ThemedIcon.new('starred-symbolic'),
            label: 'Start Menu',
            callback: () => this._startMenuPopup && this._startMenuPopup.toggle(),
            dockManager: this,
        });
        leftBox.add_child(startBtn);

        // Intent Bar toggle search button
        const searchBtn = new DockIcon({
            icon: Gio.ThemedIcon.new('system-search-symbolic'),
            label: 'Intent Bar (Search / AI)',
            callback: () => this._intentBar && this._intentBar.toggle(),
            dockManager: this,
        });
        leftBox.add_child(searchBtn);

        // Spacer to push apps to center
        const centerSpacerLeft = new St.Widget({ x_expand: true });
        this._actor.add_child(centerSpacerLeft);

        // 2. Center container: App icons (pinned and running)
        this._iconRow = new St.BoxLayout({
            style_class: 'axon-taskbar-center-apps',
            vertical: false,
            y_align:  Clutter.ActorAlign.CENTER,
        });
        this._actor.add_child(this._iconRow);

        // Spacer to push tray to far right
        const centerSpacerRight = new St.Widget({ x_expand: true });
        this._actor.add_child(centerSpacerRight);

        // 3. Right container: Clock + System Indicators (stealing native items)
        const rightBox = new St.BoxLayout({
            style_class: 'axon-taskbar-right',
            vertical: false,
            y_align: Clutter.ActorAlign.CENTER,
            spacing: 12,
        });
        this._actor.add_child(rightBox);

        // Clock Label
        const clock = new TaskbarClock();
        rightBox.add_child(clock);

        // Migrate native Quick Settings (network/volume/power aggregate)
        const nativeQS = Main.panel.statusArea['quickSettings'];
        if (nativeQS) {
            const parent = nativeQS.get_parent();
            if (parent) parent.remove_child(nativeQS);
            rightBox.add_child(nativeQS);
            this._actor._quickSettingsHolder = nativeQS;
        }

        // Migrate native Clock/Calendar menu button (holds notifications)
        const nativeDate = Main.panel.statusArea['dateMenu'];
        if (nativeDate) {
            const parent = nativeDate.get_parent();
            if (parent) parent.remove_child(nativeDate);
            rightBox.add_child(nativeDate);
            this._actor._dateMenuHolder = nativeDate;
        }

        // Register with Chrome Manager so windows respect taskbar boundary
        Main.layoutManager.addChrome(this._actor, {
            affectsStruts: true,
            trackPosition: true,
        });

        this._reposition();
    }

    _populate() {
        for (const [, icon] of this._iconMap) {
            icon.destroy();
        }
        this._iconMap.clear();

        const running = this._appSystem.get_running();
        for (const app of running) {
            this._addApp(app);
        }
    }

    _addApp(app) {
        if (this._iconMap.has(app)) return;

        // Skip adding the system installers or tools if running in background
        const appName = app.get_name();
        if (appName === "Axon OS Installer") return;

        const dockIcon = new DockIcon({
            app:      app,
            label:    app.get_name(),
            callback: () => this._activateApp(app),
            dockManager: this,
        });
        dockIcon.setRunning(true);
        this._iconRow.add_child(dockIcon);
        this._iconMap.set(app, dockIcon);
    }

    _removeApp(app) {
        const icon = this._iconMap.get(app);
        if (!icon) return;
        icon.destroy();
        this._iconMap.delete(app);
    }

    _activateApp(app) {
        const windows = app.get_windows();
        if (windows.length > 0) {
            app.activate();
        } else {
            app.open_new_window(-1);
        }
    }

    _connectSignals() {
        this._appsChangedId = this._appSystem.connect(
            'app-state-changed',
            this._onAppStateChanged.bind(this)
        );

        this._windowCreatedId = global.display.connect(
            'window-created',
            this._onWindowCreated.bind(this)
        );
    }

    _disconnectSignals() {
        if (this._appsChangedId) {
            this._appSystem.disconnect(this._appsChangedId);
            this._appsChangedId = null;
        }
        if (this._windowCreatedId) {
            global.display.disconnect(this._windowCreatedId);
            this._windowCreatedId = null;
        }
    }

    _onAppStateChanged(appSystem, app) {
        const state = app.get_state();
        if (state === Shell.AppState.RUNNING) {
            this._addApp(app);
            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                const icon = this._iconMap.get(app);
                if (icon) icon.bounce();
                return GLib.SOURCE_REMOVE;
            });
        } else if (state === Shell.AppState.STOPPED) {
            this._removeApp(app);
        }
    }

    _onWindowCreated(_display, window) {
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
            const tracker = Shell.WindowTracker.get_default();
            const app     = tracker.get_window_app(window);
            if (app) {
                const icon = this._iconMap.get(app);
                if (icon) icon.bounce();
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _reposition() {
        if (!this._actor) return;

        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;

        this._actor.set_position(monitor.x, monitor.y + monitor.height - TASKBAR_HEIGHT);
        this._actor.set_size(monitor.width, TASKBAR_HEIGHT);
    }

    _show() {
        if (!this._actor) return;
        this._visible = true;
        this._reposition();
        this._actor.show();
    }

    _hide() {
        if (!this._actor) return;
        this._visible = false;
        this._actor.hide();
    }

    toggle() {
        if (this._visible) {
            this._hide();
        } else {
            this._show();
        }
    }

    showIconContextMenu(icon, event) {
        this.closeActiveMenu();

        this._contextMenu = new St.BoxLayout({
            style_class: 'axon-dock-menu',
            vertical:    true,
            reactive:    true,
        });

        const titleLabel = new St.Label({
            style_class: 'axon-dock-menu-title',
            text: icon._label || 'Application',
        });
        this._contextMenu.add_child(titleLabel);

        if (icon._app) {
            const closeBtn = new St.Button({
                style_class: 'axon-dock-menu-item',
                label: 'Close Window',
                x_align: Clutter.ActorAlign.START,
                reactive: true,
            });
            closeBtn.connect('clicked', () => {
                icon._app.request_quit();
                this.closeActiveMenu();
            });
            this._contextMenu.add_child(closeBtn);

            const sep = new St.Widget({ style_class: 'axon-dock-separator', style: 'height: 1px; width: 90%;' });
            this._contextMenu.add_child(sep);
        }

        const loadingItem = new St.Button({
            style_class: 'axon-dock-menu-item',
            label: '⬡ Loading AI shortcuts…',
            x_align: Clutter.ActorAlign.START,
            reactive: false,
        });
        this._contextMenu.add_child(loadingItem);

        Main.uiGroup.add_child(this._contextMenu);
        this._repositionMenu(icon);

        let contextStr = 'Active workspace.';
        if (this._contextProxy) {
            try {
                this._contextProxy.GetContextStringRemote((c, err) => {
                    if (!err && c) {
                        contextStr = c[0] || c;
                    }
                    this._requestAIPredictedActions(icon, contextStr, loadingItem);
                });
            } catch (e) {
                this._requestAIPredictedActions(icon, contextStr, loadingItem);
            }
        } else {
            this._requestAIPredictedActions(icon, contextStr, loadingItem);
        }

        this._clickDismissId = global.stage.connect('button-press-event', (actor, event) => {
            this.closeActiveMenu();
            return Clutter.EVENT_PROPAGATE;
        });
    }

    _requestAIPredictedActions(icon, contextStr, loadingItem) {
        if (!this._brainProxy) {
            if (loadingItem && loadingItem.set_text) loadingItem.set_text('AI Brain service offline');
            return;
        }

        let appName = icon._label || 'this application';
        let prompt = `Given the user is working on the desktop and the current context is:\n"${contextStr}"\nGenerate 2-3 short, context-specific action/task shortcuts for the application "${appName}" (e.g. "Resume my report" for LibreOffice, "Run tests" for Terminal, "Open inbox" for Mail). Reply ONLY as a JSON array of strings, e.g. ["Action 1", "Action 2"]. Do not include markdown, code blocks, or explanations.`;

        this._brainProxy.GenerateRemote(prompt, "", "", false, (result, error) => {
            if (!this._contextMenu) return;

            if (!error && result) {
                try {
                    let [response] = result;
                    let cleanResponse = response.trim();
                    if (cleanResponse.startsWith('```')) {
                        cleanResponse = cleanResponse.replace(/^```json|```$/g, '').trim();
                    }
                    if (cleanResponse.startsWith('`')) {
                        cleanResponse = cleanResponse.replace(/^`|`$/g, '').trim();
                    }
                    let actions = JSON.parse(cleanResponse);
                    if (Array.isArray(actions) && actions.length > 0) {
                        this._contextMenu.remove_child(loadingItem);

                        actions.slice(0, 3).forEach(act => {
                            const btn = new St.Button({
                                style_class: 'axon-dock-menu-item',
                                label: '⬡ ' + act,
                                x_align: Clutter.ActorAlign.START,
                                reactive: true,
                            });
                            btn.connect('clicked', () => {
                                this._executeAIAction(act, icon);
                                this.closeActiveMenu();
                            });
                            this._contextMenu.add_child(btn);
                        });
                        this._repositionMenu(icon);
                    } else {
                        if (loadingItem && loadingItem.set_text) loadingItem.set_text('No shortcuts suggested');
                    }
                } catch (e) {
                    if (loadingItem && loadingItem.set_text) loadingItem.set_text('No shortcuts suggested');
                }
            } else {
                if (loadingItem && loadingItem.set_text) loadingItem.set_text('AI suggestion failed');
            }
        });
    }

    _executeAIAction(actionText, icon) {
        if (!this._brainProxy) return;

        this._brainProxy.ClassifyIntentRemote(actionText, (result, error) => {
            if (!error && result) {
                try {
                    let [response] = result;
                    let action = JSON.parse(response);
                    if (action && action.action === 'run_command' && action.command) {
                        const cmdText = action.command.trim();
                        console.log(`AxonDock: Prompting user for AI shortcut command: ${cmdText}`);

                        const confirmProc = new Gio.Subprocess({
                            argv: [
                                'zenity',
                                '--question',
                                '--title=AI Action Confirmation',
                                '--text',
                                `Do you want to run this AI generated command?\n\n${cmdText}`,
                                '--no-wrap',
                            ],
                            flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
                        });
                        confirmProc.init(null);
                        confirmProc.wait_check_async(null, (proc, res) => {
                            try {
                                proc.wait_check_finish(res);
                                if (!proc.get_successful()) return;

                                const [ok, argv] = GLib.shell_parse_argv(cmdText);
                                if (!ok || !argv || argv.length === 0) {
                                    console.error('AxonDock: AI command cannot be safely parsed for execution:', cmdText);
                                    return;
                                }

                                const runProc = new Gio.Subprocess({
                                    argv: argv,
                                    flags: Gio.SubprocessFlags.NONE,
                                });
                                runProc.init(null);
                                runProc.wait_check_async(null, () => {});
                            } catch (e) {
                                console.error('AxonDock: failed to execute AI command:', e.message);
                            }
                        });
                    } else if (action && action.action === 'open_app' && action.app) {
                        console.log(`AxonDock: Launching app: ${action.app}`);
                        let app = this._appSystem.lookup_app(action.app);
                        if (app) {
                            app.activate();
                        } else {
                            const [ok, argv] = GLib.shell_parse_argv(action.app);
                            if (!ok || !argv || argv.length === 0) {
                                console.error('AxonDock: App command cannot be safely parsed:', action.app);
                                return;
                            }
                            const launchProc = new Gio.Subprocess({
                                argv: argv,
                                flags: Gio.SubprocessFlags.NONE,
                            });
                            launchProc.init(null);
                            launchProc.wait_check_async(null, () => {});
                        }
                    } else {
                        if (icon._app) {
                            icon._app.activate();
                        }
                    }
                } catch (e) {
                    if (icon._app) icon._app.activate();
                }
            }
        });
    }

    _repositionMenu(icon) {
        if (!this._contextMenu) return;

        const [iconX, iconY] = icon.get_transformed_position();
        const [, natW] = this._contextMenu.get_preferred_width(-1);
        const [, natH] = this._contextMenu.get_preferred_height(-1);

        const x = Math.round(iconX + (icon.width - natW) / 2);
        const y = Math.round(iconY - natH - 8);

        this._contextMenu.set_position(x, y);
    }

    closeActiveMenu() {
        if (this._clickDismissId) {
            global.stage.disconnect(this._clickDismissId);
            this._clickDismissId = null;
        }
        if (this._contextMenu) {
            this._contextMenu.destroy();
            this._contextMenu = null;
        }
    }
}
