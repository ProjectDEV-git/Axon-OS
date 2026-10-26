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
    model = model || 'default model';
    let label;
    if (kind === 'local' && provider === 'Ollama')
        label = `Local · ${model}`;
    else
        label = `${{local: 'Local', remote: 'Remote', cloud: 'Cloud'}[kind]} · ${provider} · ${model}`;
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
