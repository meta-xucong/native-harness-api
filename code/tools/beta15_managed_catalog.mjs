/** Bounded empty-account native catalog diagnostic. Imports only Node builtins. */
import { performance } from 'node:perf_hooks';
class Deadline extends Error {}
function check(value) { if (!value) throw new TypeError(); }
async function bounded(operation, ms) {
  if (!Number.isFinite(ms) || ms <= 0) throw new Deadline();
  const deadline = performance.now() + ms; let timer;
  try { return await Promise.race([Promise.resolve().then(() => { if (performance.now() >= deadline) throw new Deadline(); return operation(); }),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Deadline()), ms); })]); }
  finally { clearTimeout(timer); }
}
const productIdentity = product => product?.state === 'ready' && product.mode === 'managed' && product.productName === 'WorkBuddy' && product.applicationName === 'WorkBuddy' && product.productVersion === '5.9.3';
function checkProductInput(input) {
  check(input?.schemaVersion === 1 && input.kind === 'overlay');
  const p = input.configuration;
  check(p?.productName === 'WorkBuddy' && p.applicationName === 'WorkBuddy' && p.productVersion === '5.9.3');
  check(p.authentication?.id === 'workbuddy-desktop' && p.authentication.type === 'account-scoped-external-link' && p.authentication.attributes?.platform === 'workbuddy');
  check(!Object.hasOwn(p.authentication.attributes, 'token') && !Object.hasOwn(p, 'apiKeyHelper'));
  for (const key of ['include','productConfigEnv','productConfigPathEnv','replacements','mergeStrategy']) check(!Object.hasOwn(p,key));
}

export async function runManagedCatalog(loadApi, { verifyEntry, loadProduct, verifyEmptyRoots, configDir, timeoutMs = 30000, cleanupMs = 10000 } = {}) {
  check(typeof loadApi === 'function' && typeof verifyEntry === 'function' && typeof loadProduct === 'function' && typeof verifyEmptyRoots === 'function');
  check(Number.isSafeInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 30000 && Number.isSafeInteger(cleanupMs) && cleanupMs > 0 && cleanupMs <= 10000);
  const dto = { schema_version: 1, entry_verified: false, empty_roots_verified: false, product_config_verified: false, import_ok: false,
    harness_created: false, client_created: false, connect_returned: false, connected: false, product_read_returned: false, product_identity_verified: false,
    managed_catalog_verified: false, account_read_returned: false, account_unauthenticated: false, auth_status_returned: false, auth_status_unauthenticated: false,
    client_closed: false, harness_closed: false, harness_settled: false, elicitation_seen: 0, failure_stage: 0, elapsed_ms: 0, success: false,
    native_account_verified: false, real_model_ready: false };
  const start = performance.now(), controller = new AbortController();
  const remaining = () => { const left = timeoutMs - (performance.now() - start); if (left <= 0) throw new Deadline(); return left; };
  const clean = () => { remaining(); check(dto.elicitation_seen === 0); };
  let harness, client, stage = 1;
  try {
    await bounded(verifyEntry,remaining()); remaining(); dto.entry_verified = true;
    check(await bounded(verifyEmptyRoots,remaining()) === true); remaining(); dto.empty_roots_verified = true;
    const productConfig = await bounded(loadProduct,remaining()); remaining(); checkProductInput(productConfig); dto.product_config_verified = true;
    const api = await bounded(loadApi,remaining()); clean(); check(typeof api.createHarness === 'function' && typeof api.createClient === 'function'); dto.import_ok = true;
    stage = 2;
    harness = await bounded(() => {
      clean();
      const creation = Promise.resolve().then(() => { clean(); return api.createHarness({configDir,productConfig,strictMcpConfig:true,signal:controller.signal}); });
      creation.then(value => { if (controller.signal.aborted && !harness) Promise.resolve().then(() => { value.closed?.catch?.(() => {}); return value.close(); }).catch(() => {}); }, () => {});
      return creation;
    },remaining());
    check(harness && typeof harness.close === 'function' && typeof harness.closed?.then === 'function'); harness.closed.catch(()=>{}); dto.harness_created = true;
    stage = 3; clean();
    client = api.createClient({account:{mode:'managed'},product:{mode:'managed'},capabilities:{elicitation:{form:{},url:{}}}});
    check(client && typeof client.on === 'function' && typeof client.close === 'function' && typeof client.connect === 'function'); dto.client_created = true;
    client.on('elicitation/create', () => { dto.elicitation_seen = 1; controller.abort(); return {action:'cancel'}; });
    stage = 4; clean();
    await bounded(() => client.connect(harness,{timeoutMs:remaining()}),remaining()); dto.connect_returned = true; clean();
    check(client.initialization?.protocolVersion === 1); dto.connected = true;
    stage = 5; clean();
    const product = await bounded(() => client.product.read({}, {cancellationSignal:controller.signal}),remaining()); dto.product_read_returned = true; clean();
    check(productIdentity(product)); dto.product_identity_verified = true;
    stage = 6; clean();
    const methods = client.initialization.authMethods;
    check(Array.isArray(methods) && methods.length === 1);
    const method = methods[0];
    check(method?.type === 'agent' && method.methodId === 'workbuddy-desktop' && method.providerType === 'account-scoped-external-link'); dto.managed_catalog_verified = true;
    stage = 7; clean();
    const account = await bounded(() => client.account.read({}, {cancellationSignal:controller.signal}),remaining()); dto.account_read_returned = true; clean();
    check(account?.state === 'unauthenticated' && account.mode === null && account.account === null); dto.account_unauthenticated = true;
    stage = 8; clean();
    const status = await bounded(() => client.auth.status({}, {cancellationSignal:controller.signal}),remaining()); dto.auth_status_returned = true; clean();
    check(status?.authenticated === false); dto.auth_status_unauthenticated = true;
  } catch (error) { dto.failure_stage = error instanceof Deadline ? 10 : stage; }
  finally {
    if (dto.failure_stage !== 0) controller.abort();
    const end = performance.now() + cleanupMs;
    const closeStep = async (operation,flag) => {
      try { const pending = Promise.resolve(operation()); pending.catch(()=>{}); await bounded(()=>pending,end-performance.now());
        if (performance.now() >= end) throw new Deadline(); dto[flag] = true;
      } catch (error) { controller.abort(); if(dto.failure_stage === 0) dto.failure_stage = error instanceof Deadline ? 10 : 9; }
    };
    if(client) await closeStep(()=>client.close(),'client_closed');
    if(harness) { await closeStep(()=>harness.close(),'harness_closed'); await closeStep(()=>harness.closed,'harness_settled'); }
    if(dto.elicitation_seen !== 0 && dto.failure_stage === 0) dto.failure_stage = 4;
    dto.elapsed_ms = Math.min(120000,Math.max(0,Math.round(performance.now()-start)));
  }
  dto.success = dto.failure_stage === 0 && dto.elicitation_seen === 0 && [
    'entry_verified','empty_roots_verified','product_config_verified','import_ok','harness_created','client_created','connect_returned','connected',
    'product_read_returned','product_identity_verified','managed_catalog_verified','account_read_returned','account_unauthenticated','auth_status_returned',
    'auth_status_unauthenticated','client_closed','harness_closed','harness_settled'].every(key=>dto[key] === true);
  return dto;
}
