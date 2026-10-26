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
import SpaceSwitcher from './spaceswitcher.js';
import AccentManager from './accent.js';
import {confirmAndRun, launchAppById} from './ai-actions.js';
import {_t, setTranslator} from './ui-logic.js';

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
        });

        // Colour plus the accessible name: status never relies on colour alone
        this._statusDot = new St.Label({
            style_class: 'axon-ai-indicator-dot axon-status-error',
            text: '○',
            y_align: Clutter.ActorAlign.CENTER,
        });

        box.add_child(this._iconLabel);
        box.add_child(this._statusDot);
        this.add_child(box);

        // Async init: a synchronous constructor would block the compositor
        // while D-Bus activation starts the Python service.
        new BrainProxy(
            Gio.DBus.session,
            'org.axonos.Brain',
            '/org/axonos/Brain',
            (proxy, error) => {
                if (error) {
                    console.warn('AxonShell: could not create BrainProxy:', error.message);
                    return;
                }
                this._proxy = proxy;
                this._checkBrainStatus();
            }
        );

        this._checkBrainStatus();

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
        // Filled dot when online, hollow when offline: shape as well as colour
        this._statusDot.set_text(online ? '●' : '○');
        if (online) {
            this._statusDot.remove_style_class_name('axon-status-error');
            this._statusDot.add_style_class_name('axon-status-ok');
        } else {
            this._statusDot.remove_style_class_name('axon-status-ok');
            this._statusDot.add_style_class_name('axon-status-error');
        }
        this.accessible_name = online ? _t('Axon AI: online') : _t('Axon AI: offline');
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

const BrainGenerateInterface = `
<node>
  <interface name="org.axonos.Brain">
    <method name="Generate">
      <arg type="s" name="prompt" direction="in"/>
      <arg type="s" name="context" direction="in"/>
      <arg type="s" name="model" direction="in"/>
      <arg type="b" name="stream" direction="in"/>
      <arg type="s" name="response" direction="out"/>
    </method>
    <method name="GetStatus">
      <arg type="s" name="status_json" direction="out"/>
    </method>
    <method name="ListProviders">
      <arg type="s" name="providers_json" direction="out"/>
    </method>
    <method name="GetModelSettings">
      <arg type="s" name="settings_json" direction="out"/>
    </method>
  </interface>
</node>
`;

const BrainFullProxy = Gio.DBusProxy.makeProxyWrapper(BrainGenerateInterface);

// Lets the GTK apps (e.g. the GTK Intent Bar) switch Spaces.
const AxonShellInterface = `
<node>
  <interface name="org.axonos.Shell">
    <method name="SwitchToSpace">
      <arg type="i" name="index" direction="in"/>
    </method>
  </interface>
</node>
`;

export default class AxonShellExtension extends Extension {
    constructor(metadata) {
        super(metadata);
        this._spacesManager = null;
        this._intentBar     = null;
        this._aiIndicator   = null;
        this._dockManager   = null;
        this._spaceSwitcher = null;
        this._keybindingIds = [];
        this._contextProxy  = null;
        this._brainProxy    = null;
        this._voiceProxy    = null;
        this._voiceOverlayProc = null;
        this._focusWindowId = null;
        this._overlayKeyId  = null;
        this._dbusImpl      = null;
        this._busNameId     = 0;
    }

