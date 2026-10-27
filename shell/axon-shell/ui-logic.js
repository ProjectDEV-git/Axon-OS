// Axon Shell — pure UI logic (no gi:// imports).
//
// Everything here is plain JavaScript so it can be unit tested with node
// (tests/js/). The GTK apps use the Python twin in services/ui_logic.py;
// keep the two in sync.

let translate = msgid => msgid;

/**
 * Use *fn* (the extension's gettext) for Shell strings; null restores English.
 *
 * @param {?function(string): string} fn
 */
export function setTranslator(fn) {
    translate = typeof fn === 'function' ? fn : msgid => msgid;
}

/**
 * Translate *msgid* and fill ``{name}`` placeholders, like ``_().format()``
 * in services/i18n.py, so the Python and Shell msgids match.
 *
 * @param {string} msgid
 * @param {object} [values]
 * @returns {string}
 */
export function _t(msgid, values = null) {
    const s = translate(msgid);
    if (!values)
        return s;
    return s.replace(/\{(\w+)\}/g, (m, key) => (key in values ? String(values[key]) : m));
}

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
    return {body: s.slice(0, m.index).trim(), error: message || _t('Something went wrong.')};
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
        return app ? _t('Opening {app}…', {app}) : _t('Opening app…');
    }
    if (kind === 'run_command') {
        const command = String(action.command ?? '').trim();
        return command ? _t('Run: {command}', {command}) : _t('Run command');
    }
    return _t("Axon suggested an action it can't perform here.");
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
        windows = _t('No windows open.');
    else if (windowCount === 1)
        windows = _t('1 window open.');
    else
        windows = _t('{count} windows open.', {count: windowCount});
    return `${_t('Switched to {name} Space.', {name: spaceName})} ${windows}`;
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
    return _t('{app} moved to {space}', {app: appName || _t('Window'), space: spaceName});
}

/** "Axon thinks Firefox belongs in Web." */
export function askMessage(appName, spaceName) {
    return _t('Axon thinks {app} belongs in {space}.', {app: appName || _t('this window'), space: spaceName});
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
        return _t('No windows');
    const unique = [...new Set(appNames.map(n => String(n || '').trim()).filter(Boolean))];
    const parts = [count === 1 ? _t('1 window') : _t('{count} windows', {count})];
    parts.push(...unique.slice(0, maxNames));
    if (unique.length > maxNames)
        parts.push(_t('+{count} more', {count: unique.length - maxNames}));
    return parts.join(' · ');
}

/** Loading stages (twin of STAGE_* in services/ui_logic.py). */
export const STAGES = Object.freeze({
    connecting: 'Connecting to Axon Brain…',
    waiting: 'Waiting for model…',
    planning: 'Planning action…',
    searching: 'Searching local files…',
    confirm: 'Ready for confirmation',
});

/** Example prompts for the empty Intent Bar (twin of EXAMPLE_PROMPTS). */
export const EXAMPLE_PROMPTS = Object.freeze([
    'find the PDF I edited yesterday',
    'Open Firefox',
    'How much disk space is free?',
    'Explain what a symlink is',
]);

/**
 * History (newest first) with `query` moved to the front.
 *
 * @param {string[]} history
 * @param {string} query
 * @param {number} [limit]
 * @returns {string[]}
 */
export function pushHistory(history, query, limit = 20) {
    const q = String(query ?? '').trim();
    if (!q)
        return [...history];
    return [q, ...history.filter(h => h !== q)].slice(0, limit);
}

/**
 * Most recent distinct queries to offer before the user types.
 *
 * @param {string[]} history - newest first
 * @param {number} [limit]
 * @returns {string[]}
 */
export function recentForEmptyState(history, limit = 3) {
    const out = [];
    for (const item of history) {
        if (item && !out.includes(item) && !EXAMPLE_PROMPTS.includes(item))
            out.push(item);
        if (out.length >= limit)
            break;
    }
    return out;
}

// ── Responsive sizing ───────────────────────────────────────────────────────

/**
 * Intent Bar width: 45% of the monitor, clamped to 480–820 px
 * (twin of intent_bar_width()).
 *
 * @param {number} monitorWidth - logical px
 * @returns {number}
 */
