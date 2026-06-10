/**
 * Axon OS — macOS-style Dock
 * dock.js
 *
 * DockManager creates a floating, pill-shaped bottom panel that shows:
 *   - Running application icons with a presence dot and bounce-on-launch animation
 *   - A visual separator before pinned Axon launcher icons (AI Panel, Intent Bar)
 *   - Smooth show/hide driven by Clutter easing
 *   - Auto-hide when the focused window goes fullscreen
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
import AppDisplay from 'gi://AppDisplay';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

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

const DOCK_HEIGHT        = 72;   // px, total actor height including padding
const ICON_SIZE          = 48;   // px, app icon size
const ICON_PADDING       = 8;    // px, padding around each icon cell
const DOCK_MARGIN_BOTTOM = 10;   // px, gap between dock bottom edge and screen edge
const SHOW_DURATION      = 220;  // ms
const HIDE_DURATION      = 180;  // ms
const BOUNCE_PEAK        = 1.35; // scale factor at bounce apex
const BOUNCE_DURATION    = 160;  // ms per bounce phase
const TRAMPOLINE_SCALE   = 0.88; // click-press scale
const TRAMPOLINE_DURATION = 110; // ms
const FULLSCREEN_CHECK_INTERVAL = 1500; // ms

// ─── DockIcon ──────────────────────────────────────────────────────────────────

/**
 * A single icon slot in the dock.
 *
 * @param {object} opts
 *   app      {Shell.App|null}  — tracked running app (null for pinned launchers)
 *   icon     {Gio.Icon|null}   — override icon (used for pinned launchers)
 *   label    {string}          — tooltip text
 *   callback {function}        — called on click
 */
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

        // ── Icon image ──────────────────────────────────────────────────────────
        let iconWidget;
        if (this._app) {
            iconWidget = this._app.create_icon_texture(ICON_SIZE);
        } else if (opts.icon) {
            iconWidget = new St.Icon({
                gicon:     opts.icon,
                icon_size: ICON_SIZE,
                style:     'color: #c4b5fd;',
            });
        } else {
            iconWidget = new St.Icon({
                icon_name: 'application-x-executable-symbolic',
                icon_size: ICON_SIZE,
                style:     'color: #c4b5fd;',
            });
        }

        // ── Dot indicator (running apps) ───────────────────────────────────────
        this._dot = new St.Label({
            style_class: 'axon-dock-dot',
            text: '●',
            visible: this._app !== null,
        });

        // ── Cell layout: icon stacked above dot ────────────────────────────────
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

        // ── Signals ────────────────────────────────────────────────────────────
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

    // ── Dot visibility ─────────────────────────────────────────────────────────

    setRunning(running) {
        this._dot.visible = running;
    }

    // ── Bounce animation (called on app launch) ────────────────────────────────

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

    // ── Click — trampoline (scale press/release) ───────────────────────────────

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

    // ── Tooltip ────────────────────────────────────────────────────────────────

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

        // Position above this icon
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

    // ── Cleanup ────────────────────────────────────────────────────────────────

    _onDestroy() {
        if (this._tooltipId) {
            GLib.source_remove(this._tooltipId);
            this._tooltipId = null;
        }
        this._hideTooltip();
    }
});

// ─── DockManager ───────────────────────────────────────────────────────────────

export default class DockManager {
    /**
     * @param {import('./extension.js').default} extension
     * @param {IntentBar} intentBar  — IntentBar instance (for launcher button)
     */
    constructor(extension, intentBar) {
        this._extension  = extension;
        this._intentBar  = intentBar;

        this._actor         = null;
        this._iconRow       = null;
        this._iconMap       = new Map();  // Shell.App → DockIcon
        this._visible       = false;
        this._autoHidden    = false;

        this._appSystem          = Shell.AppSystem.get_default();
        this._windowTrackerId    = null;
        this._fullscreenTimerId  = null;
        this._appsChangedId      = null;
        this._windowCreatedId    = null;

        this._contextMenu        = null;
        this._clickDismissId     = null;
        this._brainProxy         = null;
        this._contextProxy       = null;
    }

    // ── Lifecycle ───────────────────────────────────────────────────────────────

    enable() {
        try {
            this._brainProxy = new BrainProxy(
                Gio.DBus.session,
                'org.axonos.Brain',
                '/org/axonos/Brain'
            );
        } catch (e) {
            console.warn('AxonDock: could not create BrainProxy in dock.js:', e.message);
        }

        try {
            this._contextProxy = new ContextProxy(
                Gio.DBus.session,
                'org.axonos.Context',
                '/org/axonos/Context'
            );
        } catch (e) {
            console.warn('AxonDock: could not create ContextProxy in dock.js:', e.message);
        }

        this._buildUI();
        this._populate();
        this._show(false /* no animation on init */);
        this._connectSignals();
        this._startFullscreenWatch();
    }

