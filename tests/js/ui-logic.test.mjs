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