export function intentBarWidth(monitorWidth) {
    if (!(monitorWidth > 0))
        return 660;
    return Math.max(480, Math.min(820, Math.round(monitorWidth * 0.45)));
}

/**
 * Fit a popup into a work area, keeping a margin on each side.
 *
 * @param {number} width - preferred width
 * @param {number} height - preferred height
 * @param {{width: number, height: number}} workArea - logical px
 * @param {number} [margin] - px kept free on each side
 * @param {{width: number, height: number}} [minimum]
 * @returns {{width: number, height: number}}
 */
export function fitToWorkArea(width, height, workArea, margin = 20,
    minimum = {width: 320, height: 320}) {
    if (!workArea || !(workArea.width > 0) || !(workArea.height > 0))
        return {width, height};
    return {
        width: Math.max(minimum.width, Math.min(width, workArea.width - 2 * margin)),
        height: Math.max(minimum.height, Math.min(height, workArea.height - 2 * margin)),
    };
}

// ── Where the model runs (twin of model_origin() in services/ui_logic.py) ───

const PROVIDER_LABELS = {
    ollama: 'Ollama',
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    gemini: 'Google Gemini',
    openrouter: 'OpenRouter',
};
const ORIGIN_RANK = {local: 0, remote: 1, cloud: 2};

function isPrivateIPv4(host) {
    const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(host);
    if (!m)
        return null;
    const [a, b] = [Number(m[1]), Number(m[2])];
    if (a === 127)
        return 'local';
    if (a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
        (a === 169 && b === 254))
        return 'remote';
    return 'cloud';
}

/**
 * Classify a provider URL as local, remote (LAN) or cloud.
 *
 * @param {string} baseUrl
 * @returns {'local'|'remote'|'cloud'}
 */
export function hostKind(baseUrl) {
    const m = /^[a-z]+:\/\/(\[[^\]]+\]|[^/:]+)/i.exec(String(baseUrl ?? ''));
    let host = m ? m[1].toLowerCase() : '';
    if (host.startsWith('['))
        host = host.slice(1, -1);
    if (!host || host === 'localhost' || host.endsWith('.localhost') || host === '::1')
        return 'local';
    const v4 = isPrivateIPv4(host);
    if (v4)
        return v4;
    if (host.includes(':'))
        return /^(fc|fd|fe80)/.test(host) ? 'remote' : 'cloud';
    if (/\.(local|lan|internal|home\.arpa)$/.test(host) || !host.includes('.'))
        return 'remote';
    return 'cloud';
}

/**
 * Where a model runs. A `@provider/model` ref with no provider info counts
 * as cloud: never claim local without checking.
 *
 * @param {string} modelId
 * @param {object[]|null} providers - Brain ListProviders().providers
 * @returns {{kind: string, provider: string, model: string, label: string}}
 */
export function modelOrigin(modelId, providers = null) {
    const id = String(modelId ?? '');
    let providerId = 'ollama';
    let model = id;
    if (id.startsWith('@') && id.includes('/')) {
        const slash = id.indexOf('/');
        providerId = id.slice(1, slash);
        model = id.slice(slash + 1);
    }
    const cfg = (providers ?? []).find(p => String(p.id) === providerId);
    const provider = String(cfg?.label || PROVIDER_LABELS[providerId] || providerId);
    let kind;
    if (cfg?.base_url)
        kind = hostKind(cfg.base_url);
    else
        kind = providerId === 'ollama' ? 'local' : 'cloud';
    model = model || _t('default model');
    let label;
    if (kind === 'local' && provider === 'Ollama')
        label = _t('Local · {model}', {model});
    else
        label = `${{local: _t('Local'), remote: _t('Remote'), cloud: _t('Cloud')}[kind]} · ${provider} · ${model}`;
    return {kind, provider, model, label};
}

/**
 * Origin including the Brain's automatic tier choice ('' model): the least
 * private tier decides.
 *
 * @param {string} modelId
 * @param {object} tiers - GetModelSettings() result
 * @param {object[]|null} providers
 */