    disable() {
        this._stopFullscreenWatch();
        this._disconnectSignals();
        this.closeActiveMenu();

        if (this._actor) {
            this._actor.destroy();
            this._actor = null;
        }

        this._iconRow  = null;
        this._iconMap.clear();
        this._visible    = false;
        this._autoHidden = false;
        this._brainProxy = null;
        this._contextProxy = null;
    }

    // ── UI construction ─────────────────────────────────────────────────────────

    _buildUI() {
        // Outer pill — glass-morphism look via CSS, positioned by _reposition()
        this._actor = new St.BoxLayout({
            style_class: 'axon-dock',
            vertical:    false,
            reactive:    true,
            track_hover: true,
            x_align:     Clutter.ActorAlign.CENTER,
            y_align:     Clutter.ActorAlign.CENTER,
        });

        // Running-apps icon row
        this._iconRow = new St.BoxLayout({
            vertical: false,
            y_align:  Clutter.ActorAlign.CENTER,
        });
        this._actor.add_child(this._iconRow);

        // Separator divider
        const sep = new St.Widget({ style_class: 'axon-dock-separator' });
        this._actor.add_child(sep);

        // Pinned launchers row
        const pinnedRow = new St.BoxLayout({
            vertical: false,
            y_align:  Clutter.ActorAlign.CENTER,
        });
        this._actor.add_child(pinnedRow);

        // AI Panel launcher
        const aiIcon = new DockIcon({
            icon:     Gio.ThemedIcon.new('starred-symbolic'),
            label:    'AI Panel',
            callback: () => this._launchAIPanel(),
            dockManager: this,
        });
        pinnedRow.add_child(aiIcon);

        // Intent Bar launcher
        const intentIcon = new DockIcon({
            icon:     Gio.ThemedIcon.new('system-search-symbolic'),
            label:    'Intent Bar',
            callback: () => this._intentBar && this._intentBar.toggle(),
            dockManager: this,
        });
        pinnedRow.add_child(intentIcon);

        Main.uiGroup.add_child(this._actor);
        this._reposition();
    }

    // ── Populate running apps ───────────────────────────────────────────────────

