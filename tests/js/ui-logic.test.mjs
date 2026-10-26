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
