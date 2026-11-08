/** Injectable external-only runtime assembly. No vendor import or command-line entry. */
import { performance } from 'node:perf_hooks';
import { createCommandShim } from './beta15_command_shim.mjs';

export class RuntimeSetupError extends Error {
  constructor(code) { super(code); this.code = code; }
}
class Deadline extends Error {}
const check = value => { if (!value) throw new RuntimeSetupError('EXTERNAL_RUNTIME_REJECTED'); };
function budget(value, max) { check(Number.isSafeInteger(value) && value >= 1 && value <= max); }

/**
 * All paths, original bytes and native execution authority remain the owner's
 * responsibility. verifyEntry must affirm true before loadApi may be invoked.
 * loadApi is injected: importing this module never loads the vendor bundle.
 */
export async function createExternalRuntime({ verifyEntry, loadApi, configDir, expectedCwd, emit,
  timeoutMs = 30000, cleanupMs = 10000, now = () => performance.now() } = {}) {
  check(typeof verifyEntry === 'function' && typeof loadApi === 'function' && typeof emit === 'function' && typeof now === 'function');
  check(typeof configDir === 'string' && configDir.length > 0 && configDir.length <= 4096);
  check(typeof expectedCwd === 'string' && expectedCwd.length > 0 && expectedCwd.length <= 4096);
  budget(timeoutMs, 30000); budget(cleanupMs, 10000);
  let lastNow = -Infinity;
  function sample() {
    let value;
    try { value = now(); } catch { throw new RuntimeSetupError('EXTERNAL_RUNTIME_REJECTED'); }
    check(typeof value === 'number' && Number.isFinite(value) && value >= lastNow);
    lastNow = value; return value;
  }
  const start = sample(), controller = new AbortController();
  const remaining = () => { const left = timeoutMs - (sample() - start); if (left <= 0) throw new Deadline(); return left; };
  async function wait(promise, ms) {
    const pending = Promise.resolve(promise);
    pending.catch(() => {});
    if (!(ms > 0)) throw new Deadline();
    let timer;
    try {
      return await Promise.race([pending, new Promise((_, reject) => { timer = setTimeout(() => reject(new Deadline()), ms); })]);
    } finally { clearTimeout(timer); }
  }
  async function startStep(operation) {
    const left = remaining();
    const pending = operation(); // No delayed side effect can start after timeout.
    const result = await wait(pending, left);
    remaining(); return result;
  }
  let client, harness, createdHarness, shim, failed = false, elicitationSeen = false, closing;
  const closedObjects = new WeakMap();
  function closeObject(value) {
    if (closedObjects.has(value)) return closedObjects.get(value);
    // Cache before invocation so synchronous reentrancy cannot issue close twice.
    let resolve, reject;
    const pending = new Promise((a, b) => { resolve = a; reject = b; });
    closedObjects.set(value, pending);
    pending.catch(() => {});
    try { Promise.resolve(value.close()).then(resolve, reject); } catch (error) { reject(error); }
    return pending;
  }
  function closeRuntime() {
    if (closing) return closing;
    let resolve;
    closing = new Promise(r => { resolve = r; });
    (async () => {
      controller.abort();
      let end;
      try { end = sample() + cleanupMs; } catch { end = -Infinity; }
      let ok = true;
      async function closeStep(operation) {
        try {
          const pending = operation(); // Always request required close exactly once.
          await wait(pending, end - sample());
          if (sample() >= end) throw new Deadline();
        } catch { ok = false; }
      }
      if (client) await closeStep(() => closeObject(client));
      if (harness) {
        await closeStep(() => closeObject(harness));
        await closeStep(() => harness.closed);
      }
      return ok && Boolean(client) && Boolean(harness);
    })().then(resolve, () => resolve(false));
    return closing;
  }
  try {
    check(await startStep(verifyEntry) === true);
    const api = await startStep(loadApi);
    check(api && typeof api.createHarness === 'function' && typeof api.createClient === 'function');
    // Keep the creation promise reachable for timeout races. A late resource is
    // never published; abort plus one close request is attempted immediately.
    remaining();
    const creation = Promise.resolve(api.createHarness({ configDir, strictMcpConfig: true, signal: controller.signal }));
    creation.then(value => {
      createdHarness = value;
      if (failed && !harness && value && typeof value.close === 'function') {
        closeObject(value).catch(() => {});
        try { Promise.resolve(value.closed).catch(() => {}); } catch {}
      }
    }, () => {});
    harness = await wait(creation, remaining());
    check(harness && typeof harness.close === 'function' && typeof harness.closed?.then === 'function');
    harness.closed.catch(() => {});
    remaining();
    client = api.createClient({ account: { mode: 'external' }, product: { mode: 'external' },
      capabilities: { elicitation: { form: {}, url: {} } } });
    check(client && typeof client.on === 'function' && typeof client.connect === 'function' && typeof client.close === 'function');
    const removePermission = client.on('session/request_permission', () => ({ outcome: { outcome: 'cancelled' } }));
    const removeElicitation = client.on('elicitation/create', () => { elicitationSeen = true; controller.abort(); return { action: 'cancel' }; });
    check(typeof removePermission === 'function' && typeof removeElicitation === 'function');
    check(!elicitationSeen);
    await startStep(() => client.connect(harness, { timeoutMs: remaining() }));
    check(!elicitationSeen && client.initialization?.protocolVersion === 1);
    // Pinned client.on rejects duplicate request handlers and returns removers.
    // Transfer handlers synchronously, with no await or unprotected event turn.
    removePermission(); removeElicitation();
    shim = createCommandShim({ client, expectedCwd, emit, closeRuntime });
    remaining();
    return Object.freeze({
      dispatch: command => shim.dispatch(command),
      state: () => ({ externalOnly: true, protocolVersion: 1, ...shim.state() }),
      close: () => { shim.stop(); return closeRuntime(); }
    });
  } catch (error) {
    failed = true; controller.abort();
    // A creation can settle after the race timed out but before this catch runs.
    // Retain that resource rather than relying only on the late callback flag.
    if (!harness && createdHarness) harness = createdHarness;
    if (shim) shim.stop();
    await closeRuntime();
    throw new RuntimeSetupError(error instanceof Deadline ? 'EXTERNAL_RUNTIME_TIMEOUT' : 'EXTERNAL_RUNTIME_REJECTED');
  }
}