    _populate() {
        // Clear existing app icons
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

    // ── App activation ──────────────────────────────────────────────────────────

    _activateApp(app) {
        const windows = app.get_windows();
        if (windows.length > 0) {
            // Raise existing windows
            app.activate();
        } else {
            app.open_new_window(-1);
        }
    }

    _launchAIPanel() {
        try {
            const panelScript = GLib.build_filenamev([
                GLib.get_home_dir(),
                '.local', 'share', 'axon-os', 'axon-ai-panel', 'main.py',
            ]);
            const proc = Gio.Subprocess.new(
                ['python3', panelScript],
                Gio.SubprocessFlags.NONE
            );
            proc.wait_async(null, (subprocess, result) => {
                try { subprocess.wait_finish(result); } catch (_) {}
            });
        } catch (e) {
            console.warn('AxonDock: could not launch AI panel:', e.message);
        }
    }

    // ── Signal connections ──────────────────────────────────────────────────────

    _connectSignals() {
        // App list changes (open / close)
        this._appsChangedId = this._appSystem.connect(
            'app-state-changed',
            this._onAppStateChanged.bind(this)
        );

        // New windows may trigger a bounce
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
            // Bounce the icon to announce the new app
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
        // Give the app a moment to register, then bounce its dock icon
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

    // ── Fullscreen auto-hide ────────────────────────────────────────────────────

    _startFullscreenWatch() {
        this._fullscreenTimerId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            FULLSCREEN_CHECK_INTERVAL,
            this._checkFullscreen.bind(this)
        );
    }

    _stopFullscreenWatch() {
        if (this._fullscreenTimerId) {
            GLib.source_remove(this._fullscreenTimerId);
            this._fullscreenTimerId = null;
        }
    }

    _checkFullscreen() {
        const monitor     = Main.layoutManager.primaryMonitor;
        if (!monitor) return GLib.SOURCE_CONTINUE;

        const workspace   = global.workspace_manager.get_active_workspace();
        const windows     = workspace.list_windows();
        const isFullscreen = windows.some(w =>
            w.is_fullscreen() &&
            w.get_monitor() === Main.layoutManager.primaryIndex
        );

        if (isFullscreen && !this._autoHidden) {
            this._autoHidden = true;
            this._hide(true);
        } else if (!isFullscreen && this._autoHidden) {
            this._autoHidden = false;
            this._show(true);
        }

        return GLib.SOURCE_CONTINUE;
    }

    // ── Positioning ─────────────────────────────────────────────────────────────

    _reposition() {
        if (!this._actor) return;

        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;

        const [, natW] = this._actor.get_preferred_width(-1);
        const width    = Math.max(natW, 120);
        const x        = monitor.x + Math.round((monitor.width - width) / 2);
        const y        = monitor.y + monitor.height - DOCK_HEIGHT - DOCK_MARGIN_BOTTOM;

        this._actor.set_position(x, y);
        this._actor.set_height(DOCK_HEIGHT);
    }

    // ── Show / hide ─────────────────────────────────────────────────────────────

    _show(animate = true) {
        if (!this._actor) return;
        if (this._visible) return;
        this._visible = true;

        this._reposition();
        this._actor.show();

        if (animate) {
            const monitor  = Main.layoutManager.primaryMonitor;
            const targetY  = monitor
                ? monitor.y + monitor.height - DOCK_HEIGHT - DOCK_MARGIN_BOTTOM
                : this._actor.y;

            this._actor.set_opacity(0);
            this._actor.set_translation(0, 20, 0);

            this._actor.ease({
                opacity:      255,
                translation_y: 0,
                duration:     SHOW_DURATION,
                mode:         Clutter.AnimationMode.EASE_OUT_CUBIC,
            });
        } else {
            this._actor.set_opacity(255);
            this._actor.set_translation(0, 0, 0);
        }
    }

    _hide(animate = true) {
        if (!this._actor) return;
        if (!this._visible) return;
        this._visible = false;

        if (animate) {
            this._actor.ease({
                opacity:       0,
                translation_y: 16,
                duration:      HIDE_DURATION,
                mode:          Clutter.AnimationMode.EASE_IN_CUBIC,
                onComplete:    () => { if (this._actor) this._actor.hide(); },
            });
        } else {
            this._actor.set_opacity(0);
            this._actor.hide();
        }
    }

    /** Public toggle (e.g. for a keybinding) */
    toggle() {
        if (this._visible) {
            this._hide(true);
        } else {
            this._show(true);
        }
    }

    // ── Context Menu (AI Predicted Actions) ──────────────────────────────────────

    showIconContextMenu(icon, event) {
        this.closeActiveMenu();

        // Build context menu box
        this._contextMenu = new St.BoxLayout({
            style_class: 'axon-dock-menu',
            vertical:    true,
            reactive:    true,
        });

        // Add menu title
        const titleLabel = new St.Label({
            style_class: 'axon-dock-menu-title',
            text: icon._label || 'Application',
        });
        this._contextMenu.add_child(titleLabel);

        // Standard close app item for running apps
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

            // Separator
            const sep = new St.Widget({ style_class: 'axon-dock-separator', style: 'height: 1px; width: 90%;' });
            this._contextMenu.add_child(sep);
        }

        // Pinned launcher actions
        if (!icon._app && icon._label === 'AI Panel') {
            const resetBtn = new St.Button({
                style_class: 'axon-dock-menu-item',
                label: 'New AI Chat',
                x_align: Clutter.ActorAlign.START,
                reactive: true,
            });
            resetBtn.connect('clicked', () => {
                // Just launch it, or future: trigger reset D-Bus method
                this._launchAIPanel();
                this.closeActiveMenu();
            });
            this._contextMenu.add_child(resetBtn);
        }

        // Add placeholder/loading AI actions
        const loadingItem = new St.Button({
            style_class: 'axon-dock-menu-item',
            label: '⬡ Loading AI shortcuts…',
            x_align: Clutter.ActorAlign.START,
            reactive: false,
        });
        this._contextMenu.add_child(loadingItem);

        Main.uiGroup.add_child(this._contextMenu);
        this._repositionMenu(icon);

        // Fetch context and generate actions
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

        // Click outside to dismiss menu
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
            if (!this._contextMenu) return; // Menu closed in mean time

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
                        // Remove loading placeholder
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
                        console.log(`AxonDock: Running AI shortcut command: ${action.command}`);
                        Gio.Subprocess.new(
                            ['bash', '-c', action.command],
                            Gio.SubprocessFlags.NONE
                        );
                    } else if (action && action.action === 'open_app' && action.app) {
                        console.log(`AxonDock: Launching app: ${action.app}`);
                        let app = this._appSystem.lookup_app(action.app);
                        if (app) {
                            app.activate();
                        } else {
                            Gio.Subprocess.new(
                                ['bash', '-c', action.app],
                                Gio.SubprocessFlags.NONE
                            );
                        }
                    } else {
                        // Standard fallback open the app
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
