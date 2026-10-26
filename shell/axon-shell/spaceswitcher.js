// Axon Shell — keyboard-first quick switcher for Spaces (Super+J).
//
// Type a Space name, its number or part of a window title; Up/Down choose,
// Enter goes there (and focuses the matching window), Escape closes.

import St from 'gi://St';
import Clutter from 'gi://Clutter';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {filterSpaces, intentBarWidth, motionDuration} from './ui-logic.js';

const motion = ms => motionDuration(ms, St.Settings.get().enable_animations);

export default class SpaceSwitcher {
    constructor(spacesManager) {
        this._manager = spacesManager;
        this._actor = null;
        this._entry = null;
        this._list = null;
        this._results = [];
        this._windows = [];
        this._selected = 0;
        this._visible = false;
    }

    enable() {
        this._actor = new St.BoxLayout({
            style_class: 'axon-intentbar',
            vertical: true,
            reactive: true,
            visible: false,
            accessible_name: 'Go to Space',
        });
        this._entry = new St.Entry({
            style_class: 'axon-intentbar-input',
            hint_text: 'Go to a Space or window…',
            can_focus: true,
            x_expand: true,
            accessible_name: 'Search Spaces and windows',
        });
        this._entry.clutter_text.connect('text-changed', () => this._refresh());
        this._entry.clutter_text.connect('key-press-event', (_actor, event) => {
            const key = event.get_key_symbol();
            if (key === Clutter.KEY_Escape) {
                this.hide();
                return Clutter.EVENT_STOP;
            }
            if (key === Clutter.KEY_Up || key === Clutter.KEY_Down) {
                const step = key === Clutter.KEY_Up ? -1 : 1;
                this._selected = Math.max(0, Math.min(this._selected + step, this._results.length - 1));
                this._render();
                return Clutter.EVENT_STOP;
            }
            if (key === Clutter.KEY_Return || key === Clutter.KEY_KP_Enter) {
                this._go(this._results[this._selected]);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._list = new St.BoxLayout({
            style_class: 'axon-suggestions',
            vertical: true,
            accessible_name: 'Spaces',
        });
        this._actor.add_child(this._entry);
        this._actor.add_child(this._list);
        Main.uiGroup.add_child(this._actor);
    }

    disable() {
        this.hide();
        if (this._actor) {
            this._actor.destroy();
            this._actor = null;
        }
        this._entry = null;
        this._list = null;
    }

    toggle() {
        if (this._visible)
            this.hide();
        else
            this.show();
    }

    show() {
        if (this._visible || !this._actor)
            return;
        this._visible = true;
        this._entry.set_text('');
        this._refresh();
        const monitor = Main.layoutManager.currentMonitor || Main.layoutManager.primaryMonitor;
        if (monitor) {
            const width = Math.min(intentBarWidth(monitor.width), 560);
            this._actor.set_width(width);
            this._actor.set_position(monitor.x + Math.floor((monitor.width - width) / 2),
                monitor.y + Math.floor(monitor.height * 0.25));
        }
        this._actor.show();
        this._actor.set_opacity(0);
        this._actor.ease({opacity: 255, duration: motion(120), mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._entry.grab_key_focus();
    }

    hide() {
        if (!this._visible)
            return;
        this._visible = false;
        this._actor.ease({
            opacity: 0,
            duration: motion(80),
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => this._actor?.hide(),
        });
    }

    _spaceData() {
        const wm = global.workspace_manager;
        this._windows = [];
        return this._manager.getSpaces().slice(0, 9).map((space, i) => {
            const ws = i < wm.get_n_workspaces() ? wm.get_workspace_by_index(i) : null;
            const windows = ws ? ws.list_windows().filter(w => !w.is_skip_taskbar()) : [];
            this._windows[i] = windows;
            return {name: space.name, windows: windows.map(w => w.get_title() || '')};
        });
    }

    _refresh() {
        this._results = filterSpaces(this._entry.get_text(), this._spaceData());
        this._selected = 0;
        this._render();
    }

    _render() {
        this._list.destroy_all_children();
        if (this._results.length === 0) {
            this._list.add_child(new St.Label({style_class: 'axon-hint-label', text: 'No matching Space or window'}));
            return;
        }
        this._results.forEach((r, i) => {
            const title = r.window ? r.window : `${r.index + 1}  ${r.name}`;
            const detail = r.window ? `in ${r.name} (Super+${r.index + 1})` : `Super+${r.index + 1}`;
            const row = new St.BoxLayout({vertical: false, x_expand: true});
            row.add_child(new St.Label({text: title, x_expand: true, style_class: 'axon-suggestion-title'}));
            row.add_child(new St.Label({text: detail, style_class: 'axon-hint-label'}));
            const button = new St.Button({
                style_class: i === this._selected ? 'axon-suggestion axon-suggestion-selected' : 'axon-suggestion',
                child: row,
                x_expand: true,
                accessible_name: `${title}, ${detail}`,
            });
            button.connect('clicked', () => this._go(r));
            this._list.add_child(button);
        });
    }

    _go(result) {
        if (!result)
            return;
        this.hide();
        this._manager.switchToSpace(result.index);
        const window = result.windowIndex >= 0 ? this._windows[result.index]?.[result.windowIndex] : null;
        if (window)
            window.activate(global.get_current_time());
    }
}
