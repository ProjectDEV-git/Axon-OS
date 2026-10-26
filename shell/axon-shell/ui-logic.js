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

const ERROR_RE = /(?:^|\n)[ \t]*(?:\[error\][ \t]*([\s\S]*)|\[error:[ \t]*([\s\S]*?)\])/i;

/**
 * Split a reply into normal text and an error message ("[error] msg" or
 * "[Error: msg]" at the start of a line). Twin of split_error().
 *
 * @param {string} text - complete reply
 * @returns {{body: string, error: string|null}}
 */
export function splitError(text) {
    const s = String(text ?? '');
    const m = ERROR_RE.exec(s);
    if (!m)
        return {body: s.trim(), error: null};
    const message = (m[1] ?? m[2] ?? '').trim();
    return {body: s.slice(0, m.index).trim(), error: message || 'Something went wrong.'};
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

/**
 * Text a screen reader speaks after a Space switch.
 *
 * @param {string} spaceName - e.g. "Code"
 * @param {number} windowCount - normal windows in that Space
 * @returns {string} e.g. "Switched to Code Space. 3 windows open."
 */
export function spaceAnnouncement(spaceName, windowCount) {
    let windows;
    if (windowCount <= 0)
        windows = 'No windows open.';
    else if (windowCount === 1)
        windows = '1 window open.';
    else
        windows = `${windowCount} windows open.`;
    return `Switched to ${spaceName} Space. ${windows}`;
}

// ── Spaces: window routing ──────────────────────────────────────────────────

/** Move without asking at or above this classifier confidence. */
export const ROUTE_CONFIDENCE_THRESHOLD = 0.6;

/** Per-app rule value meaning "never move this app". */
export const NEVER_ROUTE = '__never__';

/**
 * Major version from a GNOME Shell version string ("46.2" → 46).
 *
 * @param {string} version - e.g. Config.PACKAGE_VERSION
 * @returns {number} 0 when unknown
 */
export function shellMajor(version) {
    const n = parseInt(String(version ?? '').split('.')[0], 10);
    return Number.isFinite(n) ? n : 0;
}

/**
 * The key a per-app routing rule is stored under.
 *
 * @param {string|null} appId - desktop app id, e.g. "firefox.desktop"
 * @param {string|null} wmClass - WM_CLASS fallback
 * @returns {string} lower-case key, '' when nothing identifies the app
 */
export function ruleKeyFor(appId, wmClass) {
    const id = String(appId ?? '').trim().toLowerCase().replace(/\.desktop$/, '');
    if (id && !id.startsWith('window:'))
        return id;
    const cls = String(wmClass ?? '').trim().toLowerCase();
    return cls === 'none' ? '' : cls;
}

/**
 * Decide from settings alone, before asking the AI.
 *
 * @param {boolean} enabled - the auto-route-windows setting
 * @param {object} rules - per-app rules: key → Space name or NEVER_ROUTE
 * @param {string} key - from ruleKeyFor()
 * @returns {{kind: 'none'}|{kind: 'move', space: string, remembered: true}|null}
 *   null when the AI should be asked
 */
export function ruleDecision(enabled, rules, key) {
    if (!enabled)
        return {kind: 'none'};
    const rule = key && rules ? rules[key] : undefined;
    if (rule === NEVER_ROUTE)
        return {kind: 'none'};
    if (typeof rule === 'string' && rule)
        return {kind: 'move', space: rule, remembered: true};
    return null;
}

/**
 * Parse the Brain's answer: ClassifyWindowDetailed JSON or a bare Space name
 * from the older ClassifyWindow (no confidence → trusted, as before).
 *
 * @param {string} raw - D-Bus reply
 * @returns {{space: string, confidence: number}}
 */
export function parseClassification(raw) {
    const text = String(raw ?? '').trim();
    if (text.startsWith('{')) {
        try {
            const obj = JSON.parse(text);
            const confidence = Number(obj.confidence);
            return {
                space: String(obj.space ?? ''),
                confidence: Number.isFinite(confidence) ? confidence : 0,
            };
        } catch (_e) {
            return {space: '', confidence: 0};
        }
    }
    return {space: text, confidence: 1};
}

/**
 * Decide what to do with the AI's classification.
 *
 * @param {string} space - classified Space name
 * @param {number} confidence - 0..1
 * @param {string[]} spaceNames - the user's Spaces
 * @param {string|null} currentSpace - Space the window is already on
 * @param {number} [threshold]
 * @returns {{kind: 'none'}|{kind: 'move'|'ask', space: string}}
 */
export function classificationDecision(space, confidence, spaceNames, currentSpace,
    threshold = ROUTE_CONFIDENCE_THRESHOLD) {
    const match = spaceNames.find(n => n.toLowerCase() === String(space).toLowerCase());
    if (!match || confidence <= 0)
        return {kind: 'none'};
    if (currentSpace && match.toLowerCase() === currentSpace.toLowerCase())
        return {kind: 'none'};
    return {kind: confidence >= threshold ? 'move' : 'ask', space: match};
}

/** "Firefox moved to Web" */
export function movedMessage(appName, spaceName) {
    return `${appName || 'Window'} moved to ${spaceName}`;
}

/** "Axon thinks Firefox belongs in Web." */
export function askMessage(appName, spaceName) {
    return `Axon thinks ${appName || 'this window'} belongs in ${spaceName}.`;
}

/**
 * Instant OSD summary from the window list, e.g.
 * "3 windows · Code · Terminal · Firefox".
 *
 * @param {string[]} appNames - one entry per window (duplicates allowed)
 * @param {number} [maxNames] - app names to list before "+N more"
 * @returns {string}
 */
export function windowSummary(appNames, maxNames = 3) {
    const count = appNames.length;
    if (count === 0)
        return 'No windows';
    const unique = [...new Set(appNames.map(n => String(n || '').trim()).filter(Boolean))];
    const parts = [count === 1 ? '1 window' : `${count} windows`];
    parts.push(...unique.slice(0, maxNames));
    if (unique.length > maxNames)
        parts.push(`+${unique.length - maxNames} more`);
    return parts.join(' · ');
}
