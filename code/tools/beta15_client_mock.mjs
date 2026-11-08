/** Real public-client wire rehearsal against a self-authored peer. No native loader. */
import { performance } from 'node:perf_hooks';

const METHOD = 'workbuddy-desktop';
const snapshot = () => ({ state: 'authenticated', mode: 'managed', account: { id: 'synthetic-account', authMethodId: METHOD } });
const unauthenticated = () => ({ state: 'unauthenticated', mode: null, account: null });
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
class Deadline extends Error {}
async function bounded(operation, milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) throw new Deadline();
  const deadline = performance.now() + milliseconds;
  let timer;
  try { return await Promise.race([Promise.resolve().then(() => { if (performance.now() >= deadline) throw new Deadline(); return operation(); }), new Promise((_, reject) => { timer = setTimeout(() => reject(new Deadline()), milliseconds); })]); }
  finally { clearTimeout(timer); }
}
function check(value) { if (!value) throw new TypeError(); }
function exact(value, keys) { check(isRecord(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))); }
function validId(id) { return typeof id === 'string' ? id.length > 0 && id.length <= 128 : Number.isSafeInteger(id); }

/** Fully self-authored object-stream peer; never opens URLs or performs IO. */
function makePeer(kind, facts) {
  let producer, stopped = false, initializeSeen = false, readableCancelled = false, writableEnded = false, initialized = false, loginId, loginSeen = false, reads = 0, statusReads = 0, cancelled = false, malformedSent = false;
  const requestIds = new Set();
  let markLoginArrived;
  const loginArrived = new Promise(resolve => { markLoginArrived = resolve; });
  const send = message => { check(!stopped); producer.enqueue(message); };
  const reply = (id, result) => send({ jsonrpc: '2.0', id, result });
  const failLogin = () => send({ jsonrpc: '2.0', id: loginId, error: { code: -32800, message: 'Synthetic cancelled' } });
  const stop = () => { if (!stopped) { stopped = true; try { producer.close(); } catch {} } };
  const readable = new ReadableStream({ start(controller) { producer = controller; }, cancel() { readableCancelled = true; stopped = true; } });
  const writable = new WritableStream({
    write(message) {
      check(isRecord(message) && message.jsonrpc === '2.0');
      if (message.method !== undefined) {
        check(typeof message.method === 'string' && message.method.length <= 64);
        exact(message, message.id === undefined ? ['jsonrpc','method','params'] : ['jsonrpc','id','method','params']);
      } else exact(message, ['jsonrpc','id','result']);
      if (message.id !== undefined) {
        check(validId(message.id));
        if (message.method !== undefined) { check(!requestIds.has(message.id)); requestIds.add(message.id); check(requestIds.size <= 16); }
      }
      facts.wire_messages++;
      check(facts.wire_messages <= 80);
      if (message.method === 'initialize') {
        check(!initializeSeen && !initialized && !loginSeen && validId(message.id)); initializeSeen = true;
        const p = message.params;
        exact(p,['protocolVersion','clientInfo','capabilities']);
        exact(p.clientInfo,['name','version']);
        check(p.clientInfo.name === 'synthetic-auth-peer-test' && p.clientInfo.version === '1');
        exact(p.capabilities,['agent','config','account','product']);
        exact(p.capabilities.account,['mode']);exact(p.capabilities.product,['mode']);
        exact(p.capabilities.agent,['elicitation']);exact(p.capabilities.agent.elicitation,['form']);exact(p.capabilities.agent.elicitation.form,[]);
        exact(p.capabilities.config,['warning']);exact(p.capabilities.config.warning,[]);
        check(p?.protocolVersion === 1 && p.capabilities?.account?.mode === 'managed' && p.capabilities?.product?.mode === 'external');
        reply(message.id, { protocolVersion: 1, serverInfo: { name: 'synthetic-auth-peer', version: '1' },
          capabilities: { agent: { auth: { status: true } }, account: { mode: 'managed' }, product: { mode: 'external' } },
          authMethods: [{ type: 'agent', methodId: METHOD, name: 'Synthetic WorkBuddy method fixture' }] }); return;
      }
      if (message.method === 'initialized') { check(initializeSeen && !initialized && message.id === undefined); exact(message.params,[]); initialized = true; return; }
      check(initialized);
      if (message.method === 'auth/login') {
        exact(message.params,['methodId']); check(validId(message.id) && !loginSeen && message.params.methodId === METHOD); loginSeen = true; loginId = message.id; markLoginArrived();
        if (kind === 0) reply(loginId, {});
        else if (kind === 1) send({ jsonrpc: '2.0', id: 'synthetic-form-1', method: 'elicitation/create', params: {
          mode: 'form', message: 'Synthetic cancellation check', requestId: loginId,
          requestedSchema: { type: 'object', properties: {}, required: [] } } });
        else check(kind === 3);
        return;
      }
      if (message.id === 'synthetic-form-1' && message.method === undefined) {
        exact(message.result,['action']); check(kind === 1 && message.result.action === 'cancel'); cancelled = true; facts.cancel_wire_seen = true; failLogin(); return;
      }
      if (message.method === '$/cancel_request') { exact(message.params,['requestId']); check(kind === 3 && loginSeen && message.id === undefined && message.params.requestId === loginId); cancelled = true; facts.abort_wire_seen = true; failLogin(); return; }
      if (message.method === 'account/read') {
        exact(message.params,[]);check(validId(message.id));check(++reads <= 2);
        if (kind === 0) { check(loginSeen); reply(message.id, reads === 1 ? unauthenticated() : snapshot()); }
        else if (kind === 2) { malformedSent = true; reply(message.id, { state: 'authenticated', mode: 'managed', account: null }); }
        else { check(cancelled); reply(message.id, unauthenticated()); }
        return;
      }
      if (message.method === 'auth/status') {
        exact(message.params,[]);check(validId(message.id));check(kind === 0 && ++statusReads === 1 && reads === 2); reply(message.id, { authenticated: true }); return;
      }
      throw new TypeError(); // No session/model/provision/logout/helper/network methods.
    }, close() { writableEnded = true; stop(); }, abort() { writableEnded = true; stop(); }
  });
  return { readable, writable, stop, loginArrived, get closed() { return readableCancelled && writableEnded; }, get malformedSent() { return malformedSent; }, lateAck() { check(kind === 3 && cancelled); reply(loginId, {}); } };
}

