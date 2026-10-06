// Axon Shell — shared handling for actions proposed by the AI.
//
// Every command the model proposes is checked against the same allowlist as
// services/service_utils.py (ALLOWED_COMMANDS) and runs only after the user
// approves it in a confirmation dialog. Apps launch only by desktop ID; a
// string from the model is never executed as a command line.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';

// Keep in sync with ALLOWED_COMMANDS in services/service_utils.py
const ALLOWED_BINARIES = new Set([
    'ls', 'cat', 'grep', 'echo', 'date', 'whoami', 'hostname', 'uname',
    'df', 'du', 'free', 'uptime', 'ps', 'pwd', 'wc', 'head', 'tail', 'sort',
    'uniq', 'diff', 'file', 'stat', 'readlink', 'realpath', 'basename',
    'dirname', 'paplay', 'notify-send',
]);

const FORBIDDEN_CHARS = /[|;&$`\\(){}[\]<>*?~!#\n\r\t\0]/;

/**
 * Parse an AI-proposed command and check it against the allowlist.
 *
 * @param {string} command - command line proposed by the model
 * @returns {string[]|null} argv when allowed, otherwise null
 */
export function parseAllowedCommand(command) {
    if (!command || typeof command !== 'string') return null;
    if (FORBIDDEN_CHARS.test(command)) return null;
    let argv;
    try {
        const [ok, parsed] = GLib.shell_parse_argv(command.trim());
        if (!ok) return null;
        argv = parsed;
    } catch (e) {
        return null;
    }
    if (!argv || argv.length === 0 || !ALLOWED_BINARIES.has(argv[0])) return null;
    return argv;
}

/**
 * Ask the user to approve an AI-proposed command, then run it.
 *
 * Fails closed: a blocked command, a missing dialog or a cancel runs nothing.
 *
 * @param {string} command - command line proposed by the model
 * @param {string} [context] - optional extra line shown above the command
 * @returns {boolean} false if the command was rejected before asking
 */
export function confirmAndRun(command, context = '') {
    const argv = parseAllowedCommand(command);
    if (!argv) {
        console.warn(`Axon: blocked AI command not in allowlist: ${command}`);
        return false;
    }
    const text = `${context ? `${context}\n\n` : ''}Axon AI wants to run this command:\n\n${command.trim()}\n\nRun it?`;
    try {
        const confirmProc = new Gio.Subprocess({
            argv: [
                'zenity', '--question', '--no-markup', '--no-wrap',
                '--title=Axon AI', '--ok-label=Run', '--cancel-label=Cancel',
                '--text', text,
            ],
            flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
        });
        confirmProc.init(null);
        confirmProc.wait_async(null, (proc, res) => {
            try {
                proc.wait_finish(res);
                if (!proc.get_successful()) return;
                const runProc = new Gio.Subprocess({argv, flags: Gio.SubprocessFlags.NONE});
                runProc.init(null);
                runProc.wait_async(null, () => {});
            } catch (e) {
                console.error(`Axon: failed to run AI command: ${e.message}`);
            }
        });
    } catch (e) {
        console.error(`Axon: no confirmation dialog available: ${e.message}`);
        return false;
    }
    return true;
}

/**
 * Launch an installed app by desktop ID. Never executes the name itself.
 *
 * @param {string} appId - desktop ID or name proposed by the model
 * @returns {boolean} true if a matching installed app was activated
 */
export function launchAppById(appId) {
    if (!appId || typeof appId !== 'string') return false;
    const id = appId.trim();
    const appSystem = Shell.AppSystem.get_default();
    const app = appSystem.lookup_app(id) || appSystem.lookup_app(`${id}.desktop`);
    if (!app) {
        console.warn(`Axon: no installed app matches "${id}"`);
        return false;
    }
    app.activate();
    return true;
}
