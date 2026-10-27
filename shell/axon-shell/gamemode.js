// gamemode.js — top-bar indicator for Axon game mode (org.axonos.Gaming).
//
// Hidden until a game runs. Then a controller icon shows; its menu lists the
// running games and has "Exit game mode" (ForceExit) for when something is stuck.

import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {monitorService, unmonitorService} from './dbus-helpers.js';
import {_t, gameModeNames} from './ui-logic.js';

const GamingInterface = `
<node>
  <interface name="org.axonos.Gaming">
    <method name="ForceExit"/>
    <property name="Active" type="b" access="read"/>
    <property name="ActiveGames" type="a(ssu)" access="read"/>
  </interface>
</node>
`;

const GamingProxy = Gio.DBusProxy.makeProxyWrapper(GamingInterface);

export const GameModeIndicator = GObject.registerClass(
class GameModeIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'Axon Game Mode Indicator', false);
        this._proxy = null;
        this._changedId = 0;
        this._watchId = 0;

        this._icon = new St.Icon({
            icon_name: 'input-gaming-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);
        this.visible = false;

        // Not added with Main.panel.addToStatusArea(), so register the menu for
        // click-outside-to-close ourselves
        this._menuManager = new PopupMenu.PopupMenuManager(this);
        this._menuManager.addMenu(this.menu);

        this._header = new PopupMenu.PopupMenuItem('', {reactive: false, can_focus: false});
        this.menu.addMenuItem(this._header);
        this._gameItems = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._gameItems);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const exit = new PopupMenu.PopupMenuItem(_t('Exit game mode'));
        exit.connect('activate', () => this._proxy?.ForceExitRemote(() => {}));
        this.menu.addMenuItem(exit);

        // The proxy never starts the service; watch for it instead, so a missing
        // service just means no icon.
        this._watchId = monitorService('org.axonos.Gaming',
            () => this._connect(), () => this._disconnect());
    }

    _connect() {
        new GamingProxy(Gio.DBus.session, 'org.axonos.Gaming', '/org/axonos/Gaming',
            (proxy, error) => {
                if (error) {
                    console.warn('AxonShell: could not create GamingProxy:', error.message);
                    return;
                }
                this._proxy = proxy;
                this._changedId = proxy.connect('g-properties-changed', () => this._refresh());
                this._refresh();
            }, null, Gio.DBusProxyFlags.DO_NOT_AUTO_START);
    }

    _disconnect() {
        if (this._proxy && this._changedId)
            this._proxy.disconnect(this._changedId);
        this._changedId = 0;
        this._proxy = null;
        this._refresh();
    }

    _refresh() {
        const active = Boolean(this._proxy?.Active);
        this.visible = active;
        this.accessible_name = active ? _t('Game mode is on') : '';
        if (!active)
            return;
        const names = gameModeNames(this._proxy.ActiveGames);
        this._header.label.text = _t('Game mode is on. AI is paused.');
        this._gameItems.removeAll();
        for (const name of names)
            this._gameItems.addMenuItem(new PopupMenu.PopupMenuItem(name, {reactive: false, can_focus: false}));
    }

    destroy() {
        unmonitorService(this._watchId);
        this._watchId = 0;
        this._disconnect();
        super.destroy();
    }
});
