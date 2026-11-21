import assert from 'node:assert/strict';
import { PINS, verifyClientEntries } from '../../tools/workbuddy_client_mock_driver.mjs';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

// Data-only negative paths. No vendor import or real official bytes are used.
const root = await mkdtemp(path.join(tmpdir(), 'client-mock-loader-'));
try {
  const resources = path.join(root, 'resources'); await mkdir(resources);
  await writeFile(path.join(resources, 'app.asar'), 'synthetic-not-official');
  const fs = await import('node:fs/promises');
  let virtualRead = false;
  await assert.rejects(verifyClientEntries(resources, fs, {open() {virtualRead = true; throw Error('must not reach virtual');}}));
  assert.equal(virtualRead, false);
  assert.deepEqual(Object.keys(PINS), ['package.json', 'lib/client/public.js', 'lib/transport/effect.js']);
  assert.equal(Object.isFrozen(PINS), true);
  const calls = [];
  const physical = async (file, hash, limit, filesystem) => {
    assert.equal(filesystem, fs); assert.match(hash, /^[0-9a-f]{64}$/);
    calls.push(['physical', file, hash, limit]); return 123;
  };
  const virtual = async (file, hash, size, filesystem) => {
    assert.equal(filesystem, fs); assert.match(hash, /^[0-9a-f]{64}$/);
    calls.push(['virtual', file, hash, size]);
  };
  await verifyClientEntries(resources, fs, fs, physical, virtual);
  assert.deepEqual(calls.map(call => call[0]), ['physical','physical','virtual','physical','virtual','physical','virtual','virtual','virtual']);
  assert.equal(calls[3][2], PINS['lib/client/public.js']);
  assert.equal(calls[5][2], PINS['lib/transport/effect.js']);
  assert.equal(calls[6][3], 123);
  assert.equal(calls[7][3], 2087); assert.equal(calls[8][3], 12944);
  for (const breakAt of [3, 4, 5, 6, 7, 8]) {
    let count = 0;
    const fail = async (...args) => { if (++count === breakAt) throw Error('fixture mismatch'); return 123; };
    await assert.rejects(verifyClientEntries(resources, fs, fs, fail, fail));
    assert.equal(count, breakAt); // No later member is consulted after a mismatch.
  }
  console.log('client mock loader negative fixture passed');
} finally { await rm(root, {recursive: true, force: true}); }