export function routingOrigin(modelId, tiers = {}, providers = null) {
    if (modelId)
        return modelOrigin(modelId, providers);
    const names = Object.values(tiers ?? {}).filter(Boolean);
    if (names.length === 0) {
        // Unknown tiers could include a cloud model: don't say "local"
        return {kind: 'unknown', provider: '', model: '', label: 'Model: unknown'};
    }
    const general = tiers.general_model || names[0];
    const worst = names.map(n => modelOrigin(n, providers))
        .reduce((a, b) => (ORIGIN_RANK[b.kind] > ORIGIN_RANK[a.kind] ? b : a));
    return worst.kind === 'local' ? modelOrigin(general, providers) : worst;
}

// ── Command confirmation ────────────────────────────────────────────────────

/**
 * Command line for the confirmation dialog: the Axon card, else zenity.
 * The command is always its own argument and shown as plain text.
 *
 * @param {string} command - the exact command
 * @param {string} context - optional line shown above it
 * @param {string|null} cardPath - path to the card, or null for zenity
 * @returns {string[]}
 */
export function confirmArgv(command, context, cardPath) {
    if (cardPath) {
        const argv = ['python3', cardPath, '--command', command, '--timeout', '60'];
        return context ? [...argv, '--context', context] : argv;
    }
    const text = `${context ? `${context}\n\n` : ''}Axon AI wants to run this command:\n\n${command}\n\nRun it?`;
    return [
        'zenity', '--question', '--no-markup', '--no-wrap',
        '--title=Axon AI', '--ok-label=Run', '--cancel-label=Cancel', '--timeout=60',
        '--text', text,
    ];
}

// ── Reduced motion ──────────────────────────────────────────────────────────

/**
 * Animation length honouring org.gnome.desktop.interface enable-animations.
 *
 * @param {number} ms - designed duration
 * @param {boolean} enabled - St.Settings.get().enable_animations
 * @returns {number} 0 when animations are off
 */
export function motionDuration(ms, enabled) {
    return enabled ? ms : 0;
}

/**
 * Shell motion timings in ms (Emil Kowalski's rules): surfaces opened from the
 * keyboard many times a day get only a 50 ms fade; exits are faster than
 * entrances; nothing in the UI runs past 300 ms. Entrances use a strong
 * ease-out (Clutter EASE_OUT_QUINT), exits EASE_OUT_QUAD; never ease-in.
 */
export const MOTION = Object.freeze({
    keyboard: 50,   // Intent Bar, Space switcher, Space OSD
    press: 100,     // a pressed control scaling down
    release: 160,   // and settling back
    enter: 160,     // mouse-opened menus and popovers
    exit: 110,
    tooltip: 120,
    settle: 240,    // launch feedback returning to rest
});

/** Pressed controls scale to this (subtle: 0.95-0.98). */
export const PRESS_SCALE = 0.95;

/** Menus and popovers enter from this scale with opacity, never from 0. */
export const ENTER_SCALE = 0.97;

const TOOLTIP_DELAY = 400;
const TOOLTIP_WARM = 600;

/**
 * Delay before a tooltip shows: the first waits, and once one has been open,
 * neighbours open at once (and without a fade) for a short while.
 *
 * @param {number} now - current time in ms
 * @param {?number} lastHidden - when the last tooltip closed, or null
 * @returns {number}
 */
export function tooltipDelay(now, lastHidden) {
    return lastHidden !== null && lastHidden !== undefined && now - lastHidden < TOOLTIP_WARM
        ? 0
        : TOOLTIP_DELAY;
}

/**
 * Geometry of the floating taskbar. The chrome actor stays on the monitor's
 * bottom edge (GNOME only reserves struts for chrome touching an edge), and
 * the visible bar floats inside it, *inset* px from the sides and bottom.
 *
 * @param {{x: number, y: number, width: number, height: number}} monitor
 * @param {number} barHeight
 * @param {number} inset
 * @returns {{x: number, y: number, width: number, height: number, inset: number}}
 */
export function taskbarGeometry(monitor, barHeight, inset) {
    // Very small screens keep the bar flush, so no width is lost
    const gap = monitor.width < 800 ? 0 : inset;
    const height = barHeight + gap;
    return {x: monitor.x, y: monitor.y + monitor.height - height, width: monitor.width, height, inset: gap};
}

