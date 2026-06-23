// dbus-helpers.js — D-Bus error recovery utilities for Axon Shell Extension

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

/**
 * Safely call a D-Bus proxy method with error handling.
 * @param {Gio.DBusProxy} proxy - The D-Bus proxy
 * @param {string} method - Method name
 * @param {Array} args - Method arguments
 * @param {Function} callback - Callback(result, error)
 * @param {*} fallback - Fallback value on error
 */
export function safeCall(proxy, method, args, callback, fallback = null) {
    if (!proxy) {
        if (callback) callback(fallback, new Error('No D-Bus proxy'));
        return fallback;
    }

    try {
        const methodName = `${method}Remote`;
        if (typeof proxy[methodName] === 'function') {
            proxy[methodName](...args, (result, error) => {
                if (error) {
                    console.warn(`AxonShell: D-Bus ${method} failed:`, error.message);
                    if (callback) callback(fallback, error);
                } else {
                    if (callback) callback(result, null);
                }
            });
        } else {
            console.warn(`AxonShell: D-Bus method ${method} not found on proxy`);
            if (callback) callback(fallback, new Error(`Method ${method} not found`));
        }
    } catch (e) {
        console.warn(`AxonShell: D-Bus ${method} exception:`, e.message);
        if (callback) callback(fallback, e);
    }
    return fallback;
}

/**
 * Create a D-Bus proxy with retry logic.
 * @param {string} interfaceXml - D-Bus interface XML
 * @param {string} busName - D-Bus bus name
 * @param {string} objectPath - D-Bus object path
 * @param {number} maxRetries - Maximum retry attempts
 * @returns {Gio.DBusProxy|null}
 */
export function createProxyWithRetry(interfaceXml, busName, objectPath, maxRetries = 3) {
    const ProxyClass = Gio.DBusProxy.makeProxyWrapper(interfaceXml);

    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const proxy = new ProxyClass(
                Gio.DBus.session,
                busName,
                objectPath
            );
            return proxy;
        } catch (e) {
            console.warn(`AxonShell: proxy creation attempt ${attempt + 1} failed:`, e.message);
            if (attempt < maxRetries - 1) {
                // Wait before retry (exponential backoff)
                GLib.usleep((attempt + 1) * 100000); // 100ms, 200ms, 300ms
            }
        }
    }
    return null;
}

/**
 * Wrap a proxy method call with automatic retry on transient failures.
 * @param {Gio.DBusProxy} proxy - The D-Bus proxy
 * @param {string} method - Method name (without Remote suffix)
 * @param {Array} args - Method arguments
 * @param {Function} callback - Callback(result, error)
 * @param {number} maxRetries - Maximum retry attempts
 */
export function callWithRetry(proxy, method, args, callback, maxRetries = 2) {
    let attempt = 0;

    function tryCall() {
        attempt++;
        const methodName = `${method}Remote`;

        if (!proxy || typeof proxy[methodName] !== 'function') {
            if (callback) callback(null, new Error('Invalid proxy or method'));
            return;
        }

        proxy[methodName](...args, (result, error) => {
            if (error && attempt < maxRetries) {
                // Retry on transient errors (service not ready, etc.)
                console.warn(`AxonShell: ${method} attempt ${attempt} failed, retrying...`);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500 * attempt, () => {
                    tryCall();
                    return GLib.SOURCE_REMOVE;
                });
            } else {
                if (callback) callback(result, error);
            }
        });
    }

    tryCall();
}

/**
 * Monitor a D-Bus service for name owner changes (restart detection).
 * @param {string} busName - D-Bus bus name to monitor
 * @param {Function} onAvailable - Called when service becomes available
 * @param {Function} onUnavailable - Called when service disappears
 * @returns {number} Signal handler ID
 */
export function monitorService(busName, onAvailable, onUnavailable) {
    let lastOwner = null;

    const handlerId = Gio.DBus.session.watch_name(
        busName,
        Gio.BusNameWatcherFlags.NONE,
        (connection, name, owner) => {
            if (owner && !lastOwner) {
                console.log(`AxonShell: ${name} appeared (owner: ${owner})`);
                if (onAvailable) onAvailable(name, owner);
            }
            lastOwner = owner;
        },
        (connection, name) => {
            if (lastOwner) {
                console.warn(`AxonShell: ${name} disappeared`);
                if (onUnavailable) onUnavailable(name);
            }
            lastOwner = null;
        }
    );

    return handlerId;
}

/**
 * Unmonitor a D-Bus service.
 * @param {number} handlerId - Handler ID from monitorService
 */
export function unmonitorService(handlerId) {
    if (handlerId) {
        Gio.DBus.session.unwatch_name(handlerId);
    }
}
