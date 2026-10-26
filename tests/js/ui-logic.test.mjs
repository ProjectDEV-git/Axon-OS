// Unit tests for shell/axon-shell/ui-logic.js (run by tests/test_shell_js.py).
import assert from 'node:assert/strict';
import test from 'node:test';

import {
    classifyStreamPrefix,
    describeAction,
    parseAction,
    stripCodeFence,
} from './ui-logic.mjs';

test('holds replies that may be action JSON', () => {
    for (const t of ['', '  ', '{', '`', '``', '```', '```j', '```json', '```json\n{', '```\n'])
        assert.equal(classifyStreamPrefix(t), 'hold', JSON.stringify(t));
});

test('shows plain text right away', () => {
    for (const t of ['Sure', '```python', '```bash\nls', '```json\nnope', '[1]'])
        assert.equal(classifyStreamPrefix(t), 'show', JSON.stringify(t));
});

test('parses bare and fenced actions', () => {
    assert.deepEqual(parseAction('{"action":"open_app","app":"firefox"}'),
        {action: 'open_app', app: 'firefox'});
    assert.deepEqual(parseAction('```json\n{"action":"run_command","command":"ls"}\n```'),
        {action: 'run_command', command: 'ls'});
});

test('rejects non-actions', () => {
    for (const t of ['hello', '{"app":"x"}', '{"action":1}', '{bad', '[1]', ''])
        assert.equal(parseAction(t), null, t);
});

test('stripCodeFence', () => {
    assert.equal(stripCodeFence(' hi '), 'hi');
    assert.equal(stripCodeFence('```\nx\n```'), 'x');
});