    enable() {
        // Shell strings go through the extension's gettext (domain axon-os).
        setTranslator(msgid => this.gettext(msgid));
        try {
            // Async init: synchronous D-Bus proxy constructors block the whole
            // compositor while activation spawns the Python services, freezing
            // the desktop at every login/unlock.
            new ContextProxy(
                Gio.DBus.session,
                'org.axonos.Context',
                '/org/axonos/Context',
                (proxy, error) => {
                    if (error) {
                        console.warn('AxonShell: could not create ContextProxy in extension.js:', error.message);
                        return;
                    }
                    this._contextProxy = proxy;
                }
            );

            new BrainFullProxy(
                Gio.DBus.session,
                'org.axonos.Brain',
                '/org/axonos/Brain',
                (proxy, error) => {
                    if (error) {
                        console.warn('AxonShell: could not create BrainProxy in extension.js:', error.message);
                        return;
                    }
                    this._brainProxy = proxy;
                }
            );

            this._spacesManager = new SpacesManager(this);
            this._spacesManager.enable();

            this._intentBar = new IntentBar(this, this._spacesManager);
            this._intentBar.enable();

            this._spaceSwitcher = new SpaceSwitcher(this._spacesManager);
            this._spaceSwitcher.enable();

            // The accent colour chosen in Axon Preferences
            this._accent = new AccentManager(this);
            this._accent.enable();

            this._dockManager = new DockManager(this, this._intentBar);
            this._dockManager.enable();

            this._aiIndicator = new AxonAIIndicator(this);
            // Put AI indicator on the taskbar right box instead
            if (this._dockManager && this._dockManager._actor) {
                const rightBox = this._dockManager._actor.get_children().find(c => c.style_class === 'axon-taskbar-right');
                if (rightBox) {
                    rightBox.insert_child_at_index(this._aiIndicator, 0);
                }
            }

            this._registerKeybindings();
            this._exportDBus();

            // 1. Intercept the Super / Windows Key press to toggle Start Menu
            this._overlayKeyId = global.display.connect('overlay-key', () => {
                if (this._dockManager && this._dockManager._startMenuPopup) {
                    this._dockManager._startMenuPopup.toggle(true);
                }
            });

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

            // Hide default GNOME Top Panel AFTER all initialization succeeds
            if (Main.panel) {
                Main.panel.hide();
            }

            // The Axon taskbar replaces the Overview dash, and the session
            // starts on the desktop rather than in the Overview
            Main.overview.dash?.hide();
            if (Main.layoutManager._startingUp) {
                if ('startInOverview' in Main.layoutManager)
                    Main.layoutManager.startInOverview = false;
                this._startupId = Main.layoutManager.connect('startup-complete', () => {
                    Main.overview.hide();
                    Main.layoutManager.disconnect(this._startupId);
                    this._startupId = 0;
                });
            }
        } catch (e) {
            console.error('AxonShell: enable() failed, restoring panel:', e);
            // Restore the panel so the desktop doesn't go black
            if (Main.panel) {
                Main.panel.show();
            }
        }
    }

