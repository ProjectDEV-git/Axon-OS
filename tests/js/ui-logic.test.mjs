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
