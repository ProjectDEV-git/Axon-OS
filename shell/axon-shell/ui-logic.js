// Axon Shell — pure UI logic (no gi:// imports).
//
// Everything here is plain JavaScript so it can be unit tested with node
// (tests/js/). The GTK apps use the Python twin in services/ui_logic.py;
// keep the two in sync.

const FENCE = '```';
const FENCE_RE = /^```[A-Za-z0-9_-]*\s*\n?([\s\S]*?)\n?```\s*$/;

/**
 * Return the inside of a single fenced code block, or the trimmed text.
 *
 * @param {string} text - model reply
 * @returns {string}
 */
export function stripCodeFence(text) {
    const s = String(text ?? '').trim();
    const m = FENCE_RE.exec(s);
    return m ? m[1].trim() : s;
}

/**
 * Decide whether a partial reply can be shown yet.
 *
 * @param {string} text - everything received so far
 * @returns {'hold'|'show'} hold while the reply could still be action JSON
 */
export function classifyStreamPrefix(text) {
    const s = String(text ?? '').trimStart();
    if (!s || s.startsWith('{'))
        return 'hold';
    if (FENCE.startsWith(s))
        return 'hold';
    if (!s.startsWith(FENCE))
        return 'show';
    const rest = s.slice(FENCE.length);
    const newline = rest.indexOf('\n');
    if (newline < 0)
        return 'json'.startsWith(rest.trim().toLowerCase()) ? 'hold' : 'show';
    const lang = rest.slice(0, newline).trim().toLowerCase();
    if (lang !== '' && lang !== 'json')
        return 'show';
    const body = rest.slice(newline + 1).trimStart();
    return !body || body.startsWith('{') ? 'hold' : 'show';
}

/**
 * Parse a model reply as an action object.
 *
 * @param {string} text - complete reply
 * @returns {object|null} the action when it has a string `action` key
 */
export function parseAction(text) {
    const body = stripCodeFence(text);
    if (!body.startsWith('{'))
        return null;
    try {
        const obj = JSON.parse(body);
        if (obj && typeof obj === 'object' && !Array.isArray(obj) &&
            typeof obj.action === 'string')
            return obj;
    } catch (_e) {
        // not JSON
    }
    return null;
}

/**
 * A short, friendly line for an action instead of its JSON.
 *
 * @param {object} action - parsed action
 * @returns {string}
 */
export function describeAction(action) {
    const kind = action?.action ?? '';
    if (kind === 'open_app') {
        const app = String(action.app ?? '').trim();
        return app ? `Opening ${app}…` : 'Opening app…';
    }
    if (kind === 'run_command') {
        const command = String(action.command ?? '').trim();
        return command ? `Run: ${command}` : 'Run command';
    }
    return "Axon suggested an action it can't perform here.";
}

/**
 * Track the one AI request a popup may have running. Cancelling (or
 * starting a newer request) makes older ids stale so late replies are
 * dropped. Twin of RequestTracker in services/ui_logic.py.
 */
export class RequestTracker {
    constructor() {
        this._id = 0;
        this._busy = false;
    }

    get busy() {
        return this._busy;
    }

    start() {
        this._id += 1;
        this._busy = true;
        return this._id;
    }

    finish(id) {
        if (id !== this._id)
            return false;
        this._busy = false;
        return true;
    }

    cancel() {
        if (!this._busy)
            return false;
        this._id += 1;
        this._busy = false;
        return true;
    }

    isCurrent(id) {
        return id === this._id && this._busy;
    }
}

/**
 * What Escape does: cancel a running request first, otherwise close.
 *
 * @param {boolean} busy - whether a request is running
 * @returns {'cancel'|'close'}
 */
export function escapeAction(busy) {
    return busy ? 'cancel' : 'close';
}
