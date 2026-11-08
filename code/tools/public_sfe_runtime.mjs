/** Closed-gate public SFE connection assembly. No vendor import, spawn or login. */
import { performance } from 'node:perf_hooks';
export class PublicSfeError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const reject = () => { throw new PublicSfeError('PUBLIC_SFE_REJECTED'); };
const check = value => { if (!value) reject(); };
const record = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const budget = (n, max) => check(Number.isSafeInteger(n) && n > 0 && n <= max);
class Timeout extends Error {}

/**
 * Owner supplies verified Client-only loader and an ALREADY owned MessageStream.
 * closeOwnedTransport must be idempotent with the official Client's close path.
 * Neither its settlement nor client.close proves process exit / Job emptiness.
 * This factory never exposes Client and cannot admit sessions, even if logged in.
 */
export async function createPublicSfeRuntime({ verifyEntry, loadClient, transport,
  closeOwnedTransport, timeoutMs = 90000, cleanupMs = 10000,
  now = () => performance.now() } = {}) {
  let previous = -Infinity;
  function clock() {
    const n = now(); check(Number.isFinite(n) && n >= previous); previous = n; return n;
  }
  let started;
  const abort = new AbortController();
  let client, closing, invalid = false, ready = false, closed = false;
  const left = () => { const n = timeoutMs - (clock() - started); if (n <= 0) throw new Timeout(); return n; };
  async function wait(p, ms) {
    const pending = Promise.resolve(p); pending.catch(() => {});
    if (!(ms > 0)) throw new Timeout();
    let timer;
    try { return await Promise.race([pending, new Promise((_, no) => { timer = setTimeout(() => no(new Timeout()), ms); })]); }
    finally { clearTimeout(timer); }
  }
  async function step(fn) { const ms = left(); const result = await wait(fn(), ms); left(); check(!invalid); return result; }
  function close() {
    if (closing) return closing;
    closed = true; ready = false; abort.abort();
    let resolve; closing = new Promise(r => { resolve = r; });
    (async () => {
      let ok = true, end;
      try { end = clock() + (Number.isSafeInteger(cleanupMs) && cleanupMs > 0 && cleanupMs <= 10000 ? cleanupMs : 10000); } catch { end = -Infinity; }
      async function part(fn) {
        try { const pending = Promise.resolve(fn()); pending.catch(() => {}); await wait(pending, end - clock()); if (clock() >= end) throw new Timeout(); }
        catch { ok = false; }
      }
      if (client) await part(() => client.close());
      await part(() => closeOwnedTransport());
      return ok;
    })().then(resolve, () => resolve(false));
    return closing;
  }
  function invalidate() { invalid = true; ready = false; abort.abort(); }
  try {
    check(typeof verifyEntry === 'function' && typeof loadClient === 'function' &&
      typeof closeOwnedTransport === 'function' && typeof now === 'function' && record(transport));
    budget(timeoutMs, 120000); budget(cleanupMs, 10000);
    started = clock();
    check(await step(verifyEntry) === true);
    const api = await step(loadClient);
    check(api && typeof api.createClient === 'function');
    // Official createClient is synchronous; a promise is not a supported Client.
    client = api.createClient({ account: { mode: 'managed' }, product: { mode: 'managed' },
      capabilities: { elicitation: { form: {}, url: {} } } });
    check(client && typeof client.close === 'function' && typeof client.on === 'function' && typeof client.connect === 'function');
    client.on('elicitation/create', () => { invalidate(); return { action: 'cancel' }; });
    client.on('session/request_permission', () => { invalidate(); return { outcome: { outcome: 'cancelled' } }; });
    // Changes during bootstrap invalidate snapshots too; never race old evidence
    // against a new account/product generation or unsolicited session activity.
    for (const method of ['account/updated', 'product/updated', 'session/update']) client.on(method, invalidate);
    await step(() => client.connect({ transport, signal: abort.signal, timeoutMs: left() }));
    const initialization = client.initialization;
    check(initialization?.protocolVersion === 1 && initialization.serverInfo?.name === '@genie/agent-harness');
    const methods = initialization.authMethods;
    check(Array.isArray(methods) && methods.length === 1 && methods[0]?.methodId === 'workbuddy-desktop' &&
      methods[0].name === 'TencentCloud' && methods[0].type === 'agent' && methods[0].description === null);
    check(typeof client.product?.read === 'function' && typeof client.account?.read === 'function' && typeof client.auth?.status === 'function');
    const connection = client.connection;
    check(connection && typeof connection.closed?.then === 'function' && connection.signal instanceof AbortSignal && !connection.signal.aborted);
    connection.closed.then(invalidate, invalidate);
    connection.signal.addEventListener('abort', invalidate, { once: true });
    const product = await step(() => client.product.read({}, { cancellationSignal: abort.signal }));
    check(record(product) && product.state === 'ready' && product.mode === 'managed' &&
      product.productName === 'WorkBuddy' && product.applicationName === 'WorkBuddy' && product.productVersion === '5.9.3');
    const account = await step(() => client.account.read({}, { cancellationSignal: abort.signal }));
    // Authenticated identities are deliberately not retained or returned.
    check(record(account) && account.state === 'unauthenticated' && account.mode === null && account.account === null);
    const status = await step(() => client.auth.status({}, { cancellationSignal: abort.signal }));
    check(record(status) && status.authenticated === false && !connection.signal.aborted);
    ready = true;
    return Object.freeze({
      state: () => Object.freeze({ catalogReady: ready && !invalid && !closed,
        invalidated: invalid, closed, protocolVersion: 1, native_account_verified: false,
        real_model_ready: false, sessionAdmission: 'closed' }),
      // No synthetic bool, environment switch or caller-provided admission callback.
      dispatch: () => { throw new PublicSfeError('PUBLIC_SFE_SESSION_GATE_CLOSED'); },
      close
    });
  } catch (error) {
    invalidate(); await close();
    throw new PublicSfeError(error instanceof Timeout ? 'PUBLIC_SFE_TIMEOUT' : 'PUBLIC_SFE_REJECTED');
  }
}
