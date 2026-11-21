/** Runtime assembly fixtures only. No vendor import, subprocess or network. */
import assert from 'node:assert/strict';
import { createExternalRuntime, RuntimeSetupError } from '../../tools/beta15_external_runtime.mjs';
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function productProjection() {
  return { schemaVersion: 1, kind: 'overlay', configuration: {
    productName: 'WorkBuddy', applicationName: 'WorkBuddy', productVersion: '5.9.3',
    authentication: { id: 'workbuddy-desktop', type: 'account-scoped-external-link', attributes: { platform: 'workbuddy' } },
    platform: 'synthetic', endpoint: 'https://synthetic.invalid', models: { synthetic: { label: 'fixture' } },
    config: { retained: { flags: [true, false], value: 'complete fixture config' } }
  } };
}
function productState() { return { state: 'ready', mode: 'managed', productName: 'WorkBuddy', applicationName: 'WorkBuddy', productVersion: '5.9.3' }; }
function fixture(overrides = {}) {
  const calls = [], handlers = {}, lines = [], done = deferred();
  const product = productProjection(); const originalProduct = structuredClone(product);
  const harness = { closed: done.promise, close() { calls.push('harness.close'); done.resolve(); } };
  const client = { initialization: { protocolVersion: 1 }, on(name, handler) {
      if (Object.hasOwn(handlers,name)) throw new Error('duplicate handler');
      handlers[name] = handler; calls.push(`on:${name}`);
      return () => { if(handlers[name]===handler) delete handlers[name]; };
    },
    async connect(h, options) { assert.equal(h, harness); assert.ok(options.timeoutMs > 0); calls.push('connect'); },
    async close() { calls.push('client.close'); },
    product: { async read(params) { assert.deepEqual(params, {}); calls.push('product.read'); return productState(); } }, session: {
      async new() { calls.push('session.new'); return { sessionId: 'root', configOptions: [{ configId: 'model', type: 'select', currentValue: 'synthetic-a', options: [{ value: 'synthetic-a', name: 'A' }] }] }; },
      async setConfigOption() { throw new Error('unused'); }, async prompt() { throw new Error('unused'); }, async cancel() {}, async close() { return {}; }
    }
  };
  const api = { createHarness(options) { calls.push('createHarness'); assert.equal(options.strictMcpConfig, true); assert.equal(options.configDir, '/synthetic/config'); assert.deepEqual(options.productConfig, originalProduct); assert.ok(options.signal instanceof AbortSignal); return harness; },
    createClient(options) { calls.push('createClient'); assert.deepEqual(options, { account: { mode: 'external' }, product: { mode: 'managed' }, capabilities: { elicitation: { form: {}, url: {} } } }); return client; }
  };
  const options = { verifyEntry: async () => { calls.push('verify'); return true; }, loadProduct: async () => { calls.push('loadProduct'); return product; }, loadApi: async () => { calls.push('load'); return api; }, configDir: '/synthetic/config', expectedCwd: '/synthetic/empty', emit: line => { lines.push(JSON.parse(line)); }, timeoutMs: 200, cleanupMs: 200, ...overrides };
  return { options, api, harness, client, done, calls, handlers, lines, product };
}
const shutdown = { id: 2147483647, method: 'shim/close', params: {} };
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
test('verified external initialization and bounded close assembly', async () => {
  const f = fixture(); const runtime = await createExternalRuntime(f.options);
  assert.deepEqual(f.calls.slice(0, 5), ['verify', 'loadProduct', 'load', 'createHarness', 'createClient']);
  assert.ok(f.calls.indexOf('connect') < f.calls.indexOf('product.read'));
  assert.equal(f.calls.filter(x => x === 'product.read').length, 1);
  assert.equal(f.calls.includes('session.new'), false);
  assert.ok(f.calls.indexOf('on:elicitation/create') < f.calls.indexOf('connect'));
  assert.ok(f.calls.indexOf('on:session/request_permission') < f.calls.indexOf('connect'));
  assert.equal(runtime.state().externalAccount, true); assert.equal(runtime.state().managedProduct, true);
  assert.equal(runtime.state().productReady, true); assert.equal(runtime.state().productIdentityVerified, true);
  assert.equal(runtime.state().authMethodsEmpty, true); assert.equal(Object.hasOwn(runtime.state(), 'externalOnly'), false);
  assert.equal(runtime.state().protocolVersion, 1);
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
  const f = fixture({ timeoutMs: 5, cleanupMs: 20, now: () => 0 }); const late = deferred();
  f.api.createHarness = () => late.promise;
  await assert.rejects(createExternalRuntime(f.options), error => error.code === 'EXTERNAL_RUNTIME_TIMEOUT');
  late.resolve(f.harness); await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(f.calls.filter(x => x === 'harness.close').length, 1); assert.equal(f.calls.includes('createClient'), false);
});
test('connect timeout aborts and closes resources', async () => {
  const f = fixture({ timeoutMs: 5, cleanupMs: 20, now: () => 0 });
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
test('mandatory Product loader must exist before verification begins', async () => {
  const f = fixture({ loadProduct: undefined });
  await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError); assert.equal(f.calls.length, 0);
});
test('malformed or wrong Product projections reject before API loading', async () => {
  const mutations = [p => { p.schemaVersion = '1'; }, p => { p.kind = 'base'; }, p => { p.configuration = []; },
    p => { p.configuration.productName = 'Other'; }, p => { p.configuration.applicationName = 'Other'; },
    p => { p.configuration.productVersion = '5.9.4'; }, p => { p.configuration.authentication.id = 'other'; },
    p => { p.configuration.authentication.type = 'managed'; }, p => { p.configuration.authentication.attributes.platform = 'other'; }];
  for (const mutate of mutations) {
    const f = fixture(); mutate(f.product);
    await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
    assert.equal(f.calls.includes('load'), false); assert.equal(f.calls.includes('createHarness'), false);
  }
});
test('unstripped override controls reject at config or envelope', async () => {
  for (const key of ['include', 'productConfigEnv', 'productConfigPathEnv', 'replacements', 'mergeStrategy']) {
    for (const location of ['configuration', 'envelope']) {
      const f = fixture(); (location === 'configuration' ? f.product.configuration : f.product)[key] = null;
      await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError); assert.equal(f.calls.includes('load'), false);
    }
  }
});
test('full projected configuration is retained and detached before API import', async () => {
  const f = fixture(); const expected = structuredClone(f.product);
  expected.configuration.largeRetained = 'x'.repeat(300000); f.product.configuration.largeRetained = expected.configuration.largeRetained;
  f.options.loadApi = async () => { f.product.configuration.endpoint = 'https://changed.invalid'; return f.api; };
  f.api.createHarness = options => { assert.deepEqual(options.productConfig, expected); return f.harness; };
  const runtime = await createExternalRuntime(f.options); await runtime.close();
});
test('oversized Product and accessor properties reject without executing getters', async () => {
  const f = fixture(); f.product.configuration.large = 'x'.repeat(1024 * 1024);
  await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError); assert.equal(f.calls.includes('load'), false);
  const g = fixture(); let touched = false;
  Object.defineProperty(g.product.configuration, 'secret', { enumerable: true, get() { touched = true; throw new Error('PRIVATE'); } });
  await assert.rejects(createExternalRuntime(g.options), RuntimeSetupError); assert.equal(touched, false); assert.equal(g.calls.includes('load'), false);
});
test('Product loading timeout cannot later import or construct resources', async () => {
  const pending = deferred(); const f = fixture({ timeoutMs: 5, now: () => 0, loadProduct: () => pending.promise });
  await assert.rejects(createExternalRuntime(f.options), error => error.code === 'EXTERNAL_RUNTIME_TIMEOUT');
  pending.resolve(f.product); await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(f.calls.includes('load'), false); assert.equal(f.calls.includes('createHarness'), false);
});
test('authMethods must be omitted or an empty array, not login evidence', async () => {
  for (const value of [undefined, []]) {
    const f = fixture(); f.client.initialization.authMethods = value;
    const runtime = await createExternalRuntime(f.options); assert.equal(runtime.state().authMethodsEmpty, true); await runtime.close();
  }
  for (const value of [null, false, {}, '', [{ id: 'login' }]]) {
    const f = fixture(); f.client.initialization.authMethods = value;
    await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
    assert.equal(f.calls.includes('product.read'), false); assert.equal(f.calls.includes('session.new'), false);
    assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
  }
});
test('auth advertisement changing during Product read cannot expose a runtime', async () => {
  const f = fixture(); f.client.product.read = async () => { f.client.initialization.authMethods = [{id:'login'}]; return productState(); };
  await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
  assert.equal(f.calls.includes('session.new'), false); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
test('managed Product read must return matching ready identity', async () => {
  const mutations = [p => { p.state = 'unavailable'; }, p => { p.mode = 'external'; },
    p => { p.productName = 'Other'; }, p => { p.applicationName = 'Other'; }, p => { p.productVersion = '5.9.4'; }];
  for (const mutate of mutations) {
    const f = fixture(); const state = productState(); mutate(state); f.client.product.read = async () => state;
    await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
    assert.equal(f.calls.includes('session.new'), false); assert.equal(f.calls.filter(x => x === 'client.close').length, 1); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
  }
});
test('missing Product read and failed read clean up without exposing raw errors', async () => {
  for (const read of [undefined, async () => { throw new Error('PRIVATE_PRODUCT'); }]) {
    const f = fixture(); f.client.product.read = read;
    await assert.rejects(createExternalRuntime(f.options), error => error.code === 'EXTERNAL_RUNTIME_REJECTED' && !error.message.includes('PRIVATE'));
    assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
  }
});
test('Product read timeout closes client and harness exactly once', async () => {
  const f = fixture({ timeoutMs: 5, cleanupMs: 20, now: () => 0 }); let entered = 0;
  f.client.product.read = () => { entered++; return new Promise(() => {}); };
  await assert.rejects(createExternalRuntime(f.options), error => error.code === 'EXTERNAL_RUNTIME_TIMEOUT');
  assert.equal(entered, 1);
  assert.equal(f.calls.filter(x => x === 'client.close').length, 1); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
  assert.equal(f.calls.includes('session.new'), false);
});
test('elicitation during Product read is cancelled and blocks runtime exposure', async () => {
  const f = fixture(); f.client.product.read = async () => {
    assert.deepEqual(f.handlers['elicitation/create'](new Proxy({}, {get(){throw new Error('read');}})), {action:'cancel'});
    return productState();
  };
  await assert.rejects(createExternalRuntime(f.options), RuntimeSetupError);
  assert.equal(f.calls.includes('session.new'), false); assert.equal(f.calls.filter(x => x === 'harness.close').length, 1);
});
let passed = 0;
for (const [name, fn] of tests) { await fn(); passed++; console.log(`ok ${passed} - ${name}`); }
console.log(`passed ${passed} vendor-free external runtime scenarios`);
