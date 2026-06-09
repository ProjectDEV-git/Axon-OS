import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';

import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

import SpacesManager from './spaces.js';
import IntentBar from './intentbar.js';
import DockManager from './dock.js';

// ─── AxonAIIndicator ──────────────────────────────────────────────────────────

const BrainInterface = `
<node>
  <interface name="org.axonos.Brain">
    <method name="GetStatus">
      <arg type="s" name="status_json" direction="out"/>
    </method>
  </interface>
</node>
`;

const BrainProxy = Gio.DBusProxy.makeProxyWrapper(BrainInterface);

const AxonAIIndicator = GObject.registerClass(
class AxonAIIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'Axon AI Indicator', false);

        this._extension = extension;
        this._pollTimerId = null;
        this._proxy = null;

        // Build UI: "⬡ AI" label + status dot
        const box = new St.BoxLayout({
            style_class: 'axon-ai-indicator',
            vertical: false,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._iconLabel = new St.Label({
            style_class: 'axon-ai-indicator-icon',
            text: '⬡ AI',
            y_align: Clutter.ActorAlign.CENTER,
            style: [
                'font-family: "Inter", "Ubuntu", system-ui, sans-serif;',
                'font-size: 12px;',
                'font-weight: 600;',
                'color: #8b5cf6;',
                'margin-right: 5px;',
            ].join(' '),
        });

        this._statusDot = new St.Label({
            style_class: 'axon-ai-indicator-dot',
            text: '●',
            y_align: Clutter.ActorAlign.CENTER,
            style: 'color: #ef4444; font-size: 10px;', // default: red (unreachable)
        });

        box.add_child(this._iconLabel);
        box.add_child(this._statusDot);
        this.add_child(box);

        try {
            this._proxy = new BrainProxy(
                Gio.DBus.session,
                'org.axonos.Brain',
                '/org/axonos/Brain'
            );
        } catch (e) {
            console.warn('AxonShell: could not create BrainProxy:', e.message);
        }

        this._checkBrainStatus();

        // Poll every 30 seconds
        this._pollTimerId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            30,
            this._checkBrainStatus.bind(this)
        );
    }

    _checkBrainStatus() {
        if (!this._proxy) {
            this._setDotColor(false);
            return GLib.SOURCE_CONTINUE;
        }

        this._proxy.GetStatusRemote((result, error) => {
            if (error) {
                this._setDotColor(false);
            } else {
                try {
                    let [statusJson] = result;
                    let status = JSON.parse(statusJson);
                    this._setDotColor(status.ollama_online);
                } catch (e) {
                    this._setDotColor(false);
                }
            }
        });

        return GLib.SOURCE_CONTINUE;
    }

    _setDotColor(online) {
        const color = online ? '#10b981' : '#ef4444';
        this._statusDot.set_style(`color: ${color}; font-size: 10px;`);
    }

    destroy() {
        if (this._pollTimerId) {
            GLib.source_remove(this._pollTimerId);
            this._pollTimerId = null;
        }
        this._proxy = null;
        super.destroy();
    }
});

// ─── AxonShellExtension ───────────────────────────────────────────────────────

const ContextInterface = `
<node>
  <interface name="org.axonos.Context">
    <method name="SetActiveWindow">
      <arg type="s" name="title" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
  </interface>
</node>
`;

const ContextProxy = Gio.DBusProxy.makeProxyWrapper(ContextInterface);

export default class AxonShellExtension extends Extension {
    constructor(metadata) {
        super(metadata);
        this._spacesManager = null;
        this._intentBar     = null;
        this._aiIndicator   = null;
        this._dockManager   = null;
        this._keybindingIds = [];
        this._contextProxy  = null;
        this._focusWindowId = null;
    }

    enable() {
        try {
            this._contextProxy = new ContextProxy(
                Gio.DBus.session,
                'org.axonos.Context',
                '/org/axonos/Context'
            );
        } catch (e) {
            console.warn('AxonShell: could not create ContextProxy in extension.js:', e.message);
        }

        this._spacesManager = new SpacesManager(this);
        this._spacesManager.enable();

        // IntentBar must be created before DockManager (dock holds a reference)
        this._intentBar = new IntentBar(this, this._spacesManager);
        this._intentBar.enable();

        this._dockManager = new DockManager(this, this._intentBar);
        this._dockManager.enable();

        this._aiIndicator = new AxonAIIndicator(this);
        Main.panel.addToStatusArea('axon-ai-indicator', this._aiIndicator, 0, 'right');

        this._registerKeybindings();

        // Listen for focused window changes
        this._focusWindowId = global.display.connect('notify::focus-window', () => {
            try {
                let win = global.display.focus_window;
                if (win && this._contextProxy) {
                    let title = win.get_title() || "None";
                    let wmClass = win.get_wm_class() || "None";
                    this._contextProxy.SetActiveWindowRemote(title, wmClass, (result, error) => {});
                }
            } catch (e) {
                console.warn('AxonShell: focus window track error:', e.message);
            }
        });
    }

    _registerKeybindings() {
        const settings = this.getSettings();

        // Super+1..9 to switch spaces
        for (let i = 1; i <= 9; i++) {
            const spaceIndex = i - 1;
            const bindingName = `switch-to-space-${i}`;
            try {
                Main.wm.addKeybinding(
                    bindingName,
                    settings,
                    Meta.KeyBindingFlags.NONE,
                    Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
                    () => {
                        this._spacesManager.switchToSpace(spaceIndex);
                    }
                );
                this._keybindingIds.push(bindingName);
            } catch (e) {
                console.warn(`AxonShell: could not bind ${bindingName}:`, e.message);
            }
        }

        // Super+Space → toggle intent bar
        try {
            Main.wm.addKeybinding(
                'toggle-intent-bar',
                settings,
                Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
                () => {
                    this._intentBar.toggle();
                }
            );
            this._keybindingIds.push('toggle-intent-bar');
        } catch (e) {
            console.warn('AxonShell: could not bind toggle-intent-bar:', e.message);
        }

        // Super+A → toggle AI panel
        try {
            Main.wm.addKeybinding(
                'toggle-ai-panel',
                settings,
                Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
                () => {
                    this._toggleAIPanel();
                }
            );
            this._keybindingIds.push('toggle-ai-panel');
        } catch (e) {
            console.warn('AxonShell: could not bind toggle-ai-panel:', e.message);
        }
    }

    _toggleAIPanel() {
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
                try {
                    subprocess.wait_finish(result);
                } catch (e) {
                    console.warn('AxonShell: AI panel process error:', e.message);
                }
            });
        } catch (e) {
            console.warn('AxonShell: could not launch AI panel:', e.message);
        }
    }

    disable() {
        // Remove all registered keybindings
        for (const id of this._keybindingIds) {
            try {
                Main.wm.removeKeybinding(id);
            } catch (e) {
                console.warn(`AxonShell: could not remove keybinding ${id}:`, e.message);
            }
        }
        this._keybindingIds = [];

        if (this._focusWindowId) {
            global.display.disconnect(this._focusWindowId);
            this._focusWindowId = null;
        }

        if (this._aiIndicator) {
            this._aiIndicator.destroy();
            this._aiIndicator = null;
        }

        // Dock before IntentBar (dock holds a ref to intentBar)
        if (this._dockManager) {
            this._dockManager.disable();
            this._dockManager = null;
        }

        if (this._intentBar) {
            this._intentBar.disable();
            this._intentBar = null;
        }

        if (this._spacesManager) {
            this._spacesManager.disable();
            this._spacesManager = null;
        }

        this._contextProxy = null;
    }
}
