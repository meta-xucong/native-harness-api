#!/usr/bin/env node
/**
 * Documentation-only audit. No network, vendor SDK, real processes or production DB.
 * The model below checks explicit design assumptions, NOT the actual product.
 * Run from any directory: node audit/check-blueprint.mjs
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const docs = [
  'README.md', 'AGENTS.md', 'docs/BLUEPRINT.md', 'docs/CONTRACTS.md',
  'docs/REUSE_AND_INTEGRATION.md', 'docs/DEVELOPMENT_AND_ACCEPTANCE.md', 'docs/AUDIT.md'
];
const auditedFiles = [...docs, '.gitignore', 'audit/check-blueprint.mjs'];
const results = [];
function check(name, fn) {
  try { fn(); results.push({ name, status: 'PASS' }); }
  catch (e) { results.push({ name, status: 'FAIL', message: String(e.message) }); }
}
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
const text = rel => read(rel);
const need = (ok, code) => { if (!ok) throw new Error(code); };
const states = ['ACCEPTED', 'CLAIMED', 'START_GRANTED', 'RUNNING', 'WAITING_APPROVAL',
  'CANCEL_REQUESTED', 'RECONCILING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'EXPIRED'];

check('D01_required_files', () => auditedFiles.forEach(p => assert.ok(fs.existsSync(path.join(root, p)), p)));
check('D02_utf8_roundtrip', () => auditedFiles.forEach(p => {
  const bytes = fs.readFileSync(path.join(root, p));
  assert.deepEqual(Buffer.from(bytes.toString('utf8'), 'utf8'), bytes, p);
  assert.ok(!bytes.toString('utf8').includes('\uFFFD'), `${p}: replacement character`);
}));
check('D03_relative_markdown_links', () => {
  const generated = new Set(['audit/results.json', 'audit/manifest.sha256'].map(p => path.join(root, p)));
  for (const p of docs) {
    for (const m of read(p).matchAll(/\[[^\]]*\]\(([^\s)]+)\)/g)) {
      const href = m[1];
      if (/^(https?:|mailto:|#)/.test(href)) continue;
      const target = path.resolve(root, path.dirname(p), decodeURIComponent(href.split('#')[0]));
      assert.ok(target.startsWith(root + path.sep), `${p}: escapes repository`);
      assert.ok(generated.has(target) || fs.existsSync(target), `${p} -> ${href}`);
    }
  }
});
check('D04_twelve_invariants', () => {
  for (let i = 1; i <= 12; i++) assert.ok(text('docs/BLUEPRINT.md').includes(`INV-${String(i).padStart(2, '0')}`));
});
check('D05_eleven_states', () => {
  for (const s of states) assert.ok(text('docs/BLUEPRINT.md').includes(`| ${s} |`), s);
});
check('D06_thirty_six_unrun_product_scenarios', () => {
  const content = text('docs/DEVELOPMENT_AND_ACCEPTANCE.md');
  for (let i = 1; i <= 36; i++) assert.ok(content.includes(`| T${String(i).padStart(2, '0')} |`));
  assert.ok(content.includes('NOT_RUN'));
});
check('D07_ten_open_release_gates', () => {
  const content = text('docs/DEVELOPMENT_AND_ACCEPTANCE.md');
  for (let i = 1; i <= 10; i++) {
    const gate = `G${String(i).padStart(2, '0')}`;
    const row = content.split('\n').find(l => l.startsWith(`| ${gate} |`));
    assert.ok(row?.includes('OPEN'), gate);
  }
});
const snapshots = {
  bridge: 'cf1e72ec3830854fa0983eb9acb92fad62a604c8',
  sub2api: '2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14',
  wrapper: 'a6c820d9a843f73c2e09aad3b4dead1544c25006'
};
check('D08_pinned_source_snapshots', () => {
  for (const sha of Object.values(snapshots)) assert.ok(text('docs/REUSE_AND_INTEGRATION.md').includes(sha));
});
check('D09_obvious_secret_patterns_only', () => {
  // Heuristic only; not an exhaustive security certification.
  const pattern = /gh[pousr]_[A-Za-z0-9]{25,}|sk-[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/;
  for (const p of auditedFiles) assert.ok(!pattern.test(read(p)), `${p}: apparent secret`);
});
check('D10_scope_and_honest_evidence_labels', () => {
  assert.ok(text('README.md').includes('仅为开发蓝图'));
  assert.ok(text('docs/AUDIT.md').includes('同一编写者'));
  assert.ok(text('docs/AUDIT.md').includes('不证明分布式并发'));
});
check('D11_replay_before_new_admission_and_quota', () => {
  assert.ok(text('docs/CONTRACTS.md').includes('先查旧任务，再判断新准入'));
  assert.ok(text('docs/BLUEPRINT.md').includes('新执行的额度/容量预留只能作用于新操作'));
  assert.ok(text('docs/BLUEPRINT.md').includes('VPS 先持久记录 START_GRANTED'));
});
check('D12_blueprint_snapshot_has_no_product_implementation', () => {
  for (const p of ['facade', 'adapters', 'src', 'node_modules']) assert.ok(!fs.existsSync(path.join(root, p)), p);
});

// A deliberately small in-memory model. Actual CAS, disk durability, child process
// ownership, auth and gateway retry code MUST be tested separately in P1-P5.
const terminal = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'EXPIRED']);
class LedgerModel {
  constructor() { this.tasks = new Map(); this.next = 1; this.settled = new Set(); }
  accept(identity, digest = 'body-A', options = {}) {
    const key = JSON.stringify(identity);
    const existing = this.tasks.get(key);
    if (existing) {
      need(existing.digest === digest, 'IDEMPOTENCY_CONFLICT');
      need(!existing.resultExpired, 'RESULT_EXPIRED');
      return existing;
    }
    need(options.ready !== false, 'NODE_UNAVAILABLE');
    need(options.quota !== false, 'QUOTA_EXHAUSTED');
    need([...this.tasks.values()].filter(t => !terminal.has(t.state)).length < 1, 'CAPACITY_EXHAUSTED');
    const t = {
      id: `task-${this.next++}`, key, digest, profile: options.profile ?? 'profile-v1',
      state: 'ACCEPTED', epoch: 0, possible: false, intent: false, starts: 0,
      events: new Map(), result: null, usage: null, resultExpired: false
    };
    this.tasks.set(key, t);
    return t;
  }
  claim(t) { need(t.state === 'ACCEPTED', 'BAD_STATE'); t.state = 'CLAIMED'; return ++t.epoch; }
  reclaim(t) { need(t.state === 'CLAIMED' && !t.possible, 'UNSAFE_RECLAIM'); t.epoch++; t.state = 'ACCEPTED'; }
  grant(t, epoch = t.epoch) {
    need(t.state === 'CLAIMED', 'BAD_STATE'); need(epoch === t.epoch, 'STALE_CLAIM');
    t.possible = true; t.state = 'START_GRANTED'; t.grant = `${t.id}:${t.epoch}`;
  }
  launch(t, crashBeforeSpawn = false) {
    need(!terminal.has(t.state), 'TERMINAL');
    if (t.intent) return false;
    need(t.state === 'START_GRANTED', 'NO_GRANT');
    t.intent = true;
    if (crashBeforeSpawn) { t.state = 'RECONCILING'; return false; }
    t.starts++; t.state = 'RUNNING'; return true;
  }
  uncertain(t) { if (t.possible && !terminal.has(t.state)) t.state = 'RECONCILING'; }
  waitTimeout(t) { return t.state; }
  cancel(t) {
    if (terminal.has(t.state)) return;
    t.state = t.possible ? 'CANCEL_REQUESTED' : 'CANCELLED';
  }
  confirmStop(t, confirmed) {
    need(t.state === 'CANCEL_REQUESTED' || t.state === 'RECONCILING', 'BAD_STATE');
    if (confirmed === true) t.state = 'CANCELLED';
  }
  expire(t) { if (!t.possible && !terminal.has(t.state)) t.state = 'EXPIRED'; }
  finish(t, evidence = {}) {
    const { done = true, exit = 0, output = 'native final answer', usage = null } = evidence;
    if (t.state === 'SUCCEEDED') { need(t.result === output, 'TERMINAL_CONFLICT'); return; }
    need(!terminal.has(t.state), 'TERMINAL_CONFLICT');
    need(t.possible && t.intent && done === true, 'NO_NATIVE_TERMINAL');
    need(typeof exit === 'number' && Number.isInteger(exit) && exit === 0, 'INVALID_EXIT');
    need(typeof output === 'string' && output.trim().length > 0, 'NATIVE_EMPTY_RESULT');
    t.state = 'SUCCEEDED'; t.result = output; t.usage = usage;
  }
  fail(t) { need(!terminal.has(t.state), 'TERMINAL'); t.state = 'FAILED'; }
  event(t, grant, seq, payload) {
    need(grant === t.grant, 'WRONG_GRANT');
    need(Number.isInteger(seq) && seq > 0, 'BAD_SEQUENCE');
    if (t.events.has(seq)) { need(t.events.get(seq) === payload, 'EVENT_CONFLICT'); return false; }
    t.events.set(seq, payload); return true;
  }
  settle(t) {
    need(t.state === 'SUCCEEDED', 'NOT_SUCCEEDED');
    if (t.usage === null || this.settled.has(t.key)) return false;
    this.settled.add(t.key); return true;
  }
}
const ident = (op = 'op-1', tenant = 'tenant-1', principal = 'principal-1', source = 'source-1') => [source, tenant, principal, op];
const fresh = () => { const l = new LedgerModel(); const t = l.accept(ident()); return { l, t }; };
const running = () => { const { l, t } = fresh(); l.claim(t); l.grant(t); l.launch(t); return { l, t }; };

check('M01_same_key_returns_same_task', () => { const { l, t } = fresh(); assert.equal(l.accept(ident()), t); assert.equal(l.tasks.size, 1); });
check('M02_changed_input_conflicts', () => { const { l } = fresh(); assert.throws(() => l.accept(ident(), 'body-B'), /IDEMPOTENCY_CONFLICT/); });
check('M03_offline_new_request_not_accepted', () => { const l = new LedgerModel(); assert.throws(() => l.accept(ident(), 'body-A', { ready: false }), /NODE_UNAVAILABLE/); assert.equal(l.tasks.size, 0); });
check('M04_shared_single_slot_across_profiles', () => { const { l } = fresh(); assert.throws(() => l.accept(ident('op-2'), 'body-A', { profile: 'other-harness' }), /CAPACITY_EXHAUSTED/); });
check('M05_launch_only_once', () => { const { l, t } = running(); assert.equal(l.launch(t), false); assert.equal(t.starts, 1); });
check('M06_lease_loss_after_grant_never_requeues', () => { const { l, t } = running(); l.uncertain(t); assert.throws(() => l.reclaim(t), /UNSAFE_RECLAIM/); assert.equal(t.possible, true); });
check('M07_reconnect_does_not_spawn_again', () => { const { l, t } = running(); l.uncertain(t); assert.equal(l.launch(t), false); assert.equal(t.starts, 1); });
check('M08_intent_crash_remains_uncertain', () => { const { l, t } = fresh(); l.claim(t); l.grant(t); l.launch(t, true); assert.equal(l.launch(t), false); assert.equal(t.state, 'RECONCILING'); assert.equal(t.starts, 0); });
check('M09_cancel_before_start_wins', () => { const { l, t } = fresh(); l.claim(t); l.cancel(t); assert.throws(() => l.grant(t), /BAD_STATE/); assert.equal(t.possible, false); });
check('M10_cancel_request_is_not_confirmation', () => { const { l, t } = running(); l.cancel(t); l.confirmStop(t, false); assert.equal(t.state, 'CANCEL_REQUESTED'); assert.throws(() => l.accept(ident('op-2')), /CAPACITY_EXHAUSTED/); });
check('M11_confirmed_cancel_no_respawn', () => { const { l, t } = running(); l.cancel(t); l.confirmStop(t, true); assert.throws(() => l.launch(t), /TERMINAL/); assert.equal(t.starts, 1); });
check('M12_http_timeout_keeps_task_and_slot', () => { const { l, t } = running(); l.waitTimeout(t); assert.equal(t.state, 'RUNNING'); assert.throws(() => l.accept(ident('op-2')), /CAPACITY_EXHAUSTED/); });
check('M13_prestart_reclaim_invalidates_old_claim', () => { const { l, t } = fresh(); const old = l.claim(t); l.reclaim(t); const current = l.claim(t); assert.throws(() => l.grant(t, old), /STALE_CLAIM/); l.grant(t, current); assert.equal(l.tasks.size, 1); });
check('M14_expiry_cannot_stop_possible_execution', () => { const { l, t } = running(); l.expire(t); assert.equal(t.state, 'RUNNING'); assert.equal(t.possible, true); });
check('M15_missing_native_terminal_not_success', () => { const { l, t } = running(); assert.throws(() => l.finish(t, { done: false }), /NO_NATIVE_TERMINAL/); assert.notEqual(t.state, 'SUCCEEDED'); });
check('M16_boolean_exit_not_integer_zero', () => { const { l, t } = running(); assert.throws(() => l.finish(t, { exit: false }), /INVALID_EXIT/); });
check('M17_string_exit_not_integer_zero', () => { const { l, t } = running(); assert.throws(() => l.finish(t, { exit: '0' }), /INVALID_EXIT/); });
check('M18_empty_result_not_synthetic_success', () => { const { l, t } = running(); assert.throws(() => l.finish(t, { output: '  ' }), /NATIVE_EMPTY_RESULT/); assert.equal(t.result, null); });
check('M19_success_is_immutable_under_cancel', () => { const { l, t } = running(); l.finish(t); l.cancel(t); assert.equal(t.state, 'SUCCEEDED'); assert.equal(t.result, 'native final answer'); });
check('M20_success_after_confirmed_cancel_conflicts', () => { const { l, t } = running(); l.cancel(t); l.confirmStop(t, true); assert.throws(() => l.finish(t), /TERMINAL_CONFLICT/); });
check('M21_duplicate_terminal_receipt_is_idempotent', () => { const { l, t } = running(); l.finish(t); l.finish(t); assert.equal(t.starts, 1); assert.throws(() => l.finish(t, { output: 'different' }), /TERMINAL_CONFLICT/); });
check('M22_duplicate_event_is_deduplicated', () => { const { l, t } = running(); assert.equal(l.event(t, t.grant, 1, 'hello'), true); assert.equal(l.event(t, t.grant, 1, 'hello'), false); assert.equal(t.events.size, 1); });
check('M23_conflicting_or_wrong_grant_event_rejected', () => { const { l, t } = running(); l.event(t, t.grant, 1, 'hello'); assert.throws(() => l.event(t, t.grant, 1, 'changed'), /EVENT_CONFLICT/); assert.throws(() => l.event(t, 'wrong', 2, 'text'), /WRONG_GRANT/); });
check('M24_offline_and_no_quota_replay_allowed', () => { const { l, t } = running(); l.finish(t); const replay = l.accept(ident(), 'body-A', { ready: false, quota: false }); assert.equal(replay, t); assert.equal(t.starts, 1); });
check('M25_profile_change_does_not_change_replay', () => { const { l, t } = running(); l.finish(t); assert.equal(l.accept(ident(), 'body-A', { profile: 'profile-v2' }).profile, 'profile-v1'); });
check('M26_tenant_scope_prevents_result_reuse', () => { const { l, t } = running(); l.finish(t); const other = l.accept(ident('op-1', 'tenant-2')); assert.notEqual(other.id, t.id); assert.equal(other.result, null); });
check('M27_principal_scope_prevents_result_reuse', () => { const { l, t } = running(); l.finish(t); assert.notEqual(l.accept(ident('op-1', 'tenant-1', 'principal-2')).id, t.id); });
check('M28_source_scope_prevents_result_reuse', () => { const { l, t } = running(); l.finish(t); assert.notEqual(l.accept(ident('op-1', 'tenant-1', 'principal-1', 'source-2')).id, t.id); });
check('M29_expired_result_tombstone_never_reexecutes', () => { const { l, t } = running(); l.finish(t); t.resultExpired = true; assert.throws(() => l.accept(ident()), /RESULT_EXPIRED/); assert.equal(l.tasks.size, 1); assert.equal(t.starts, 1); });
check('M30_unknown_usage_not_fake_zero_settlement', () => { const { l, t } = running(); l.finish(t); assert.equal(l.settle(t), false); assert.equal(t.usage, null); });
check('M31_known_usage_replay_settles_once', () => { const { l, t } = running(); l.finish(t, { usage: { input: 10, output: 4 } }); assert.equal(l.settle(t), true); assert.equal(l.settle(l.accept(ident())), false); assert.equal(l.settled.size, 1); });
check('M32_native_failure_no_restart', () => { const { l, t } = running(); l.fail(t); assert.equal(l.accept(ident()).state, 'FAILED'); assert.throws(() => l.launch(t), /TERMINAL/); assert.equal(t.starts, 1); });
check('M33_same_body_different_key_is_new_operation', () => { const { l, t } = running(); l.finish(t); assert.notEqual(l.accept(ident('op-2')).id, t.id); });
check('M34_prestart_expired_task_cannot_start', () => { const { l, t } = fresh(); l.expire(t); assert.throws(() => l.claim(t), /BAD_STATE/); assert.equal(t.starts, 0); });
check('M35_start_grant_without_intent_stays_uncertain', () => { const { l, t } = fresh(); l.claim(t); l.grant(t); l.uncertain(t); assert.throws(() => l.launch(t), /NO_GRANT/); assert.equal(t.possible, true); });
check('M36_native_refusal_is_not_transport_failure', () => { const { l, t } = running(); l.finish(t, { output: 'The native agent declined this operation.' }); assert.equal(t.state, 'SUCCEEDED'); assert.equal(t.starts, 1); });

const failed = results.filter(t => t.status === 'FAIL');
const report = {
  scope: 'DOCUMENT_AND_ABSTRACT_MODEL_ONLY',
  disclaimer: 'Not product, integration, vendor authorization or production security validation.',
  baseline_date: '2026-11-07', executed_at_utc: new Date().toISOString(),
  environment: { node: process.version, platform: process.platform, arch: process.arch },
  source_snapshots: snapshots,
  summary: { total: results.length, passed: results.length - failed.length, failed: failed.length },
  product_acceptance: 'NOT_RUN', release_gates: 'G01-G10 OPEN', tests: results
};
fs.writeFileSync(path.join(root, 'audit/results.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
const manifest = auditedFiles.slice().sort().map(p => {
  const hash = crypto.createHash('sha256').update(fs.readFileSync(path.join(root, p))).digest('hex');
  return `${hash}  ${p}`;
}).join('\n') + '\n';
fs.writeFileSync(path.join(root, 'audit/manifest.sha256'), manifest, 'utf8');
console.log(JSON.stringify(report.summary));
console.log('Scope: document structure and abstract model only; product acceptance NOT_RUN.');
for (const failure of failed) console.error(`${failure.name}: ${failure.message}`);
if (failed.length) process.exitCode = 1;
