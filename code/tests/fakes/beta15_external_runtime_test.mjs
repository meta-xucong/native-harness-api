/** Runtime assembly fixtures only. No vendor import, subprocess or network. */
import assert from 'node:assert/strict';
import { createExternalRuntime, RuntimeSetupError } from '../../tools/beta15_external_runtime.mjs';
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function fixture(overrides = {}) {
  const calls = [], handlers = {}, lines = [], done = deferred();
  const harness = { closed: done.promise, close() { calls.push('harness.close'); done.resolve(); } };
  const client = { initialization: { protocolVersion: 1 }, on(name, handler) {
      if (Object.hasOwn(handlers,name)) throw new Error('duplicate handler');
      handlers[name] = handler; calls.push(`on:${name}`);
      return () => { if(handlers[name]===handler) delete handlers[name]; };
    },
    async connect(h, options) { assert.equal(h, harness); assert.ok(options.timeoutMs > 0); calls.push('connect'); },
    async close() { calls.push('client.close'); }, session: {
      async new() { calls.push('session.new'); return { sessionId: 'root', configOptions: [{ configId: 'model', type: 'select', currentValue: 'synthetic-a', options: [{ value: 'synthetic-a', name: 'A' }] }] }; },
      async setConfigOption() { throw new Error('unused'); }, async prompt() { throw new Error('unused'); }, async cancel() {}, async close() { return {}; }
    }
  };
  const api = { createHarness(options) { calls.push('createHarness'); assert.equal(options.strictMcpConfig, true); assert.equal(options.configDir, '/synthetic/config'); assert.ok(options.signal instanceof AbortSignal); return harness; },
    createClient(options) { calls.push('createClient'); assert.deepEqual(options, { account: { mode: 'external' }, product: { mode: 'external' }, capabilities: { elicitation: { form: {}, url: {} } } }); return client; }
  };
  const options = { verifyEntry: async () => { calls.push('verify'); return true; }, loadApi: async () => { calls.push('load'); return api; }, configDir: '/synthetic/config', expectedCwd: '/synthetic/empty', emit: line => { lines.push(JSON.parse(line)); }, timeoutMs: 200, cleanupMs: 200, ...overrides };
  return { options, api, harness, client, done, calls, handlers, lines };
}
const shutdown = { id: 2147483647, method: 'shim/close', params: {} };
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
test('verified external initialization and bounded close assembly', async () => {
  const f = fixture(); const runtime = await createExternalRuntime(f.options);
  assert.deepEqual(f.calls.slice(0, 4), ['verify', 'load', 'createHarness', 'createClient']);
  assert.ok(f.calls.indexOf('on:elicitation/create') < f.calls.indexOf('connect'));
  assert.ok(f.calls.indexOf('on:session/request_permission') < f.calls.indexOf('connect'));
  assert.equal(runtime.state().externalOnly, true); assert.equal(runtime.state().protocolVersion, 1);
  await runtime.dispatch({ id: 1, method: 'session/new', params: { cwd: '/synthetic/empty', mcpServers: [] } });
  await runtime.dispatch(shutdown);
  assert.deepEqual(f.lines.at(-1), { id: 2147483647, result: { closed: true } });
  assert.equal(f.calls.filter(x => x === 'client.close').length, 1); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
  await runtime.close(); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
test('verification must explicitly pass before import', async () => {
  for (const value of [false, undefined, {}, 'true']) {
    const f = fixture({ verifyEntry: async () => value });
    await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
    assert.equal(f.calls.includes('load'), false);
  }
});
test('load failure has finite output and no account construction', async () => {
  const f = fixture({ loadApi: async () => { throw new Error('SECRET'); } });
  await assert.rejects(createExternalRuntime(f.options), error => error.code === 'EXTERNAL_RUNTIME_REJECTED' && !error.message.includes('SECRET'));
  assert.equal(f.calls.includes('createHarness'), false);
});
test('wrong protocol closes both resources', async () => {
  const f = fixture(); f.client.initialization.protocolVersion = 2;
  await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
  assert.equal(f.calls.filter(x => x === 'client.close').length, 1); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
test('elicitation before ready cancels without reading request and rejects init', async () => {
  const f = fixture(); f.client.connect = async () => {
    const request = new Proxy({}, { get() { throw new Error('read'); } });
    assert.deepEqual(f.handlers['elicitation/create'](request), { action: 'cancel' });
  };
  await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
  assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
test('late createHarness gets close exactly once and is never returned', async () => {
  const f = fixture({ timeoutMs: 5, cleanupMs: 20 }); const late = deferred();
  f.api.createHarness = () => late.promise;
  await assert.rejects(createExternalRuntime(f.options), error => error.code === 'EXTERNAL_RUNTIME_TIMEOUT');
  late.resolve(f.harness); await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(f.calls.filter(x => x === 'harness.close').length, 1); assert.equal(f.calls.includes('createClient'), false);
});
test('connect timeout aborts and closes resources', async () => {
  const f = fixture({ timeoutMs: 5, cleanupMs: 20 });
  f.client.connect = () => new Promise(() => {});
  await assert.rejects(createExternalRuntime(f.options), error => error.code === 'EXTERNAL_RUNTIME_TIMEOUT');
  assert.equal(f.calls.filter(x => x === 'client.close').length, 1); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
test('failed client close still closes harness and never acknowledges success', async () => {
  const f = fixture(); f.client.close = async () => { f.calls.push('client.close'); throw new Error('SECRET_CLOSE'); };
  const runtime = await createExternalRuntime(f.options); await runtime.dispatch(shutdown);
  assert.ok(f.lines.find(x => x.id === 2147483647).error); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
  assert.equal(JSON.stringify(f.lines).includes('SECRET'), false);
});
test('expired cleanup still invokes every close once', async () => {
  let time = 0; const f = fixture({ now: () => time, cleanupMs: 5 });
  f.client.close = () => { f.calls.push('client.close'); time += 10; return undefined; };
  const runtime = await createExternalRuntime(f.options); await runtime.dispatch(shutdown);
  assert.ok(f.lines.find(x => x.id === 2147483647).error);
  assert.equal(f.calls.filter(x => x === 'client.close').length, 1); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
test('harness closed rejection is observed and fails shutdown', async () => {
  const f = fixture(); f.harness.close = () => { f.calls.push('harness.close'); f.done.reject(new Error('PRIVATE')); };
  const runtime = await createExternalRuntime(f.options); await runtime.dispatch(shutdown);
  assert.ok(f.lines.find(x => x.id === 2147483647).error); assert.equal(JSON.stringify(f.lines).includes('PRIVATE'), false);
});
test('out-of-band close fences future commands and is exactly once', async () => {
  const f = fixture(); const runtime = await createExternalRuntime(f.options);
  const a = runtime.close(), b = runtime.close(); assert.equal(await a, true); assert.equal(await b, true);
  await runtime.dispatch({ id: 1, method: 'session/new', params: { cwd: '/synthetic/empty', mcpServers: [] } });
  assert.equal(f.calls.includes('session.new'), false); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
test('invalid or throwing monotonic clock never leaks dynamic error', async () => {
  for (const now of [() => NaN, () => { throw new Error('PRIVATE_CLOCK'); }]) {
    const f = fixture({ now }); await assert.rejects(createExternalRuntime(f.options), error => error instanceof RuntimeSetupError && !error.message.includes('PRIVATE'));
  }
});
test('bad budgets reject before verification', async () => {
  for (const timeoutMs of [0, NaN, 30001, true]) {
    const f = fixture({ timeoutMs }); await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError); assert.equal(f.calls.length, 0);
  }
});
let passed = 0;
for (const [name, fn] of tests) { await fn(); passed++; console.log(`ok ${passed} - ${name}`); }
console.log(`passed ${passed} vendor-free external runtime scenarios`);
