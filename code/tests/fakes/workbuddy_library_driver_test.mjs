/** All APIs here are self-authored fakes. No vendor package is imported. */
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { runHandshake as actualHandshake, complete, classifyError, verifyPhysicalFile, verifyVirtualFile } from '../../tools/workbuddy_library_driver.mjs';

const runHandshake = (load, options = {}, clock) => actualHandshake(load, { verifyEntry: async () => {}, ...options }, clock);

function fixture(fail = '') {
  const calls = [];
  const closed = fail === 'settle' ? Promise.reject(new Error('PRIVATE_SECRET')) : Promise.resolve();
  closed.catch(() => {});
  const harness = { closed, async close() { calls.push('harness.close'); if (fail === 'harness.close') throw Error('PRIVATE_SECRET'); } };
  const client = { initialization: { protocolVersion: fail === 'protocol' ? 2 : 1 },
    async connect(h, options) { calls.push('connect'); assert.equal(h, harness); assert.ok(options.timeoutMs > 0);
      if (fail === 'connect') throw Error('PRIVATE_SECRET'); if (fail === 'deadline') return new Promise(() => {}); },
    async close() { calls.push('client.close'); if (fail === 'client.close') throw Error('PRIVATE_SECRET'); if (fail === 'close.deadline') return new Promise(() => {}); } };
  const api = {
    async createHarness(options) { calls.push('createHarness'); assert.equal(options.configDir, '/synthetic');
      assert.equal(options.strictMcpConfig, true); assert.ok(options.signal instanceof AbortSignal);
      if (fail === 'harness') throw Error('PRIVATE_SECRET'); return harness; },
    createClient(options) { calls.push('createClient'); assert.deepEqual(options, { account: { mode: 'external' }, product: { mode: 'external' } });
      if (fail === 'client') throw Error('PRIVATE_SECRET'); return client; }
  };
  // Only these permitted APIs exist. Any accidental prohibited verb fails the fixture.
  return { calls, load: async () => { calls.push('import'); if (fail === 'import') throw Error('PRIVATE_SECRET'); return api; } };
}
let count = 0;
async function test(fail, expectedStage, expectedCalls) {
  const f = fixture(fail);
  const dto = await runHandshake(f.load, { configDir: '/synthetic', timeoutMs: fail === 'deadline' ? 30 : 1000, cleanupMs: fail === 'close.deadline' ? 30 : 1000 });
  assert.equal(dto.failure_stage, expectedStage);
  assert.equal(complete(dto), expectedStage === 0);
  assert.deepEqual(f.calls, expectedCalls);
  assert.ok(Object.values(dto).every(v => typeof v === 'boolean' || (typeof v === 'number' && Number.isFinite(v))));
  assert.ok(!JSON.stringify(dto).includes('PRIVATE_SECRET'));
  count++;
}
const full = ['import','createHarness','createClient','connect','client.close','harness.close'];
await test('',0,full);
await test('import',1,['import']);
await test('harness',2,['import','createHarness']);
await test('client',3,['import','createHarness','createClient','harness.close']);
await test('connect',4,full);
await test('protocol',4,full);
await test('client.close',5,full);
await test('harness.close',6,full);
await test('settle',6,full);
await test('deadline',7,full);
await test('close.deadline',7,full);
await assert.rejects(runHandshake(async()=>({}), { timeoutMs:0 })); count++;
let lateClosed = 0, releaseLate;
const lateCreation = new Promise(resolve => { releaseLate = resolve; });
const late = await runHandshake(async () => ({
  createHarness: async ({ signal }) => {
    await lateCreation;
    assert.equal(signal.aborted, true);
    return { closed: Promise.resolve(), close: async () => { lateClosed++; } };
  },
  createClient: () => { throw Error('prohibited after deadline'); }
}), { configDir: '/synthetic', timeoutMs: 30, cleanupMs: 1000 });
assert.equal(late.failure_stage, 7);
assert.equal(late.success, false);
releaseLate();
await new Promise(resolve => setImmediate(resolve));
assert.equal(lateClosed, 1); count++;
let tick = 0;
const clock = () => tick;
let forbidden = 0;
const exhaustedImport = await runHandshake(() => {
  tick += 15;
  return { createHarness() { forbidden++; } };
}, { timeoutMs: 5, cleanupMs: 1000 }, clock);
assert.equal(exhaustedImport.failure_stage, 7);
assert.equal(forbidden, 0); count++;
let syncClosed = 0;
const exhaustedCreate = await runHandshake(async () => ({
  createHarness() { tick += 15; return { closed: Promise.resolve(), close: async () => { syncClosed++; } }; },
  createClient() { forbidden++; }
}), { timeoutMs: 5, cleanupMs: 1000 }, clock);
assert.equal(exhaustedCreate.failure_stage, 7);
assert.equal(forbidden, 0);
assert.equal(syncClosed, 1); count++;
let cleanupHarness = 0;
const exhaustedClose = await runHandshake(async () => ({
  createHarness: async () => ({ closed: Promise.resolve(), close: async () => { cleanupHarness++; } }),
  createClient: () => ({ initialization: { protocolVersion: 1 }, connect: async () => {},
    close: async () => { tick += 15; } })
}), { timeoutMs: 1000, cleanupMs: 5 }, clock);
assert.equal(exhaustedClose.failure_stage, 7);
assert.equal(exhaustedClose.client_closed, false);
assert.equal(exhaustedClose.harness_closed, false);
assert.equal(exhaustedClose.harness_settled, false);
assert.equal(cleanupHarness, 1); count++;
// Clock crosses the deadline between the positive left check and bounded's
// microtask gate. Cleanup invocation must already have happened exactly once.
let closing = false, closeClockReads = 0, raceClientCloses = 0, raceHarnessCloses = 0;
const raceClock = () => !closing ? 0 : (++closeClockReads <= 2 ? 0 : 20);
const microtaskRace = await runHandshake(async () => ({
  createHarness: async () => ({ closed: Promise.resolve(), close: () => { raceHarnessCloses++; } }),
  createClient: () => ({ initialization: { protocolVersion: 1 }, connect: async () => {},
    close: () => { raceClientCloses++; closing = true; } })
}), { timeoutMs: 1000, cleanupMs: 5 }, raceClock);
assert.equal(microtaskRace.failure_stage, 7);
assert.equal(microtaskRace.client_closed, false);
assert.equal(microtaskRace.harness_closed, false);
assert.equal(microtaskRace.harness_settled, false);
assert.equal(raceClientCloses, 1);
assert.equal(raceHarnessCloses, 1); count++;
const verifyCalls = [];
const verified = fixture();
const ordered = await runHandshake(() => { verifyCalls.push('import'); return verified.load(); }, {
  configDir: '/synthetic', timeoutMs: 1000, cleanupMs: 1000,
  verifyEntry: async () => { verifyCalls.push('verify'); }
});
assert.deepEqual(verifyCalls, ['verify', 'import']);
assert.equal(ordered.schema_version, 2);
assert.equal(ordered.entry_verified, true);
assert.equal(ordered.error_class, 0); count++;
let blockedImport = 0;
const virtualFail = await runHandshake(() => { blockedImport++; }, { verifyEntry: async () => {
  throw Object.defineProperty(new Error('PRIVATE_SECRET'), 'code', { value: 'ENOENT' });
} });
assert.equal(blockedImport, 0);
assert.equal(virtualFail.entry_verified, false);
assert.equal(virtualFail.import_ok, false);
assert.equal(virtualFail.failure_stage, 1);
assert.equal(virtualFail.error_class, 3); count++;
const importFail = await runHandshake(async () => { throw { code: 'ERR_MODULE_NOT_FOUND', message: 'PRIVATE_SECRET' }; });
assert.equal(importFail.entry_verified, true);
assert.equal(importFail.import_ok, false);
assert.equal(importFail.error_class, 1);
assert.ok(!JSON.stringify(importFail).includes('PRIVATE_SECRET')); count++;
let getterCalls = 0;
assert.equal(classifyError({ get code() { getterCalls++; throw Error('PRIVATE_SECRET'); } }), 8);
assert.equal(getterCalls, 0);
assert.equal(classifyError(Object.create({ code: 'ENOENT' })), 8);
assert.equal(classifyError({ code: 'PRIVATE_SECRET' }), 8);
assert.equal(classifyError({ code: 3 }), 8);
assert.equal(classifyError(new Proxy({}, { getOwnPropertyDescriptor() { throw Error('PRIVATE_SECRET'); } })), 8);
for (const [code, value] of Object.entries({ ERR_MODULE_NOT_FOUND:1, MODULE_NOT_FOUND:2, ENOENT:3, EACCES:4, EPERM:5, ERR_UNSUPPORTED_DIR_IMPORT:6, ERR_UNKNOWN_FILE_EXTENSION:7 })) {
  assert.equal(classifyError({ code }), value);
}
count++;
let verifyClock = 0;
const verifyExpired = await runHandshake(() => { blockedImport++; }, {
  timeoutMs: 5, cleanupMs: 1000, verifyEntry: async () => { verifyClock = 10; }
}, () => verifyClock);
assert.equal(verifyExpired.failure_stage, 7);
assert.equal(verifyExpired.entry_verified, false);
assert.equal(blockedImport, 0); count++;

