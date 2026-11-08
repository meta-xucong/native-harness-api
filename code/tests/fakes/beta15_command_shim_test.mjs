/** Self-authored client fixtures only; no vendor code import or launch. */
import assert from 'node:assert/strict';
import { boundedCopy, createCommandShim } from '../../tools/beta15_command_shim.mjs';

const catalog = (current = 'synthetic-a') => [{ configId: 'model', type: 'select', currentValue: current,
  options: [{ value: 'synthetic-a', name: 'A' }, { value: 'synthetic-b', name: 'B' }] }];
const command = (id, method, params) => ({ id, method, params });
const fresh = () => command(1, 'session/new', { cwd: '/synthetic/empty', mcpServers: [] });
const select = () => command(2, 'session/set_config_option', { sessionId: 'root', configId: 'model', type: 'id', value: 'synthetic-b' });
const prompt = () => command(3, 'session/prompt', { sessionId: 'root', prompt: [{ type: 'text', text: 'synthetic input' }] });
const close = () => command(4, 'session/close', { sessionId: 'root' });
const shutdown = () => command(2147483647, 'shim/close', {});
const cancel = () => ({ method: 'session/cancel', params: { sessionId: 'root' } });
const ack = () => ({ messageId: 'inserted-message', _meta: { 'codebuddy.ai/requestId': 'run' } });
const update = (kind = 'agent_message_chunk', extra = {}) => ({ sessionId: 'root', update: {
  sessionUpdate: kind, messageId: 'answer', _meta: { 'codebuddy.ai/requestId': 'run' }, content: { type: 'text', text: 'synthetic answer' }, ...extra } });
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function setup(overrides = {}, closeRuntimeOverride) {
  const handlers = {}, calls = [], messages = [];
  const client = { on(name, handler) { handlers[name] = handler; }, session: {
    async new(p) { calls.push(['new', p]); return { sessionId: 'root', configOptions: catalog() }; },
    async setConfigOption(p) { calls.push(['select', p]); return { configOptions: catalog('synthetic-b') }; },
    async prompt(p) { calls.push(['prompt', p]); return ack(); },
    async cancel(p) { calls.push(['cancel', p]); },
    async close(p) { calls.push(['close', p]); return {}; },
    ...overrides
  } };
  const shim = createCommandShim({ client, expectedCwd: '/synthetic/empty', emit(line) { messages.push(JSON.parse(line)); },
    closeRuntime: closeRuntimeOverride || (async () => { calls.push(['shutdown']); return true; }) });
  return { shim, handlers, calls, messages, client };
}
async function ready(f) { await f.shim.dispatch(fresh()); await f.shim.dispatch(select()); return f; }
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('normal command mappings, updates and shutdown', async () => {
  const f = await ready(setup());
  await f.shim.dispatch(prompt()); f.handlers['session/update'](update());
  await f.shim.dispatch(cancel()); await f.shim.dispatch(close()); await f.shim.dispatch(shutdown());
  assert.deepEqual(f.calls.map(x => x[0]), ['new', 'select', 'prompt', 'cancel', 'close', 'shutdown']);
  assert.deepEqual(f.messages.map(x => x.id ?? x.method), [1, 2, 3, 'session/update', 4, 2147483647]);
  assert.deepEqual(f.messages.at(-1), { id: 2147483647, result: { closed: true } });
  assert.equal(f.shim.state().fault, null); assert.equal(f.shim.state().retrySafe, false);
});
test('early prompt events remain before acknowledgement', async () => {
  const pending = deferred(); const f = setup({ prompt() { f.handlers['session/update'](update()); return pending.promise; } });
  await ready(f); const running = f.shim.dispatch(prompt());
  assert.equal(f.messages.at(-1).method, 'session/update');
  assert.ok(!f.messages.some(x => x.id === 3));
  pending.resolve(ack()); await running; assert.equal(f.messages.at(-1).id, 3);
});
test('pending prompt does not serialize cancel or close', async () => {
  const pending = deferred(); const f = await ready(setup({ prompt() { return pending.promise; } }));
  const running = f.shim.dispatch(prompt());
  await f.shim.dispatch(cancel()); await f.shim.dispatch(close());
  assert.deepEqual(f.calls.map(x => x[0]), ['new', 'select', 'cancel', 'close']);
  assert.ok(f.messages.some(x => x.id === 4));
  pending.resolve(ack()); await running;
});
test('shutdown overlaps pending new and fences every admission', async () => {
  const pending = deferred(); let count = 0;
  const f = setup({ new() { return pending.promise; } }, async () => { count++; return true; });
  const opening = f.shim.dispatch(fresh());
  const closing = f.shim.dispatch(shutdown());
  await Promise.resolve();
  assert.ok(!f.messages.some(x => x.id === 2147483647));
  pending.resolve({ sessionId: 'root', configOptions: catalog() }); await opening; await closing;
  await f.shim.dispatch(select()); assert.equal(count, 1);
  assert.equal(f.calls.length, 0); assert.equal(f.shim.state().modelConfirmed, false);
  await f.shim.dispatch(shutdown()); assert.equal(count, 1);
});
test('late selection response cannot reopen after close', async () => {
  const pending = deferred(); const f = setup({ setConfigOption() { return pending.promise; } });
  await f.shim.dispatch(fresh()); const selecting = f.shim.dispatch(select());
  await f.shim.dispatch(close()); pending.resolve({ configOptions: catalog('synthetic-b') }); await selecting;
  await f.shim.dispatch(prompt()); assert.equal(f.calls.some(x => x[0] === 'prompt'), false);
  assert.equal(f.shim.state().modelConfirmed, false);
});
test('late selection response cannot reopen after shutdown', async () => {
  const pending = deferred(); const f = setup({ setConfigOption() { return pending.promise; } });
  await f.shim.dispatch(fresh()); const selecting = f.shim.dispatch(select());
  const closing = f.shim.dispatch(shutdown()); pending.resolve({ configOptions: catalog('synthetic-b') }); await selecting; await closing;
  await f.shim.dispatch(prompt()); assert.equal(f.calls.some(x => x[0] === 'prompt'), false);
  assert.equal(f.shim.state().modelConfirmed, false);
});
test('shutdown acknowledgement follows pending response and fences queued updates', async () => {
  const pending = deferred(); const f = await ready(setup({ prompt() { return pending.promise; } }));
  const running = f.shim.dispatch(prompt()); const closing = f.shim.dispatch(shutdown());
  await Promise.resolve(); assert.ok(!f.messages.some(x => x.id === 2147483647));
  pending.resolve(ack()); await running; await closing;
  assert.equal(f.messages.at(-1).id, 2147483647);
  const count = f.messages.length; f.handlers['session/update'](update('state_update', {state:'idle',stopReason:'cancelled'}));
  assert.equal(f.messages.length, count + 1); assert.equal(f.messages.at(-1).method, 'shim/fault');
  assert.equal(f.messages.at(-1).params.code, 'POST_SHUTDOWN_ACTIVITY');
});
test('post-shutdown permission and elicitation callbacks fault and cancel', async () => {
  for (const name of ['session/request_permission', 'elicitation/create']) {
    const f = setup(); await f.shim.dispatch(shutdown());
    const result = f.handlers[name](new Proxy({}, {get(){throw new Error('read');}}));
    assert.equal(f.messages.at(-1).params.code, 'POST_SHUTDOWN_ACTIVITY');
    assert.deepEqual(result, name === 'elicitation/create' ? {action:'cancel'} : {outcome:{outcome:'cancelled'}});
  }
});
test('no prompt without a correlated selection result' , async () => {
  const f = setup(); await f.shim.dispatch(fresh()); await f.shim.dispatch(prompt());
  assert.equal(f.calls.some(x => x[0] === 'prompt'), false); assert.ok(f.shim.state().fault);
});
test('new selection prompt and cancel are each single attempt', async () => {
  for (const stage of ['new', 'select', 'prompt', 'cancel']) {
    const f = setup(); await f.shim.dispatch(fresh());
    if (stage !== 'new') await f.shim.dispatch(select());
    if (stage === 'prompt' || stage === 'cancel') await f.shim.dispatch(prompt());
    if (stage === 'cancel') await f.shim.dispatch(cancel());
    const duplicate = stage === 'new' ? fresh() : stage === 'select' ? select() : stage === 'prompt' ? prompt() : cancel();
    await f.shim.dispatch(duplicate);
    const label = stage === 'select' ? 'select' : stage;
    assert.equal(f.calls.filter(x => x[0] === label).length, 1);
    assert.ok(f.shim.state().fault);
  }
});
test('wrong cwd session model extra keys and notification id are rejected', async () => {
  for (const mutate of [q => { q.params.cwd = '/other'; }, q => { q.params.mcpServers = [{}]; }, q => { q.params.extra = true; }, q => { q.id = true; }, q => { q.id = 2147483647; }]) {
    const f = setup(); const q = fresh(); mutate(q); await f.shim.dispatch(q); assert.equal(f.calls.length, 0); assert.ok(f.shim.state().fault);
  }
  for (const mutate of [q => { q.params.sessionId = 'other'; }, q => { q.params.value = 'invented'; }, q => { q.params.type = 'index'; }]) {
    const f = setup(); await f.shim.dispatch(fresh()); const q = select(); mutate(q); await f.shim.dispatch(q);
    assert.equal(f.calls.some(x => x[0] === 'select'), false);
  }
  const f = await ready(setup()); await f.shim.dispatch(prompt()); await f.shim.dispatch({ id: 10, ...cancel() });
  assert.equal(f.calls.some(x => x[0] === 'cancel'), false);
});
test('permissions and elicitations always cancel without reading input', async () => {
  const f = setup(); const input = new Proxy({}, { get() { throw new Error('must not read'); } });
  assert.deepEqual(f.handlers['session/request_permission'](input), { outcome: { outcome: 'cancelled' } });
  assert.deepEqual(f.handlers['elicitation/create'](input), { action: 'cancel' });
  assert.equal(f.calls.length, 0); assert.equal(f.messages.length, 0);
});
test('matching model update invalidates, foreign session ignored', async () => {
  const f = await ready(setup());
  const change = { sessionId: 'other', update: { sessionUpdate: 'config_option_update', configOptions: catalog() } };
  f.handlers['session/update'](change); assert.equal(f.shim.state().fault, null);
  change.sessionId = 'root'; f.handlers['session/update'](change);
  assert.equal(f.shim.state().fault, 'MODEL_EVIDENCE_LOST');
  await f.shim.dispatch(prompt()); assert.equal(f.calls.some(x => x[0] === 'prompt'), false);
  await f.shim.dispatch(close()); assert.equal(f.calls.filter(x => x[0] === 'close').length, 1);
});
test('native errors are finite and no raw message or stack is emitted', async () => {
  const f = setup({ new() { throw new Error('SECRET_PATH_AND_VALUE'); } });
  await f.shim.dispatch(fresh()); assert.ok(f.shim.state().fault);
  assert.equal(JSON.stringify(f.messages).includes('SECRET'), false);
  assert.equal(f.messages[0].error.code, 'NATIVE_RPC_FAILED');
  await f.shim.dispatch(shutdown()); assert.equal(f.messages.at(-1).result.closed, true);
});
test('model readback mismatch prevents native prompt', async () => {
  const f = setup({ async setConfigOption() { return { configOptions: catalog() }; } });
  await ready(f); await f.shim.dispatch(prompt()); assert.equal(f.calls.some(x => x[0] === 'prompt'), false);
});
test('malformed ACK and close cannot claim success', async () => {
  const f = await ready(setup({ async prompt() { return { messageId: 'm' }; } }));
  await f.shim.dispatch(prompt()); assert.ok(f.shim.state().fault); assert.ok(f.messages.find(x => x.id === 3).error);
  const g = await ready(setup({ async close() { return { _meta: [] }; } }));
  await g.shim.dispatch(close()); assert.ok(g.messages.find(x => x.id === 4).error);
  for (const result of [{}, { _meta: {} }, { _meta: null }]) {
    const h = await ready(setup({ async close() { return result; } })); await h.shim.dispatch(close()); assert.ok(h.messages.find(x => x.id === 4).result);
  }
});
test('shutdown callback requires explicit true and rejects safely', async () => {
  for (const closeRuntime of [async () => false, async () => undefined, async () => { throw new Error('private'); }]) {
    const f = setup({}, closeRuntime); await f.shim.dispatch(shutdown());
    assert.ok(f.messages.find(x => x.id === 2147483647).error);
    assert.equal(JSON.stringify(f.messages).includes('private'), false);
  }
});
test('shutdown hangs only its promise not synchronous admission fence', async () => {
  const pending = deferred(); const f = setup({}, () => pending.promise);
  const closing = f.shim.dispatch(shutdown()); await f.shim.dispatch(fresh());
  assert.equal(f.calls.length, 0); pending.resolve(true); await closing;
});
test('output frame limit is sticky and bounded', async () => {
  const f = await ready(setup()); await f.shim.dispatch(prompt());
  for (let i = 0; i < 600; i++) f.handlers['session/update'](update());
  assert.equal(f.shim.state().fault, 'OUTPUT_LIMIT'); assert.ok(f.messages.length <= 512);
  assert.equal(f.messages.at(-1).method, 'shim/fault');
});
test('oversize update fails closed without reflecting it', async () => {
  const f = await ready(setup()); const event = update(); event.update.content.text = 'x'.repeat(262145);
  f.handlers['session/update'](event); assert.equal(f.shim.state().fault, 'INVALID_UPDATE');
  assert.ok(JSON.stringify(f.messages).length < 2000);
});
test('malformed updates and stopped shim cannot restart', async () => {
  const f = await ready(setup()); f.handlers['session/update']({ sessionId: 'root' });
  assert.equal(f.shim.state().fault, 'INVALID_UPDATE'); await f.shim.dispatch(prompt()); assert.equal(f.calls.some(x => x[0] === 'prompt'), false);
  const g = setup(); g.shim.stop(); await g.shim.dispatch(fresh()); assert.equal(g.calls.length, 0);
});
test('copies are immutable snapshots and reject accessors or non-json values', async () => {
  const original = { a: [{ b: 'ok' }] }; const copied = boundedCopy(original); original.a[0].b = 'changed'; assert.equal(copied.a[0].b, 'ok');
  const bad = { get secret() { throw new Error('getter ran'); } };
  for (const value of [bad, { toJSON() { throw new Error('toJSON ran'); } }, { x: undefined }, { x: NaN }, { x: 1n }, { x: '\ud800' }, new Date(), [ , 1 ]]) assert.throws(() => boundedCopy(value), /BETA15_SHIM_CONTRACT/);
  const cycle = {}; cycle.x = cycle; assert.throws(() => boundedCopy(cycle), /BETA15_SHIM_CONTRACT/);
  const proto = boundedCopy(JSON.parse('{"__proto__":{"polluted":true}}')); assert.equal(Object.prototype.polluted, undefined); assert.equal(proto.__proto__.polluted, true);
});
test('transport failure is not swallowed as success', async () => {
  const base = setup(); const f = createCommandShim({ client: base.client, expectedCwd: '/synthetic/empty', closeRuntime: async () => true, emit() { throw new Error('private output'); } });
  await f.dispatch(fresh()); assert.equal(f.state().fault, 'TRANSPORT_FAILED'); await f.dispatch(select());
  assert.equal(base.calls.filter(x => x[0] === 'select').length, 0);
});

let passed = 0;
for (const [name, fn] of tests) { await fn(); passed++; console.log(`ok ${passed} - ${name}`); }
console.log(`passed ${passed} vendor-free beta15 shim scenarios`);