/** Only caller-supplied pinned public API is evaluated; fixtures supply authored fakes. */
export async function runClientMock(loadApi, { verifyEntry, timeoutMs = 30000, cleanupMs = 10000 } = {}) {
  check(typeof loadApi === 'function' && typeof verifyEntry === 'function');
  check(Number.isSafeInteger(timeoutMs) && timeoutMs >= 1 && timeoutMs <= 30000);
  check(Number.isSafeInteger(cleanupMs) && cleanupMs >= 1 && cleanupMs <= 10000);
  const start = performance.now(), remaining = () => timeoutMs - (performance.now() - start);
  const result = { schema_version: 1, entry_verified: false, import_ok: false, initialization_verified: false,
    ack_not_account_proof: false, synthetic_account_read_verified: false, synthetic_status_verified: false,
    cancel_wire_seen: false, cancel_rejected: false, malformed_account_rejected: false,
    abort_wire_seen: false, timeout_rejected: false, late_ack_not_ready: false,
    cases_completed: 0, clients_closed: 0, peers_closed: 0, all_account_data_synthetic: true, wire_messages: 0, success: false,
    native_account_verified: false, real_model_ready: false, failure_stage: 0, elapsed_ms: 0 };
  let stage = 1, cleanupSpent = 0;
  try {
    await bounded(verifyEntry, remaining()); check(remaining() > 0); result.entry_verified = true;
    const api = await bounded(loadApi, remaining()); check(remaining() > 0);
    check(typeof api.createClient === 'function' && typeof api.fromAcpStream === 'function' && typeof api.closeTransport === 'function'); result.import_ok = true;
    for (let kind = 0; kind < 4; kind++) {
      stage = 2 + kind;
      if (remaining() <= 0) throw new Deadline();
      const peer = makePeer(kind, result), controller = new AbortController();
      let client, transport, completed = false;
      try {
        if (remaining() <= 0) throw new Deadline();
        client = api.createClient({ clientInfo: { name: 'synthetic-auth-peer-test', version: '1' },
          account: { mode: 'managed' }, product: { mode: 'external' }, capabilities: { elicitation: { form: {} } } });
        if (remaining() <= 0) throw new Deadline();
        client.on('elicitation/create', () => ({ action: 'cancel' }));
        if (remaining() <= 0) throw new Deadline();
        transport = api.fromAcpStream({ readable: peer.readable, writable: peer.writable });
        await bounded(() => client.connect({ transport, timeoutMs: Math.max(1, remaining()), signal: controller.signal }), remaining());
        check(client.initialization?.protocolVersion === 1 && client.initialization?.authMethods?.length === 1 && client.initialization.authMethods[0].methodId === METHOD);
        result.initialization_verified = true;
        const request = operation => bounded(operation, remaining());
        if (kind === 0) {
          await request(() => client.auth.login({ methodId: METHOD }, { cancellationSignal: controller.signal }));
          const before = await request(() => client.account.read({}, { cancellationSignal: controller.signal }));
          check(before.state === 'unauthenticated' && before.account === null && before.mode === null); result.ack_not_account_proof = true;
          const after = await request(() => client.account.read({}, { cancellationSignal: controller.signal }));
          check(after.state === 'authenticated' && after.mode === 'managed' && after.account?.id === 'synthetic-account' && after.account?.authMethodId === METHOD); result.synthetic_account_read_verified = true;
          const status = await request(() => client.auth.status({}, { cancellationSignal: controller.signal }));
          check(status.authenticated === true); result.synthetic_status_verified = true;
        } else if (kind === 1) {
          let rejected = false;
          try { await request(() => client.auth.login({ methodId: METHOD }, { cancellationSignal: controller.signal })); } catch (error) { if (error instanceof Deadline) throw error; rejected = true; }
          check(rejected && result.cancel_wire_seen); result.cancel_rejected = true;
          const account = await request(() => client.account.read({}, { cancellationSignal: controller.signal })); check(account.state === 'unauthenticated');
        } else if (kind === 2) {
          let rejected = false;
          try { await request(() => client.account.read({}, { cancellationSignal: controller.signal })); } catch (error) { if (error instanceof Deadline) throw error; rejected = true; }
          check(rejected && peer.malformedSent); result.malformed_account_rejected = true;
        } else {
          const loginAbort = new AbortController();
          let timer, rejected = false;
          try {
            if (remaining() <= 0) throw new Deadline();
            const pendingLogin = Promise.resolve(client.auth.login({ methodId: METHOD }, { cancellationSignal: loginAbort.signal }));
            pendingLogin.catch(() => {}); // Observe before waiting for peer delivery.
            await bounded(() => peer.loginArrived, remaining());
            if (remaining() <= 0) throw new Deadline();
            timer = setTimeout(() => loginAbort.abort(new Error('Synthetic deadline')), 25);
            try { await request(() => pendingLogin); }
            catch (error) { if (error instanceof Deadline) throw error; rejected = loginAbort.signal.aborted; }
          } finally { clearTimeout(timer); loginAbort.abort(); }
          if (rejected) await bounded(async () => { while (!result.abort_wire_seen) { if (remaining() <= 0) throw new Deadline(); await new Promise(resolve => setTimeout(resolve, 1)); } }, remaining());
          // SDK cancellation only sends a notification; peer's -32800 settles the request.
          check(rejected && result.abort_wire_seen); result.timeout_rejected = true;
          peer.lateAck();
          const account = await request(() => client.account.read({}, { cancellationSignal: controller.signal }));
          check(account.state === 'unauthenticated' && account.account === null && account.mode === null); result.late_ack_not_ready = true;
        }
        completed = true;
      } finally {
        controller.abort();
        let cleanupError;
        const closeStep = async operation => {
          const cleanupStart = performance.now();
          try { const pending = Promise.resolve(operation()); pending.catch(() => {});
            await bounded(() => pending, cleanupMs - cleanupSpent);
          } finally { cleanupSpent += performance.now() - cleanupStart; }
        };
        try {
          if (client) try { await closeStep(() => client.close()); result.clients_closed++; } catch (error) { cleanupError = error; }
          // Public transport close is idempotent; loader runs its Effect explicitly.
          if (transport) try { await closeStep(() => api.closeTransport(transport)); } catch (error) { cleanupError ??= error; }
          if (peer.closed) result.peers_closed++; else if (transport) cleanupError ??= new TypeError();
        } finally { peer.stop(); }
        if (cleanupError) throw cleanupError;
      }
      if (completed) result.cases_completed++;
    }
  } catch (error) { result.failure_stage = error instanceof Deadline ? 7 : stage; }
  result.elapsed_ms = Math.min(120000, Math.max(0, Math.round(performance.now() - start)));
  result.success = result.failure_stage === 0 && result.cases_completed === 4 && result.clients_closed === 4 && result.peers_closed === 4 && result.all_account_data_synthetic === true &&
    ['entry_verified','import_ok','initialization_verified','ack_not_account_proof','synthetic_account_read_verified','synthetic_status_verified',
      'cancel_wire_seen','cancel_rejected','malformed_account_rejected','abort_wire_seen','timeout_rejected','late_ack_not_ready'].every(k => result[k] === true);
  return result;
}