/**
 * Whether the taskbar uses the translucent material (Apple's hierarchy cue)
 * or a solid surface (Reduce Transparency, High Contrast).
 *
 * @param {boolean} reduceTransparency
 * @param {boolean} highContrast
 * @returns {boolean}
 */
export function useMaterial(reduceTransparency, highContrast) {
    return !reduceTransparency && !highContrast;
}

// ── Accent colour ───────────────────────────────────────────────────────────

/** Accent choices, in the order Axon Preferences shows them (schema choices). */
export const ACCENTS = Object.freeze(['blue', 'teal', 'green', 'orange', 'pink', 'graphite']);

/**
 * The Shell accent override for the current appearance, relative to the
 * extension directory, or null for the default blue (already in stylesheet.css).
 *
 * @param {string} accent - the accent-color setting
 * @param {string} colorScheme - org.gnome.desktop.interface color-scheme
 * @param {boolean} highContrast
 * @returns {?string}
 */
export function accentStylesheet(accent, colorScheme, highContrast) {
    if (!ACCENTS.includes(accent) || accent === 'blue')
        return null;
    const variant = highContrast ? 'high-contrast' : colorScheme === 'prefer-light' ? 'light' : 'dark';
    return `accents/${accent}-${variant}.css`;
}

/** GNOME 47+ accent-color value for an Axon accent (graphite is GNOME's slate). */
export function gnomeAccent(accent) {
    return accent === 'graphite' ? 'slate' : accent;
}

// ── Inline calculator (twin of services/calculator.py) ─────────────────────

const CALC_MAX_LENGTH = 80;
const CALC_MAX_EXPONENT = 100;
const CALC_MAX_RESULT = 1e15;

function calcNormalise(query) {
    return String(query ?? '').replace(/×/g, '*').replace(/÷/g, '/').replace(/\*\*/g, '^');
}

/**
 * True for typed arithmetic: allowed characters and at least one operator.
 * Dates and phone-style numbers (2024-10-26) are not sums.
 */
export function looksLikeMath(query) {
    const text = calcNormalise(String(query ?? '').trim());
    if (!text || text.length > CALC_MAX_LENGTH || !/^[\d.\s+\-*/^()]+$/.test(text))
        return false;
    if (/^\d+(?:-\d+){2,}$/.test(text))
        return false;
    return /\d\s*[+\-*/^]|\)|[*/^]/.test(text) && /\d/.test(text);
}

/** Ten significant digits, no exponent notation; null when it can't be shown simply. */
export function formatNumber(value) {
    if (!Number.isFinite(value) || Math.abs(value) >= CALC_MAX_RESULT)
        return null;
    const rounded = Number(value.toPrecision(10));
    if (rounded === 0)
        return '0';
    if (Math.abs(rounded) < 1e-6)
        return null;
    return String(rounded);
}

/**
 * The result of *query* as text, or null when it isn't a calculation.
 * A recursive-descent parser, never eval: same grammar and limits as Python.
 */
export function calculate(query) {
    if (!looksLikeMath(query))
        return null;
    const tokens = calcNormalise(String(query).trim()).match(/\d+(?:\.\d*)?|\.\d+|[+\-*/^()]/g) ?? [];
    let pos = 0;
    const peek = () => (pos < tokens.length ? tokens[pos] : null);
    const take = () => tokens[pos++];
    const fail = why => {
        throw new Error(why);
    };

    let expr, unary;
    const primary = () => {
        const token = peek();
        if (token === '(') {
            take();
            const value = expr();
            if (peek() !== ')')
                fail('unbalanced brackets');
            take();
            return value;
        }
        if (token !== null && /^(?:\d+(?:\.\d*)?|\.\d+)$/.test(token))
            return Number(take());
        return fail('expected a number');
    };
    const power = () => {
        const base = primary();
        if (peek() === '^') {
            take();
            const exponent = unary();
            if (Math.abs(exponent) > CALC_MAX_EXPONENT)
                fail('exponent too large');
            if (base === 0 && exponent < 0)
                fail('division by zero');
            if (base < 0 && !Number.isInteger(exponent))
                fail('complex result');
            return base ** exponent;
        }
        return base;
    };
    unary = () => {
        if (peek() === '+' || peek() === '-') {
            const sign = take() === '-' ? -1 : 1;
            return sign * unary();
        }
        return power();
    };
    const term = () => {
        let value = unary();
        while (peek() === '*' || peek() === '/') {
            const op = take();
            const right = unary();
            if (op === '*')
                value *= right;
            else if (right === 0)
                fail('division by zero');
            else
                value /= right;
        }
        return value;
    };
    expr = () => {
        let value = term();
        while (peek() === '+' || peek() === '-') {
            const op = take();
            const right = term();
            value = op === '+' ? value + right : value - right;
        }
        return value;
    };

    try {
        const value = expr();
        if (peek() !== null)
            return null;
        return formatNumber(value);
    } catch (_e) {
        return null;
    }
}