test('describeAction', () => {
    assert.equal(describeAction({action: 'open_app', app: 'Files'}), 'Opening Files…');
    assert.equal(describeAction({action: 'run_command', command: 'ls'}), 'Run: ls');
    assert.match(describeAction({action: 'nope'}), /can't/);
});

test('RequestTracker drops cancelled and superseded requests', async () => {
    const {RequestTracker, escapeAction} = await import('./ui-logic.mjs');
    const t = new RequestTracker();
    const a = t.start();
    assert.ok(t.busy && t.isCurrent(a));
    assert.equal(escapeAction(t.busy), 'cancel');
    assert.ok(t.cancel());
    assert.ok(!t.finish(a));
    assert.equal(escapeAction(t.busy), 'close');
    const b = t.start();
    const c = t.start();
    assert.ok(!t.finish(b));
    assert.ok(t.finish(c));
    assert.ok(!t.cancel());
});

test('splitError matches the Python twin', async () => {
    const {splitError} = await import('./ui-logic.mjs');
    assert.deepEqual(splitError(' Hi '), {body: 'Hi', error: null});
    assert.deepEqual(splitError('Logs say [error] here'), {body: 'Logs say [error] here', error: null});
    assert.deepEqual(splitError('[Error: timed out]'), {body: '', error: 'timed out'});
    assert.deepEqual(splitError('Part\n[error] lost'), {body: 'Part', error: 'lost'});
    assert.deepEqual(splitError('[error]'), {body: '', error: 'Something went wrong.'});
});

test('spaceAnnouncement', async () => {
    const {spaceAnnouncement} = await import('./ui-logic.mjs');
    assert.equal(spaceAnnouncement('Code', 3), 'Switched to Code Space. 3 windows open.');
    assert.equal(spaceAnnouncement('Web', 1), 'Switched to Web Space. 1 window open.');
    assert.equal(spaceAnnouncement('Notes', 0), 'Switched to Notes Space. No windows open.');
});

test('window routing helpers', async () => {
    const m = await import('./ui-logic.mjs');
    assert.equal(m.shellMajor('46.2'), 46);
    assert.equal(m.shellMajor('45.beta'), 45);
    assert.equal(m.shellMajor(''), 0);

    assert.equal(m.ruleKeyFor('Firefox.desktop', 'firefox'), 'firefox');
    assert.equal(m.ruleKeyFor(null, 'Code'), 'code');
    assert.equal(m.ruleKeyFor('window:42', 'Gimp'), 'gimp');
    assert.equal(m.ruleKeyFor(null, 'None'), '');

    const rules = {firefox: 'Web', gimp: m.NEVER_ROUTE};
    assert.deepEqual(m.ruleDecision(false, rules, 'firefox'), {kind: 'none'});
    assert.deepEqual(m.ruleDecision(true, rules, 'gimp'), {kind: 'none'});
    assert.deepEqual(m.ruleDecision(true, rules, 'firefox'),
        {kind: 'move', space: 'Web', remembered: true});
    assert.equal(m.ruleDecision(true, rules, 'code'), null);
    assert.equal(m.ruleDecision(true, {}, ''), null);

    assert.deepEqual(m.parseClassification('{"space":"Web","confidence":0.4}'),
        {space: 'Web', confidence: 0.4});
    assert.deepEqual(m.parseClassification('Code'), {space: 'Code', confidence: 1});
    assert.deepEqual(m.parseClassification('{bad'), {space: '', confidence: 0});

    const names = ['Code', 'Web', 'Notes'];
    assert.deepEqual(m.classificationDecision('web', 0.9, names, 'Code'), {kind: 'move', space: 'Web'});
    assert.deepEqual(m.classificationDecision('Web', 0.4, names, 'Code'), {kind: 'ask', space: 'Web'});
    assert.deepEqual(m.classificationDecision('Web', 0.9, names, 'Web'), {kind: 'none'});
    assert.deepEqual(m.classificationDecision('Default', 0.9, names, 'Code'), {kind: 'none'});
    assert.deepEqual(m.classificationDecision('Web', 0, names, 'Code'), {kind: 'none'});

    assert.equal(m.movedMessage('Firefox', 'Web'), 'Firefox moved to Web');
    assert.equal(m.askMessage('Firefox', 'Web'), 'Axon thinks Firefox belongs in Web.');
});

test('windowSummary', async () => {
    const {windowSummary} = await import('./ui-logic.mjs');
    assert.equal(windowSummary([]), 'No windows');
    assert.equal(windowSummary(['Firefox']), '1 window · Firefox');
    assert.equal(windowSummary(['Code', 'Terminal', 'Terminal', 'Firefox']),
        '4 windows · Code · Terminal · Firefox');
    assert.equal(windowSummary(['A', 'B', 'C', 'D', 'E']), '5 windows · A · B · C · +2 more');
    assert.equal(windowSummary(['', 'B']), '2 windows · B');
});

test('history helpers', async () => {
    const m = await import('./ui-logic.mjs');
    assert.deepEqual(m.pushHistory(['b', 'a'], 'a'), ['a', 'b']);
    assert.deepEqual(m.pushHistory(['a'], '  '), ['a']);
    assert.deepEqual(m.recentForEmptyState(['x', m.EXAMPLE_PROMPTS[0], 'x', 'y', 'z', 'w']),
        ['x', 'y', 'z']);
});

test('responsive sizing', async () => {
    const m = await import('./ui-logic.mjs');
    assert.equal(m.intentBarWidth(1024), 480);
    assert.equal(m.intentBarWidth(1366), 615);
    assert.equal(m.intentBarWidth(1920), 820);
    assert.equal(m.intentBarWidth(0), 660);
    // 1280x800 at 125% is 1024x640 logical, minus a 48 px taskbar
    assert.deepEqual(m.fitToWorkArea(620, 520, {width: 1024, height: 592}),
        {width: 620, height: 520});
    assert.deepEqual(m.fitToWorkArea(620, 520, {width: 600, height: 400}),
        {width: 560, height: 360});
    assert.deepEqual(m.fitToWorkArea(620, 520, null), {width: 620, height: 520});
});

test('model origin matches the Python twin', async () => {
    const m = await import('./ui-logic.mjs');
    const providers = [
        {id: 'ollama', label: 'Ollama', base_url: 'http://localhost:11434'},
        {id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1'},
        {id: 'lmstudio', label: 'LM Studio', base_url: 'http://127.0.0.1:1234/v1'},
        {id: 'nas', label: 'NAS', base_url: 'http://192.168.1.20:11434'},
    ];
    assert.equal(m.modelOrigin('llama3.2:3b', providers).label, 'Local · llama3.2:3b');
    assert.equal(m.modelOrigin('@openai/gpt-4.1', providers).label, 'Cloud · OpenAI · gpt-4.1');
    assert.equal(m.modelOrigin('@anthropic/x').kind, 'cloud');
    assert.equal(m.modelOrigin('@lmstudio/q', providers).label, 'Local · LM Studio · q');
    assert.equal(m.modelOrigin('@nas/l', providers).kind, 'remote');
    assert.equal(m.modelOrigin('@openrouter/anthropic/claude').model, 'anthropic/claude');
    for (const [url, kind] of [['http://localhost:1', 'local'], ['http://[::1]:8', 'local'],
        ['http://10.0.0.5', 'remote'], ['http://host.docker.internal:1', 'remote'],
        ['https://api.anthropic.com', 'cloud'], ['https://8.8.8.8', 'cloud']])
        assert.equal(m.hostKind(url), kind, url);
    const tiers = {speed_model: 'llama3.2:1b', general_model: 'llama3.2:3b'};
    assert.equal(m.routingOrigin('', tiers, providers).label, 'Local · llama3.2:3b');
    assert.equal(m.routingOrigin('', {...tiers, deep_model: '@openai/gpt-4.1'}, providers).kind, 'cloud');
    assert.equal(m.routingOrigin('', {}, null).label, 'Model: unknown');
});

test('confirmArgv passes the exact command as its own argument', async () => {
    const {confirmArgv} = await import('./ui-logic.mjs');
    const card = confirmArgv('ls -la', 'You said: list', '/usr/lib/axon/apps/axon-confirm/main.py');
    assert.equal(card[card.indexOf('--command') + 1], 'ls -la');
    assert.equal(card[card.indexOf('--context') + 1], 'You said: list');
    const zen = confirmArgv('ls -la', '', null);
    assert.equal(zen[0], 'zenity');
    assert.ok(zen.includes('--no-markup'));
    assert.ok(zen.at(-1).includes('ls -la'));
});

test('motionDuration', async () => {
    const {motionDuration} = await import('./ui-logic.mjs');
    assert.equal(motionDuration(150, true), 150);
    assert.equal(motionDuration(150, false), 0);
});

test('defaultSuggestionIndex', async () => {
    const m = await import('./ui-logic.mjs');
    const apps = [['firefox.desktop', 'Firefox', 'web']];
    const pick = q => { const rows = m.buildSuggestions(q, apps, ['Code']); return rows[m.defaultSuggestionIndex(rows)].category; };
    assert.equal(pick('fire'), 'Applications');
    assert.equal(pick('tell me a joke'), 'Ask AI');
    assert.equal(pick('find taxes'), 'Files');
    assert.equal(pick('disk'), 'Commands');
});

test('formatCommandOutput', async () => {
    const {formatCommandOutput} = await import('./ui-logic.mjs');
    assert.equal(formatCommandOutput(''), '(no output)');
    assert.equal(formatCommandOutput('a\nb\n'), 'a\nb');
    const long = Array.from({length: 100}, (_, i) => String(i)).join('\n');
    assert.ok(formatCommandOutput(long, 5).endsWith('(output shortened)'));
});

test('spaceChipState never relies on colour alone', async () => {
    const {spaceChipState} = await import('./ui-logic.mjs');
    const active = spaceChipState(0, 'Code', true, 3, false);
    assert.equal(active.number, '1');
    assert.equal(active.badge, '3');
    assert.ok(active.styleClasses.includes('axon-space-chip-active'));
    assert.equal(active.accessibleName, 'Space 1: Code, 3 windows, current');
    const empty = spaceChipState(4, 'Media', false, 0, false);
    assert.ok(empty.styleClasses.includes('axon-space-chip-empty'));
    assert.equal(empty.badge, '');
    assert.equal(empty.accessibleName, 'Space 5: Media, empty');
    const urgent = spaceChipState(2, 'Chat', false, 1, true);
    assert.equal(urgent.badge, '1!');
    assert.match(urgent.accessibleName, /1 window, needs attention$/);
});

test('filterSpaces', async () => {
    const {filterSpaces} = await import('./ui-logic.mjs');
    const spaces = [
        {name: 'Code', windows: ['main.py - VS Code', 'Terminal']},
        {name: 'Web', windows: ['Inbox - Firefox']},
    ];
    assert.equal(filterSpaces('', spaces).length, 2);
    assert.deepEqual(filterSpaces('2', spaces).map(r => r.name), ['Web']);
    assert.deepEqual(filterSpaces('cod', spaces).map(r => [r.name, r.window]), [['Code', null]]);
    assert.deepEqual(filterSpaces('inbox', spaces), [{index: 1, name: 'Web', window: 'Inbox - Firefox', windowIndex: 0}]);
    assert.deepEqual(filterSpaces('zzz', spaces), []);
});

test('announcement', async () => {
    const {announcement} = await import('./ui-logic.mjs');
    assert.equal(announcement('answer', 'Four.\n\nMore'), 'Axon answered: Four. More');
    assert.equal(announcement('command', 'Not run.'), 'Command not run.');
    assert.equal(announcement('cancelled'), 'Cancelled.');
});

test('setTranslator routes Shell strings through gettext and fills placeholders', async () => {
    const {_t, setTranslator, describeAction: describe} = await import('./ui-logic.mjs');
    assert.equal(_t('Open {app}', {app: 'Files'}), 'Open Files');
    assert.equal(_t('Keep {missing}', {}), 'Keep {missing}');
    assert.equal(_t('Price {p}', {p: '$1'}), 'Price $1');
    setTranslator(msgid => (msgid === 'Opening {app}…' ? 'Öffne {app}…' : msgid));
    try {
        assert.equal(describe({action: 'open_app', app: 'Firefox'}), 'Öffne Firefox…');
    } finally {
        setTranslator(null);
    }
    assert.equal(describe({action: 'open_app', app: 'Firefox'}), 'Opening Firefox…');
});

test('motion timings follow the rules', async () => {
    const {MOTION, PRESS_SCALE, ENTER_SCALE, tooltipDelay} = await import('./ui-logic.mjs');
    assert.ok(MOTION.keyboard <= 60);
    assert.ok(MOTION.exit < MOTION.enter);
    assert.ok(Object.values(MOTION).every(ms => ms > 0 && ms <= 300));
    assert.ok(PRESS_SCALE >= 0.95 && PRESS_SCALE < 1);
    assert.ok(ENTER_SCALE >= 0.9 && ENTER_SCALE < 1);
    assert.equal(tooltipDelay(10_000, null), 400);
    assert.equal(tooltipDelay(10_000, 9_700), 0);
    assert.equal(tooltipDelay(10_000, 9_000), 400);
});

test('the taskbar floats inside the screen edge and turns solid on request', async () => {
    const {taskbarGeometry, useMaterial} = await import('./ui-logic.mjs');
    const monitor = {x: 0, y: 0, width: 1920, height: 1080};
    // The chrome actor touches the bottom edge (struts) and is the bar plus the gap
    assert.deepEqual(taskbarGeometry(monitor, 48, 8), {x: 0, y: 1024, width: 1920, height: 56, inset: 8});
    // Tiny screens stay flush
    assert.equal(taskbarGeometry({x: 0, y: 0, width: 640, height: 480}, 48, 8).inset, 0);
    // A second monitor keeps its own origin
    assert.equal(taskbarGeometry({x: 1920, y: 0, width: 1280, height: 1024}, 48, 8).y, 968);
    assert.equal(useMaterial(false, false), true);
    assert.equal(useMaterial(true, false), false);
    assert.equal(useMaterial(false, true), false);
});
