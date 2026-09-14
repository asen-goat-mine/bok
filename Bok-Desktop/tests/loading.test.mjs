import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const html = readFileSync(new URL('../frontend/loading.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function fixture() {
  const nodes = Object.fromEntries(['#status', '#bar', '#retry'].map(id => [id, {
    hidden: id === '#retry', disabled: false, textContent: '', classes: new Set(),
    classList: {add(name) {nodes[id].classes.add(name);}, remove(name) {nodes[id].classes.delete(name);}},
    addEventListener(_event, callback) {this.click = callback;},
  }]));
  let snapshotResolve, snapshotReject, retryReject;
  const calls = [];
  const window = {__TAURI__: {core: {invoke(command) {
    calls.push(command);
    return command === 'startup_status'
      ? new Promise((resolve, reject) => {snapshotResolve = resolve; snapshotReject = reject;})
      : new Promise((_resolve, reject) => {retryReject = reject;});
  }}}};
  vm.runInNewContext(script, {window, document: {querySelector: id => nodes[id]}});
  return {window, nodes, calls, snapshot: value => snapshotResolve(value),
    snapshotFailure: () => snapshotReject(new Error('snapshot failed')),
    retryFailure: () => retryReject(new Error('retry failed'))};
}

test('a failure before loading-script registration is recovered from native state', async () => {
  const f = fixture();
  f.snapshot({revision: 1, error: true, message: '服务已退出'});
  await new Promise(setImmediate);
  assert.equal(f.nodes['#status'].textContent, '服务已退出');
  assert.equal(f.nodes['#retry'].hidden, false);
  assert.equal(f.nodes['#retry'].disabled, false);
});

test('an older snapshot cannot replace a newer pushed startup state', async () => {
  const f = fixture();
  f.window.showStartupStatus({revision: 2, error: true, message: '服务已退出'});
  f.snapshot({revision: 1, error: false, message: '仍在等待'});
  await new Promise(setImmediate);
  assert.equal(f.nodes['#status'].textContent, '服务已退出');
  assert.equal(f.nodes['#bar'].hidden, true);
});

test('a failed native retry restores a usable retry button', async () => {
  const f = fixture(); f.snapshot({revision: 1, error: true, message: '请重试'});
  await new Promise(setImmediate);
  const pending = f.nodes['#retry'].click();
  assert.equal(f.nodes['#retry'].disabled, true);
  f.retryFailure(); await pending;
  assert.equal(f.nodes['#retry'].disabled, false);
  assert.equal(f.nodes['#retry'].hidden, false);
  assert.ok(f.calls.includes('retry_startup'));
});

test('a failed older snapshot does not hide a more specific native error', async () => {
  const f = fixture();
  f.window.showStartupStatus({revision: 2, error: true, message: '服务已退出'});
  f.snapshotFailure(); await new Promise(setImmediate);
  assert.equal(f.nodes['#status'].textContent, '服务已退出');
});