// ── Intent Bar suggestions (twin of services/suggestions.py) ────────────────

export const SUGGESTION_CATEGORIES = Object.freeze(
    ['Calculator', 'Applications', 'Files', 'Settings', 'Spaces', 'Commands', 'Ask AI']);

/** [title, GNOME Settings panel, extra search words] */
export const SETTINGS_PANELS = Object.freeze([
    ['Wi-Fi', 'wifi', 'network wireless internet'],
    ['Bluetooth', 'bluetooth', 'devices headphones'],
    ['Displays', 'display', 'screen monitor resolution scale'],
    ['Sound', 'sound', 'volume audio speaker microphone'],
    ['Appearance', 'background', 'dark light theme wallpaper'],
    ['Power', 'power', 'battery sleep'],
    ['Keyboard', 'keyboard', 'shortcuts layout'],
    ['Mouse & Touchpad', 'mouse', 'pointer touchpad'],
    ['Privacy', 'privacy', 'location camera'],
    ['Accessibility', 'universal-access', 'a11y large text contrast zoom'],
]);

/** [exact allowlisted command, what it shows, extra search words] */
export const SUGGESTED_COMMANDS = Object.freeze([
    ['df -h', 'Free disk space', 'disk storage space'],
    ['free -h', 'Memory in use', 'ram memory'],
    ['uptime', 'How long the computer has been on', 'uptime load'],
    ['uname -a', 'System and kernel version', 'kernel version system'],
    ['whoami', 'Your user name', 'user account'],
    ['date', 'Date and time', 'time clock'],
]);

/**
 * 3 prefix, 2 word prefix, 1 inside (single words of 3+ letters), 0 none.
 *
 * @param {string} query
 * @param {...string} texts
 * @returns {number}
 */
export function matchScore(query, ...texts) {
    const q = String(query ?? '').trim().toLowerCase();
    if (!q)
        return 0;
    let best = 0;
    for (const text of texts) {
        const t = String(text ?? '').toLowerCase();
        if (t.startsWith(q))
            return 3;
        if (t.replace(/-/g, ' ').split(/\s+/).some(w => w.startsWith(q)))
            best = Math.max(best, 2);
        else if (!q.includes(' ') && q.length >= 3 && t.includes(q))
            best = Math.max(best, 1);
    }
    return best;
}

function topRows(scored, limit) {
    scored.sort((a, b) => b[0] - a[0] || a[1].title.toLowerCase().localeCompare(b[1].title.toLowerCase()));
    return scored.slice(0, limit).map(([, row]) => row);
}

/**
 * Suggestions for `query`, grouped in SUGGESTION_CATEGORIES order.
 *
 * @param {string} query
 * @param {Array<[string, string, string]>} apps - [appId, name, keywords]
 * @param {string[]} spaces - Space names in Super+1…9 order
 * @param {number} [limit]
 * @returns {Array<{category: string, title: string, subtitle: string, action: object}>}
 */
