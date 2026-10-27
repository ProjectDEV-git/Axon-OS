// Axon Shell — screen-reader announcements.
//
// Orca presents an object with the NOTIFICATION role when it becomes
// showing, reading its accessible name. announce() shows such an object
// off-screen for a few seconds. Not verified with Orca in a real session.

import GLib from 'gi://GLib';
import St from 'gi://St';
import Atk from 'gi://Atk';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const LINGER_MS = 4000;

/**
 * Ask screen readers to read `text`.
 *
 * @param {string} text - short message, e.g. "Axon answered: …"
 */
export function announce(text) {
    if (!text)
        return;
    try {
        const label = new St.Label({
            text,
            opacity: 0,
            x: -10000,
            y: -10000,
            accessible_name: text,
        });
        label.accessible_role = Atk.Role.NOTIFICATION;
        Main.uiGroup.add_child(label);
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, LINGER_MS, () => {
            label.destroy();
            return GLib.SOURCE_REMOVE;
        });
    } catch (e) {
        console.warn(`AxonShell: announce failed: ${e.message}`);
    }
}