    _exportDBus() {
        try {
            this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(AxonShellInterface, {
                SwitchToSpace: index => {
                    if (this._spacesManager && Number.isInteger(index))
                        this._spacesManager.switchToSpace(index);
                },
            });
            this._dbusImpl.export(Gio.DBus.session, '/org/axonos/Shell');
            this._busNameId = Gio.bus_own_name_on_connection(
                Gio.DBus.session, 'org.axonos.Shell', Gio.BusNameOwnerFlags.NONE, null, null);
        } catch (e) {
            console.warn('AxonShell: could not export org.axonos.Shell:', e.message);
        }
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

        // Super+J → Space quick switcher
        try {
            Main.wm.addKeybinding(
                'open-space-switcher',
                settings,
                Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
                () => {
                    this._spaceSwitcher.toggle();
                }
            );
            this._keybindingIds.push('open-space-switcher');
        } catch (e) {
            console.warn('AxonShell: could not bind open-space-switcher:', e.message);
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

        // Super+V → toggle voice recording
        try {
            Main.wm.addKeybinding(
                'toggle-voice',
                settings,
                Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
                () => {
                    this._toggleVoiceRecording();
                }
            );
            this._keybindingIds.push('toggle-voice');
        } catch (e) {
            console.warn('AxonShell: could not bind toggle-voice:', e.message);
        }
    }

    _toggleVoiceRecording() {
        if (!this._voiceProxy) {
            try {
                const VoiceInterface = `
                <node>
                  <interface name="org.axonos.Voice">
                    <method name="Toggle">
                      <arg type="b" name="listening" direction="out"/>
                    </method>
                    <method name="IsListening">
                      <arg type="b" name="listening" direction="out"/>
                    </method>
                    <method name="StartAmbient">
                      <arg type="b" name="success" direction="out"/>
                    </method>
                    <method name="StopAmbient">
                      <arg type="b" name="success" direction="out"/>
                    </method>
                    <signal name="StateChanged">
                      <arg type="s" name="state"/>
                    </signal>
                    <signal name="TranscriptReady">
                      <arg type="s" name="text"/>
                    </signal>
                  </interface>
                </node>
                `;
                const VoiceProxy = Gio.DBusProxy.makeProxyWrapper(VoiceInterface);
                // Async init: the voice service loads speech models at startup;
                // a sync constructor would freeze the compositor until then.
                new VoiceProxy(
                    Gio.DBus.session,
                    'org.axonos.Voice',
                    '/org/axonos/Voice',
                    (proxy, error) => {
                        if (error) {
                            console.warn('AxonShell: could not create VoiceProxy:', error.message);
                            return;
                        }
                        this._voiceProxy = proxy;
                        this._voiceProxy.connectSignal('TranscriptReady', (p, sender, [text]) => {
                            this._onTranscriptionCompleted(text, '');
                        });
                        this._toggleVoice();
                    }
                );
            } catch (e) {
                console.warn('AxonShell: could not create VoiceProxy:', e.message);
            }
            return;
        }

        this._toggleVoice();
    }

    _toggleVoice() {
        if (!this._voiceProxy) return;
        this._voiceProxy.ToggleRemote((res, err) => {
            if (err) {
                console.warn('AxonShell: Toggle voice failed:', err.message);
                return;
            }
            let listening = false;
            if (res) {
                [listening] = res;
            }
            this._showVoiceOverlay(listening);
        });
    }

    _showVoiceOverlay(show) {
        if (show) {
            try {
                const overlayScript = GLib.build_filenamev([
                    GLib.get_home_dir(),
                    '.local', 'share', 'axon-os', 'axon-voice-overlay', 'main.py',
                ]);
                let finalScript = overlayScript;
                if (!GLib.file_test(overlayScript, GLib.FileTest.EXISTS)) {
                    finalScript = '/usr/lib/axon/apps/axon-voice-overlay/main.py';
                }
                this._voiceOverlayProc = Gio.Subprocess.new(
                    ['python3', finalScript],
                    Gio.SubprocessFlags.NONE
                );
            } catch (e) {
                console.warn('AxonShell: could not launch voice overlay:', e.message);
            }
        } else {
            if (this._voiceOverlayProc) {
                this._voiceOverlayProc.force_exit();
                this._voiceOverlayProc = null;
            }
        }
    }

    _onTranscriptionCompleted(transcription, intentJson) {
        console.log(`AxonShell Voice: Transcription: "${transcription}"`);
        if (!transcription) return;
        try {
            let action = JSON.parse(intentJson);
            if (action && action.action === 'run_command' && action.command) {
                confirmAndRun(action.command, _t('You said: “{text}”', {text: transcription}));
            } else if (action && action.action === 'open_app' && action.app) {
                launchAppById(String(action.app));
            } else {
                // If it is just a plain text reply, display it
                const infoProc = new Gio.Subprocess({
                    argv: [
                        'zenity',
                        '--info',
                        '--no-markup',
                        '--title=Axon Assistant',
                        '--text',
                        `${_t('You said: “{text}”', {text: transcription})}\n\n${_t('Reply: {reply}', {reply: intentJson})}`,
                        '--no-wrap',
                    ],
                    flags: Gio.SubprocessFlags.NONE,
                });
                infoProc.init(null);
                infoProc.wait_check_async(null, () => {});
            }
        } catch (e) {
            console.error('AxonShell Voice: failed to parse/execute intent:', e.message);
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
        setTranslator(null);
        if (this._startupId) {
            Main.layoutManager.disconnect(this._startupId);
            this._startupId = 0;
        }
        Main.overview.dash?.show();
        try {
            if (this._voiceOverlayProc) {
                this._voiceOverlayProc.force_exit();
                this._voiceOverlayProc = null;
            }
            this._voiceProxy = null;
            if (this._busNameId) {
                Gio.bus_unown_name(this._busNameId);
                this._busNameId = 0;
            }
            if (this._dbusImpl) {
                this._dbusImpl.unexport();
                this._dbusImpl = null;
            }
            // Remove Super key overlay listener
            if (this._overlayKeyId) {
                global.display.disconnect(this._overlayKeyId);
                this._overlayKeyId = null;
            }

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

            if (this._dockManager) {
                this._dockManager.disable();
                this._dockManager = null;
            }

            if (this._spaceSwitcher) {
                this._spaceSwitcher.disable();
                this._spaceSwitcher = null;
            }
            if (this._accent) {
                this._accent.disable();
                this._accent = null;
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
            this._brainProxy = null;
        } catch (e) {
            console.error('AxonShell: disable() error:', e);
        } finally {
            // Always restore the panel — even if teardown partially failed
            if (Main.panel) {
                Main.panel.show();
            }
        }
    }
}