export function buildSuggestions(query, apps = [], spaces = [], limit = 3) {
    const q = String(query ?? '').trim();
    if (!q)
        return [];
    const row = (category, title, subtitle, action) => ({category, title, subtitle, action});
    if (q.toLowerCase().startsWith('find ') && q.length > 5) {
        const target = q.slice(5).trim();
        return [
            row('Files', _t('Search files for “{query}”', {query: target}), _t('Semantic search in your files'),
                {type: 'files', query: target, explicit: true}),
            row('Ask AI', _t('Ask Axon AI'), q, {type: 'ask', query: q}),
        ];
    }
    const out = [];
    // Typed arithmetic answers at once; Enter copies the result
    const result = calculate(q);
    if (result !== null)
        out.push(row('Calculator', `= ${result}`, _t('Enter copies the result'), {type: 'copy', text: result}));
    out.push(...topRows(apps
        .map(([appId, name, keywords]) =>
            [matchScore(q, name, keywords), row('Applications', name, _t('Application'), {type: 'open_app', app_id: appId})])
        .filter(([s]) => s >= 2), limit));
    out.push(row('Files', _t('Search files for “{query}”', {query: q}), _t('Semantic search in your files'),
        {type: 'files', query: q}));
    out.push(...topRows(SETTINGS_PANELS
        .map(([title, panel, words]) =>
            [matchScore(q, title, _t(title), words), row('Settings', _t(title), _t('Settings'), {type: 'settings', panel})])
        .filter(([s]) => s >= 2), limit));
    out.push(...topRows(spaces
        .map((name, i) =>
            [matchScore(q, name, `${name} space`), row('Spaces', _t('{name} Space', {name}), `Super+${i + 1}`, {type: 'space', index: i, name})])
        .filter(([s]) => s >= 2), limit));
    out.push(...topRows(SUGGESTED_COMMANDS
        .map(([command, description, words]) =>
            [matchScore(q, command, description, _t(description), words),
                row('Commands', command, _t(description), {type: 'command', command})])
        .filter(([s]) => s >= 2), limit));
    out.push(row('Ask AI', _t('Ask Axon AI'), q, {type: 'ask', query: q}));
    return out;
}

/** Action preview for a suggestion (twin of suggestions.describe()). */
export function describeSuggestion(s) {
    const a = s?.action ?? {};
    switch (a.type) {
    case 'copy': return _t('Copy {text}', {text: a.text ?? ''});
    case 'open_app': return _t('Open {app}', {app: s.title});
    case 'files': return _t('Search your files for “{query}”', {query: a.query ?? ''});
    case 'settings': return _t('Open Settings → {panel}', {panel: s.title});
    case 'space': return _t('Switch to {name} Space', {name: a.name ?? ''});
    case 'command': return _t('Run: {command} (asks first)', {command: a.command ?? ''});
    default: return _t('Ask Axon AI');
    }
}

const STRONG_CATEGORIES = new Set(['Calculator', 'Applications', 'Settings', 'Spaces', 'Commands']);

/**
 * Row Enter runs when the selection wasn't moved (twin of default_index()):
 * the best app/setting/Space/command, the file search only after "find …",
 * otherwise "Ask Axon AI".
 *
 * @param {Array<{category: string, action: object}>} rows
 * @returns {number}
 */
export function defaultSuggestionIndex(rows) {
    const i = rows.findIndex(r => STRONG_CATEGORIES.has(r.category) || r.action?.explicit);
    return i >= 0 ? i : rows.length - 1;
}

/** Trim command output for the Intent Bar (twin of format_command_output()). */
export function formatCommandOutput(text, maxLines = 40, maxChars = 4000) {
    const t = String(text ?? '').replace(/\s+$/, '');
    if (!t)
        return _t('(no output)');
    const lines = t.split('\n');
    let cut = lines.length > maxLines;
    let shown = lines.slice(0, maxLines).join('\n');
    if (shown.length > maxChars) {
        shown = shown.slice(0, maxChars);
        cut = true;
    }
    return cut ? `${shown}\n${_t('… (output shortened)')}` : shown;
}

// ── Spaces strip and quick switcher ─────────────────────────────────────────

/**
 * How one Space chip in the taskbar looks and reads. States differ by more
 * than colour: the active Space is bold on a filled pill, a Space with
 * windows has a dot under its number, an empty one is dimmed with no dot, and
 * a window that wants attention widens the dot into a bar. The window count
 * is in the accessible name, so the chip itself stays a single number.
 *
 * @param {number} index - 0-based (chip shows index + 1, matching Super+1…9)
 * @param {string} name - Space name
 * @param {boolean} active - is the current Space
 * @param {number} windowCount - normal windows on it
 * @param {boolean} attention - a window on it demands attention
 * @returns {{number: string, marker: string, styleClasses: string[], accessibleName: string}}
 *   marker is 'none', 'windows' or 'attention'.
 */
