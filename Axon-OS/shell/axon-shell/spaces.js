import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

// ─── Default space definitions ────────────────────────────────────────────────

const DEFAULT_SPACES = [
    { name: 'Code',     color: '#8b5cf6' },
    { name: 'Web',      color: '#22d3ee' },
    { name: 'Chat',     color: '#10b981' },
    { name: 'Files',    color: '#f59e0b' },
    { name: 'Media',    color: '#ef4444' },
    { name: 'Work',     color: '#ec4899' },
    { name: 'Personal', color: '#06b6d4' },
    { name: 'Terminal', color: '#84cc16' },
    { name: 'Notes',    color: '#f97316' },
];

// ─── SpaceDefinition ──────────────────────────────────────────────────────────

class SpaceDefinition {
    constructor({ id, name, color = '#a78bfa', appIds = [], lastActive = null }) {
        this.id = id;
        this.name = name;
        this.color = color;
        this.appIds = appIds;
        this.lastActive = lastActive;
    }

    toJSON() {
        return {
            id: this.id,
            name: this.name,
            color: this.color,
            appIds: this.appIds,
            lastActive: this.lastActive,
        };
    }
}

// ─── SpaceIndicator ───────────────────────────────────────────────────────────

const SpaceIndicator = GObject.registerClass(
class SpaceIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'Axon Space Indicator', false);

        this._extension = extension;

        const box = new St.BoxLayout({
            style_class: 'axon-space-indicator',
            vertical: false,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._dot = new St.Label({
            style_class: 'axon-space-dot',
            text: '●',
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._label = new St.Label({
            style_class: 'axon-space-label',
            text: 'Space',
            y_align: Clutter.ActorAlign.CENTER,
        });

        box.add_child(this._dot);
        box.add_child(this._label);
        this.add_child(box);
    }

    update(space) {
        if (!space) return;
        this._label.set_text(space.name);
        this._dot.set_style(`color: ${space.color};`);
    }
});

const ContextInterface = `
<node>
  <interface name="org.axonos.Context">
    <method name="SetActiveSpace">
      <arg type="s" name="space_name" direction="in"/>
      <arg type="b" name="success" direction="out"/>
    </method>
  </interface>
</node>
`;

const BrainInterface = `
<node>
  <interface name="org.axonos.Brain">
    <method name="ClassifyWindow">
      <arg type="s" name="title" direction="in"/>
      <arg type="s" name="wm_class" direction="in"/>
      <arg type="s" name="space_name" direction="out"/>
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

const ContextProxy = Gio.DBusProxy.makeProxyWrapper(ContextInterface);
const BrainProxy = Gio.DBusProxy.makeProxyWrapper(BrainInterface);

// ─── SpacesManager ────────────────────────────────────────────────────────────

export default class SpacesManager {
    constructor(extension) {
        this._extension = extension;
        this._spaces = [];
        this._currentIndex = 0;
        this._indicator = null;
        this._workspaceChangedId = null;
        this._contextProxy = null;
        this._brainProxy = null;
        this._windowCreatedId = null;
        this._stateDir = GLib.build_filenamev([GLib.get_home_dir(), '.axon']);
        this._stateFile = GLib.build_filenamev([this._stateDir, 'spaces.json']);
    }

    enable() {
        this._ensureStateDir();
        this._loadState();

        try {
            this._contextProxy = new ContextProxy(
                Gio.DBus.session,
                'org.axonos.Context',
                '/org/axonos/Context'
            );
        } catch (e) {
            console.warn('AxonShell: could not create ContextProxy in spaces.js:', e.message);
        }

        try {
            this._brainProxy = new BrainProxy(
                Gio.DBus.session,
                'org.axonos.Brain',
                '/org/axonos/Brain'
            );
        } catch (e) {
            console.warn('AxonShell: could not create BrainProxy in spaces.js:', e.message);
        }

        this._indicator = new SpaceIndicator(this._extension);
        Main.panel.addToStatusArea('axon-space-indicator', this._indicator, 0, 'left');
        this._updateIndicator();

        this._workspaceChangedId = global.workspace_manager.connect(
            'active-workspace-changed',
            this._onWorkspaceChanged.bind(this)
        );

        // Listen for new windows to auto-classify and route
        this._windowCreatedId = global.display.connect('window-created', (display, window) => {
            try {
                if (window && this._brainProxy) {
                    let title = window.get_title() || "None";
                    let wmClass = window.get_wm_class() || "None";
                    this._brainProxy.ClassifyWindowRemote(title, wmClass, (result, error) => {
                        if (!error && result) {
                            let [spaceName] = result;
                            this._routeWindowToSpace(window, spaceName);
                        }
                    });
                }
            } catch (e) {
                console.warn('AxonShell: window-created hook error:', e.message);
            }
        });
    }

    disable() {
        if (this._workspaceChangedId) {
            global.workspace_manager.disconnect(this._workspaceChangedId);
            this._workspaceChangedId = null;
        }

        if (this._windowCreatedId) {
            global.display.disconnect(this._windowCreatedId);
            this._windowCreatedId = null;
        }

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }

        this._contextProxy = null;
        this._brainProxy = null;
        this._saveState();
    }

    // ── State persistence ──────────────────────────────────────────────────────

    _ensureStateDir() {
        try {
            const dir = Gio.File.new_for_path(this._stateDir);
            if (!dir.query_exists(null)) {
                dir.make_directory_with_parents(null);
            }
        } catch (e) {
            console.warn('AxonShell: could not create ~/.axon directory:', e.message);
        }
    }

    _loadState() {
        try {
            const file = Gio.File.new_for_path(this._stateFile);
            if (!file.query_exists(null)) {
                this._createDefaultState();
                return;
            }

            const [ok, contents] = file.load_contents(null);
            if (!ok) {
                this._createDefaultState();
                return;
            }

            const decoder = new TextDecoder('utf-8');
            const json = JSON.parse(decoder.decode(contents));

            if (Array.isArray(json.spaces) && json.spaces.length > 0) {
                this._spaces = json.spaces.map(s => new SpaceDefinition(s));
                this._currentIndex = typeof json.currentIndex === 'number'
                    ? Math.min(json.currentIndex, this._spaces.length - 1)
                    : 0;
            } else {
                this._createDefaultState();
            }
        } catch (e) {
            console.warn('AxonShell: failed to load spaces state:', e.message);
            this._createDefaultState();
        }
    }

    _createDefaultState() {
        this._spaces = DEFAULT_SPACES.map(def => new SpaceDefinition({
            id: GLib.uuid_string_random(),
            name: def.name,
            color: def.color,
            appIds: [],
            lastActive: null,
        }));
        this._currentIndex = 0;
        this._saveState();
    }

    saveState() {
        this._saveState();
    }

    _saveState() {
        try {
            const data = {
                currentIndex: this._currentIndex,
                spaces: this._spaces.map(s => s.toJSON()),
            };
            const json = JSON.stringify(data, null, 2);
            const file = Gio.File.new_for_path(this._stateFile);
            const encoder = new TextEncoder();
            const bytes = encoder.encode(json);
            file.replace_contents(
                bytes,
                null,
                false,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                null
            );
        } catch (e) {
            console.warn('AxonShell: failed to save spaces state:', e.message);
        }
    }

    // ── OSD overlay ───────────────────────────────────────────────────────────

    _showSpaceOSD(space, idx) {
        try {
            const monitor = Main.layoutManager.primaryMonitor;
            if (!monitor) return;

            const osd = new St.BoxLayout({
                style_class: 'axon-space-osd',
                vertical: true,
                x_align: Clutter.ActorAlign.CENTER,
                y_align: Clutter.ActorAlign.CENTER,
                reactive: false,
                style: [
                    'background: rgba(17,17,25,0.92);',
                    'border: 1px solid rgba(139,92,246,0.35);',
                    'border-radius: 16px;',
                    'padding: 20px 36px;',
                    'min-width: 160px;',
                ].join(' '),
            });

            const numberLabel = new St.Label({
                style_class: 'axon-space-osd-number',
                text: String(idx + 1),
                x_align: Clutter.ActorAlign.CENTER,
                style: [
                    `color: ${space.color};`,
                    'font-family: "Inter", "Ubuntu", system-ui, sans-serif;',
                    'font-size: 48px;',
                    'font-weight: 700;',
                    'line-height: 1;',
                ].join(' '),
            });

            const nameLabel = new St.Label({
                style_class: 'axon-space-osd-name',
                text: space.name,
                x_align: Clutter.ActorAlign.CENTER,
                style: [
                    'color: #e8e8f4;',
                    'font-family: "Inter", "Ubuntu", system-ui, sans-serif;',
                    'font-size: 18px;',
                    'font-weight: 600;',
                    'margin-top: 4px;',
                ].join(' '),
            });

            const indexLabel = new St.Label({
                style_class: 'axon-space-osd-index',
                text: `Space ${idx + 1} of 9`,
                x_align: Clutter.ActorAlign.CENTER,
                style: [
                    'color: #9090b8;',
                    'font-family: "Inter", "Ubuntu", system-ui, sans-serif;',
                    'font-size: 12px;',
                    'margin-top: 6px;',
                ].join(' '),
            });

            const summaryLabel = new St.Label({
                style_class: 'axon-space-osd-summary',
                text: 'Analyzing workspace…',
                x_align: Clutter.ActorAlign.CENTER,
                style: [
                    'color: #a78bfa;',
                    'font-family: "Inter", "Ubuntu", system-ui, sans-serif;',
                    'font-size: 13px;',
                    'font-weight: 500;',
                    'margin-top: 8px;',
                ].join(' '),
            });

            osd.add_child(numberLabel);
            osd.add_child(nameLabel);
            osd.add_child(indexLabel);
            osd.add_child(summaryLabel);

            // Fetch workspace windows and generate summary via Brain service
            let workspace = global.workspace_manager.get_workspace_by_index(idx);
            let windows = workspace ? workspace.list_windows() : [];
            if (windows.length === 0) {
                summaryLabel.set_text('Empty workspace');
            } else if (this._brainProxy) {
                let windowList = windows.map(w => {
                    let title = w.get_title() || 'Unknown';
                    let wmClass = w.get_wm_class() || 'Unknown';
                    return `- Title: "${title}" (Class: "${wmClass}")`;
                }).join('\n');
                let prompt = `Summarize what the user is doing on this desktop workspace in a single short phrase (under 10 words, e.g., "3 terminals running, 2 browser tabs open"). Do not include markdown, quotes, formatting, or prefixes. Open windows:\n${windowList}`;
                this._brainProxy.GenerateRemote(prompt, "", "", false, (result, error) => {
                    if (!error && result) {
                        let [response] = result;
                        summaryLabel.set_text(response.trim().replace(/^"|"$/g, ''));
                    } else {
                        summaryLabel.set_text('Workspace active');
                    }
                });
            } else {
                summaryLabel.set_text('Workspace active');
            }

            // Measure after adding children; use a sensible fallback size
            const osdWidth = 320;
            const osdHeight = 180;

            osd.set_position(
                monitor.x + Math.floor((monitor.width - osdWidth) / 2),
                monitor.y + Math.floor((monitor.height - osdHeight) / 2)
            );

            osd.set_opacity(0);
            Main.uiGroup.add_child(osd);

            // Fade in
            osd.ease({
                opacity: 255,
                duration: 200,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            });

            // After 3500 ms, fade out and destroy
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 3500, () => {
                osd.ease({
                    opacity: 0,
                    duration: 300,
                    mode: Clutter.AnimationMode.EASE_IN_QUAD,
                    onComplete: () => {
                        try {
                            Main.uiGroup.remove_child(osd);
                            osd.destroy();
                        } catch (_e) {
                            // Already destroyed or removed
                        }
                    },
                });
                return GLib.SOURCE_REMOVE;
            });
        } catch (e) {
            console.warn('AxonShell: _showSpaceOSD error:', e.message);
        }
    }

    // ── Workspace / space operations ───────────────────────────────────────────

    switchToSpace(idx) {
        if (idx < 0 || idx >= this._spaces.length) return;

        this._currentIndex = idx;
        this._spaces[idx].lastActive = new Date().toISOString();

        const workspaceManager = global.workspace_manager;

        // Ensure enough workspaces exist
        while (workspaceManager.get_n_workspaces() <= idx) {
            workspaceManager.append_new_workspace(false, global.get_current_time());
        }

        const workspace = workspaceManager.get_workspace_by_index(idx);
        if (workspace) {
            workspace.activate(global.get_current_time());
        }

        this._updateIndicator();
        this._saveState();
        this._showSpaceOSD(this._spaces[idx], idx);
    }

    createSpace(name, color = '#a78bfa') {
        const space = new SpaceDefinition({
            id: GLib.uuid_string_random(),
            name,
            color,
            appIds: [],
            lastActive: null,
        });
        this._spaces.push(space);
        this._saveState();
        return space;
    }

    getSpaces() {
        return [...this._spaces];
    }

    getCurrentSpace() {
        return this._spaces[this._currentIndex] || this._spaces[0] || null;
    }

    // ── Internal ───────────────────────────────────────────────────────────────

    _onWorkspaceChanged() {
        const activeIndex = global.workspace_manager.get_active_workspace_index();
        if (activeIndex >= 0 && activeIndex < this._spaces.length) {
            this._currentIndex = activeIndex;
        } else if (activeIndex >= this._spaces.length) {
            // Workspace exists but no corresponding space; clamp to last
            this._currentIndex = this._spaces.length - 1;
        }
        this._updateIndicator();
    }

    _updateIndicator() {
        const currentSpace = this.getCurrentSpace();
        if (this._indicator) {
            this._indicator.update(currentSpace);
        }
        if (this._contextProxy && currentSpace) {
            this._contextProxy.SetActiveSpaceRemote(currentSpace.name, (result, error) => {
                if (error) {
                    // Silently ignore if context daemon is not running
                }
            });
        }
    }

    _routeWindowToSpace(window, spaceName) {
        let targetIdx = -1;
        for (let i = 0; i < this._spaces.length; i++) {
            if (this._spaces[i].name.toLowerCase() === spaceName.toLowerCase()) {
                targetIdx = i;
                break;
            }
        }
        
        if (targetIdx !== -1) {
            const workspaceManager = global.workspace_manager;
            while (workspaceManager.get_n_workspaces() <= targetIdx) {
                workspaceManager.append_new_workspace(false, global.get_current_time());
            }
            let workspace = workspaceManager.get_workspace_by_index(targetIdx);
            if (workspace) {
                window.change_workspace(workspace);
                console.log(`AxonShell: Auto-routed window "${window.get_title()}" to Space ${spaceName} (Workspace ${targetIdx})`);
            }
        }
    }
}
