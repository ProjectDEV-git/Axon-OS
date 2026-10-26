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