export function spaceChipState(index, name, active, windowCount, attention) {
    const styleClasses = ['axon-space-chip'];
    if (active)
        styleClasses.push('axon-space-chip-active');
    else if (windowCount === 0)
        styleClasses.push('axon-space-chip-empty');
    if (attention)
        styleClasses.push('axon-space-chip-attention');
    const marker = attention ? 'attention' : windowCount > 0 ? 'windows' : 'none';
    const windows = windowCount === 0 ? _t('empty')
        : windowCount === 1 ? _t('1 window') : _t('{count} windows', {count: windowCount});
    const parts = [_t('Space {number}: {name}', {number: index + 1, name}), windows];
    if (active)
        parts.push(_t('current'));
    if (attention)
        parts.push(_t('needs attention'));
    return {number: String(index + 1), marker, styleClasses, accessibleName: parts.join(', ')};
}

/**
 * The taskbar's app order: pinned apps (GNOME favourites) first in their
 * pinned order, then running apps that aren't pinned, in the order given.
 * Empty and repeated ids are dropped.
 *
 * @param {string[]} pinnedIds - favourite app ids, in order
 * @param {string[]} runningIds - running app ids
 * @returns {{id: string, pinned: boolean, running: boolean}[]}
 */
export function taskbarApps(pinnedIds, runningIds) {
    const pinned = new Set(pinnedIds);
    const running = new Set(runningIds);
    const seen = new Set();
    const out = [];
    for (const id of [...pinnedIds, ...runningIds]) {
        if (!id || seen.has(id))
            continue;
        seen.add(id);
        out.push({id, pinned: pinned.has(id), running: running.has(id)});
    }
    return out;
}

/**
 * The Start Menu's pinned apps: the user's favourites when they have any that
 * are installed, otherwise the Axon defaults that are installed.
 *
 * @param {string[]} favoriteIds - favourite app ids, in order
 * @param {string[]} defaultIds - Axon's default picks, in order
 * @param {(id: string) => boolean} installed - whether an app id resolves
 * @param {number} limit - most apps to show
 * @returns {string[]}
 */
export function pinnedStartApps(favoriteIds, defaultIds, installed, limit = 9) {
    const pick = ids => [...new Set(ids)].filter(id => id && installed(id)).slice(0, limit);
    const favorites = pick(favoriteIds);
    return favorites.length > 0 ? favorites : pick(defaultIds);
}

/**
 * Quick switcher results: Spaces whose name, number or window titles match.
 *
 * @param {string} query - typed text; '' lists every Space
 * @param {Array<{name: string, windows: string[]}>} spaces - in Super+1…9 order
 * @returns {Array<{index: number, name: string, window: string|null, windowIndex: number}>}
 */
export function filterSpaces(query, spaces) {
    const q = String(query ?? '').trim().toLowerCase();
    const out = [];
    spaces.forEach((space, index) => {
        if (!q || String(index + 1) === q || space.name.toLowerCase().includes(q)) {
            out.push({index, name: space.name, window: null, windowIndex: -1});
            return;
        }
        space.windows.forEach((title, windowIndex) => {
            if (String(title).toLowerCase().includes(q))
                out.push({index, name: space.name, window: title, windowIndex});
        });
    });
    return out;
}

/** Short screen-reader message when a request ends (twin of announcement()). */
export function announcement(kind, text = '') {
    const first = String(text ?? '').split(/\s+/).filter(Boolean).join(' ').slice(0, 120);
    switch (kind) {
    case 'answer': return first ? _t('Axon answered: {text}', {text: first}) : _t('Axon answered.');
    case 'error': return first ? _t("Axon couldn't finish: {text}", {text: first}) : _t("Axon couldn't finish.");
    case 'command':
        return String(text).trim() !== _t('Not run.') ? _t('Command finished.') : _t('Command not run.');
    case 'cancelled': return _t('Cancelled.');
    default: return first || _t('Done.');
    }
}
