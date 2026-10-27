// Axon Shell — small notification helper that works on GNOME 45 to 50.
//
// GNOME 46 changed MessageTray.Source/Notification to take a params object
// and replaced showNotification() with addNotification().

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as Config from 'resource:///org/gnome/shell/misc/config.js';

import {shellMajor} from './ui-logic.js';

const MAJOR = shellMajor(Config.PACKAGE_VERSION);

let _source = null;

function getSource() {
    if (_source)
        return _source;
    _source = MAJOR >= 46
        ? new MessageTray.Source({title: 'Axon', iconName: 'view-grid-symbolic'})
        : new MessageTray.Source('Axon', 'view-grid-symbolic');
    _source.connect('destroy', () => {
        _source = null;
    });
    Main.messageTray.add(_source);
    return _source;
}

/**
 * Show a transient notification with optional action buttons.
 *
 * @param {string} title - notification title
 * @param {string} body - one line of detail
 * @param {{label: string, callback: Function}[]} [actions] - buttons
 * @returns {object|null} the notification, or null if it could not be shown
 */
export function notify(title, body, actions = []) {
    try {
        const source = getSource();
        const notification = MAJOR >= 46
            ? new MessageTray.Notification({source, title, body, isTransient: true})
            : new MessageTray.Notification(source, title, body);
        if (MAJOR < 46)
            notification.setTransient(true);
        for (const {label, callback} of actions)
            notification.addAction(label, callback);
        if (MAJOR >= 46)
            source.addNotification(notification);
        else
            source.showNotification(notification);
        return notification;
    } catch (e) {
        console.warn(`AxonShell: could not show notification: ${e.message}`);
        return null;
    }
}

/** Remove the Axon notification source (call from disable()). */
export function destroyNotifications() {
    if (_source) {
        _source.destroy();
        _source = null;
    }
}