// Self-authored bytes only. Electron's documented virtual stat semantics are
// represented explicitly: a .asar root is a directory, not its physical file.
const folder = await fs.realpath(await fs.mkdtemp(path.join(tmpdir(), 'synthetic-asar-fs-')));
try {
  const archive = path.join(folder, 'synthetic.asar');
  const backing = path.join(folder, 'synthetic-entry.js');
  const bytes = Buffer.from('synthetic archive bytes, not vendor code');
  const digest = createHash('sha256').update(bytes).digest('hex');
  await fs.writeFile(archive, bytes, { flag: 'wx' });
  await fs.writeFile(backing, bytes, { flag: 'wx' });
  let virtualOpens = 0;
  const virtual = {
    lstat: async value => value === archive
      ? { isSymbolicLink: () => false, isFile: () => false, isDirectory: () => true }
      : fs.lstat(value),
    realpath: fs.realpath,
    open: async value => {
      virtualOpens++;
      assert.equal(value, path.join(archive, 'lib', 'public.js'));
      return fs.open(backing, 'r');
    }
  };
  assert.equal(await verifyPhysicalFile(archive, digest, 1024, fs), bytes.length);
  assert.equal(virtualOpens, 0); count++;
  await assert.rejects(verifyPhysicalFile(archive, digest, 1024, virtual), TypeError);
  assert.equal(virtualOpens, 0); count++;
  await verifyVirtualFile(path.join(archive, 'lib', 'public.js'), digest, bytes.length, virtual);
  assert.equal(virtualOpens, 1); count++;
  await assert.rejects(verifyPhysicalFile(archive, '0'.repeat(64), 1024, fs), TypeError);
  await assert.rejects(verifyVirtualFile(path.join(archive, 'lib', 'public.js'), digest, bytes.length + 1, virtual), TypeError);
  await assert.rejects(verifyPhysicalFile(archive, digest, 1024, {}), TypeError); count++;
  const f = fixture();
  const rejectedPhysical = await runHandshake(f.load, {
    verifyEntry: () => verifyPhysicalFile(archive, digest, 1024, virtual)
  });
  assert.equal(rejectedPhysical.failure_stage, 1);
  assert.equal(rejectedPhysical.error_class, 8);
  assert.equal(rejectedPhysical.entry_verified, false);
  assert.equal(rejectedPhysical.import_ok, false);
  assert.deepEqual(f.calls, []); count++;
} finally { await fs.rm(folder, { recursive: true, force: true }); }
console.log(`synthetic_node_driver_tests=${count}`);
