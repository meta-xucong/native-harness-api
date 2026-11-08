import assert from 'node:assert/strict';
import { createPublicSfeRuntime } from '../../tools/public_sfe_runtime.mjs';
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function requestOptions(params, options) { assert.deepEqual(params, {}); assert.deepEqual(Object.keys(options), ['cancellationSignal']); assert.ok(options.cancellationSignal instanceof AbortSignal); }
function fixture() {
  const calls = [], handlers = new Map(), end = deferred(), signal = new AbortController(), transport = {};
  const client = {
    initialization: { protocolVersion: 1, serverInfo: { name: '@genie/agent-harness' }, authMethods: [
      { methodId: 'workbuddy-desktop', name: 'TencentCloud', type: 'agent', description: null }] },
    connection: { signal: signal.signal, closed: end.promise },
    on(name, handler) { assert.ok(!handlers.has(name)); handlers.set(name, handler); return () => handlers.delete(name); },
    async connect(options) { calls.push('connect'); assert.equal(options.transport, transport); assert.equal(Object.keys(options).sort().join(','), 'signal,timeoutMs,transport'); assert.ok(handlers.has('elicitation/create')); },
    async close() { calls.push('client.close'); end.resolve(); },
    product: { async read(params, options) { requestOptions(params, options); calls.push('product.read'); return { state: 'ready', mode: 'managed', productName: 'WorkBuddy', applicationName: 'WorkBuddy', productVersion: '5.9.3' }; } },
    account: { async read(params, options) { requestOptions(params, options); calls.push('account.read'); return { state: 'unauthenticated', mode: null, account: null }; } },
    auth: { async status(params, options) { requestOptions(params, options); calls.push('auth.status'); return { authenticated: false }; } },
    session: new Proxy({}, { get() { throw new Error('session must never be accessed'); } })
  };
  const options = { transport, now: () => 0, timeoutMs: 100, cleanupMs: 100,
    verifyEntry: async () => { calls.push('verify'); return true; },
    loadClient: async () => { calls.push('load'); return { createClient(config) { calls.push('createClient'); assert.deepEqual(config, { account: { mode: 'managed' }, product: { mode: 'managed' }, capabilities: { elicitation: { form: {}, url: {} } } }); return client; } }; },
    closeOwnedTransport: async () => { calls.push('owner.close'); }
  };
  return { options, calls, handlers, client, end, signal };
}
const tests = [];
const test = (name, run) => tests.push([name, run]);
async function rejected(f, code = 'PUBLIC_SFE_REJECTED') {
  await assert.rejects(createPublicSfeRuntime(f.options), e => e.code === code && e.message === code);
  assert.equal(f.calls.filter(x => x === 'owner.close').length, 1);
}
test('anonymous catalog cannot admit any dispatch; exact sequential reads and once cleanup', async () => {
  const f = fixture(), runtime = await createPublicSfeRuntime(f.options);
  assert.deepEqual(f.calls, ['verify', 'load', 'createClient', 'connect', 'product.read', 'account.read', 'auth.status']);
  assert.equal(runtime.state().catalogReady, true);
  assert.deepEqual(Object.keys(runtime).sort(), ['close', 'dispatch', 'state']);
  for (const request of [null, { method: 'session/new' }, { method: 'auth/login' }, { admitted: true }])
    assert.throws(() => runtime.dispatch(request), e => e.code === 'PUBLIC_SFE_SESSION_GATE_CLOSED');
  assert.equal(runtime.state().native_account_verified, false); assert.equal(runtime.state().real_model_ready, false);
  assert.equal(await runtime.close(), true); assert.equal(await runtime.close(), true);
  assert.deepEqual(f.calls.slice(-2), ['client.close', 'owner.close']);
});
test('verify rejection never imports', async () => { const f = fixture(); f.options.verifyEntry = () => false; await rejected(f); assert.ok(!f.calls.includes('load')); });
test('loader throw sanitized, owned transport still closed', async () => { const f = fixture(); f.options.loadClient = () => { throw new Error('private'); }; await rejected(f); });
for (const field of ['protocolVersion', 'authMethods']) test(`bad ${field}`, async () => { const f = fixture(); f.client.initialization[field] = null; await rejected(f); });
test('bad Product identity stops before account', async () => { const f = fixture(); f.client.product.read = () => ({ state: 'ready', mode: 'managed', productName: 'other' }); await rejected(f); assert.ok(!f.calls.includes('account.read')); });
test('authenticated account explicitly rejected, no identity returned', async () => { const f = fixture(); f.client.account.read = () => ({ state: 'authenticated', mode: 'managed', account: { id: 'PRIVATE' } }); await rejected(f); assert.ok(!f.calls.includes('auth.status')); });
test('status disagreement rejected', async () => { const f = fixture(); f.client.auth.status = () => ({ authenticated: true }); await rejected(f); });
test('elicitation before connect cancels and invalidates', async () => { const f = fixture(); f.client.connect = () => { assert.deepEqual(f.handlers.get('elicitation/create')({ secret: 'ignored' }), { action: 'cancel' }); }; await rejected(f); });
for (const event of ['account/updated', 'product/updated', 'session/update', 'elicitation/create', 'session/request_permission']) test(`late ${event} invalidates evidence`, async () => {
  const f = fixture(), runtime = await createPublicSfeRuntime(f.options);
  const result = f.handlers.get(event)({ private: 'ignored' });
  if (event === 'session/request_permission') assert.deepEqual(result, { outcome: { outcome: 'cancelled' } });
  if (event === 'elicitation/create') assert.deepEqual(result, { action: 'cancel' });
  assert.equal(runtime.state().catalogReady, false); assert.equal(runtime.state().invalidated, true); await runtime.close();
});
test('connection completion invalidates without waiting for close', async () => { const f = fixture(), runtime = await createPublicSfeRuntime(f.options); f.end.resolve(); await Promise.resolve(); assert.equal(runtime.state().catalogReady, false); await runtime.close(); });
test('connection abort invalidates', async () => { const f = fixture(), runtime = await createPublicSfeRuntime(f.options); f.signal.abort(); assert.equal(runtime.state().catalogReady, false); await runtime.close(); });
for (const stage of ['connect', 'product', 'account', 'status']) test(`bounded ${stage} timeout cleans client and transport`, async () => {
  const f = fixture(); f.options.timeoutMs = 5; let entered = 0;
  const hang = () => { entered++; return new Promise(() => {}); };
  if (stage === 'connect') f.client.connect = hang;
  if (stage === 'product') f.client.product.read = hang;
  if (stage === 'account') f.client.account.read = hang;
  if (stage === 'status') f.client.auth.status = hang;
  await rejected(f, 'PUBLIC_SFE_TIMEOUT'); assert.equal(entered, 1); assert.equal(f.calls.filter(x => x === 'client.close').length, 1);
});
test('hung client cleanup bounded and still requests owner cleanup', async () => { const f = fixture(); f.options.cleanupMs = 5; f.client.close = () => { f.calls.push('client.close'); return new Promise(() => {}); }; const runtime = await createPublicSfeRuntime(f.options); assert.equal(await runtime.close(), false); assert.equal(f.calls.filter(x => x === 'owner.close').length, 1); });
test('failed owner cleanup not misreported', async () => { const f = fixture(); f.options.closeOwnedTransport = () => { throw new Error('secret'); }; const runtime = await createPublicSfeRuntime(f.options); assert.equal(await runtime.close(), false); });
test('cancellation notification does not settle pending request or bypass timeout', async () => {
  const f = fixture(); f.options.timeoutMs = 10; let cancelled = 0;
  f.client.product.read = (params, options) => {
    requestOptions(params, options);
    options.cancellationSignal.addEventListener('abort', () => { cancelled++; });
    queueMicrotask(() => f.handlers.get('account/updated')({}));
    return new Promise(() => {});
  };
  await rejected(f, 'PUBLIC_SFE_TIMEOUT'); assert.equal(cancelled, 1);
});
test('throwing clock sanitized and owner cleanup attempted once', async () => { const f = fixture(); f.options.now = () => { throw new Error('PRIVATE'); }; await rejected(f); });
test('invalid budget rejected with owned cleanup', async () => { const f = fixture(); f.options.timeoutMs = -1; await rejected(f); });
test('cleanup clock failure consumes rejected client and owner promises', async () => {
  const f = fixture(); let failClock = false;
  f.options.now = () => { if (failClock) throw new Error('PRIVATE clock'); return 0; };
  f.client.close = () => { f.calls.push('client.close'); return Promise.reject(new Error('PRIVATE client')); };
  f.options.closeOwnedTransport = () => { f.calls.push('owner.close'); return Promise.reject(new Error('PRIVATE owner')); };
  const runtime = await createPublicSfeRuntime(f.options);
  failClock = true;
  assert.equal(await runtime.close(), false);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(f.calls.slice(-2), ['client.close', 'owner.close']);
});
for (const [name, run] of tests) { await run(); console.log(`ok ${name}`); }
console.log(`${tests.length} vendor-free public SFE closed-gate scenarios passed`);
