/** One hosted no-account library handshake. Importing this file loads only Node builtins. */
import { open, lstat, realpath } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';

const PUBLIC_SHA = '8bfbfd68b6f4c04c70a94e1943ecc2a391dd5618e4a1cff087f77ea9d5402733';
const ASAR_SHA = 'dcabcd90f07a627a06f1fd55bc872a1c25a79aab43c3012930b6287840ab3d20';
class Deadline extends Error {}
const ERROR_CODES = Object.freeze({ ERR_MODULE_NOT_FOUND: 1, MODULE_NOT_FOUND: 2,
  ENOENT: 3, EACCES: 4, EPERM: 5, ERR_UNSUPPORTED_DIR_IMPORT: 6, ERR_UNKNOWN_FILE_EXTENSION: 7 });
export function classifyError(error) {
  // Never evaluate error.code, getters, message, stack, paths or string conversion.
  try {
    if (error === null || (typeof error !== 'object' && typeof error !== 'function')) return 8;
    const descriptor = Object.getOwnPropertyDescriptor(error, 'code');
    if (!descriptor || !Object.hasOwn(descriptor, 'value') || typeof descriptor.value !== 'string') return 8;
    return Object.hasOwn(ERROR_CODES, descriptor.value) ? ERROR_CODES[descriptor.value] : 8;
  } catch { return 8; }
}
const bounded = async (operation, ms, now = () => performance.now()) => {
  if (!Number.isFinite(ms) || ms <= 0) throw new Deadline();
  const end = now() + ms;
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(() => {
      if (now() >= end) throw new Deadline();
      return operation();
    }), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Deadline()), ms);
    })]);
  } finally { clearTimeout(timer); }
};

/** Injectable only for self-authored fixtures; real caller supplies one pinned import. */
export async function runHandshake(loadApi, { configDir, verifyEntry, timeoutMs = 30000, cleanupMs = 10000 } = {}, now = () => performance.now()) {
  if (typeof verifyEntry !== 'function') throw new TypeError();
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30000 ||
      !Number.isSafeInteger(cleanupMs) || cleanupMs < 1 || cleanupMs > 10000) throw new TypeError();
  const start = now();
  const dto = { schema_version: 2, entry_verified: false, import_ok: false, harness_created: false, client_created: false, connected: false,
    client_closed: false, harness_closed: false, harness_settled: false, success: false,
    protocol_version: 0, failure_stage: 0, error_class: 0, elapsed_ms: 0 };
  const controller = new AbortController();
  let harness, client, stage = 1;
  const remaining = () => {
    const left = timeoutMs - (now() - start);
    if (left <= 0) throw new Deadline();
    return left;
  };
  try {
    await bounded(verifyEntry, remaining(), now);
    remaining();
    dto.entry_verified = true;
    const api = await bounded(loadApi, remaining(), now);
    dto.import_ok = true;
    stage = 2;
    remaining(); // Check before scheduling any createHarness work.
    // The cancellation signal also fences a createHarness that resolves after our deadline.
    harness = await bounded(() => {
      remaining();
      const creation = Promise.resolve().then(() => {
        remaining();
        return api.createHarness({ configDir, strictMcpConfig: true, signal: controller.signal });
      });
      creation.then(value => {
        if (controller.signal.aborted && !harness) Promise.resolve().then(() => value.close()).catch(() => {});
      }, () => {});
      return creation;
    }, remaining(), now);
    if (!harness || typeof harness.close !== 'function' || typeof harness.closed?.then !== 'function') throw new TypeError();
    // Observe rejection immediately; explicit bounded settlement below still determines success.
    harness.closed.catch(() => {});
    dto.harness_created = true;
    stage = 3;
    remaining();
    client = api.createClient({ account: { mode: 'external' }, product: { mode: 'external' } });
    if (!client || typeof client.connect !== 'function' || typeof client.close !== 'function') throw new TypeError();
    dto.client_created = true;
    remaining();
    stage = 4;
    await bounded(() => client.connect(harness, { timeoutMs: remaining() }), remaining(), now);
    remaining();
    if (client.initialization?.protocolVersion !== 1) throw new TypeError();
    dto.protocol_version = 1;
    dto.connected = true;
  } catch (error) {
    dto.failure_stage = error instanceof Deadline ? 7 : stage;
    dto.error_class = classifyError(error);
  } finally {
    if (dto.failure_stage !== 0) controller.abort();
    const cleanupEnd = now() + cleanupMs;
    const closeStep = async (operation, flag, failure) => {
      try {
        // Invoke exactly once even if no wait budget remains. No microtask gate can
        // accidentally suppress a required close request.
        const pending = Promise.resolve(operation());
        pending.catch(() => {});
        const left = cleanupEnd - now();
        if (left <= 0) throw new Deadline();
        await bounded(() => pending, left, now);
        if (now() >= cleanupEnd) throw new Deadline();
        dto[flag] = true;
      } catch (error) {
        controller.abort();
        if (dto.failure_stage === 0) {
          dto.failure_stage = error instanceof Deadline ? 7 : failure;
          dto.error_class = classifyError(error);
        }
      }
    };
    if (client) await closeStep(() => client.close(), 'client_closed', 5);
    if (harness) {
      await closeStep(() => harness.close(), 'harness_closed', 6);
      await closeStep(() => harness.closed, 'harness_settled', 6);
    }
    dto.elapsed_ms = Math.min(120000, Math.max(0, Math.round(now() - start)));
  }
  dto.success = complete(dto);
  return dto;
}

export function complete(dto) {
  return dto.failure_stage === 0 && dto.error_class === 0 && dto.protocol_version === 1 &&
    ['entry_verified', 'import_ok', 'harness_created', 'client_created', 'connected', 'client_closed', 'harness_closed', 'harness_settled']
      .every(key => dto[key] === true);
}

async function physical(value, directory) {
  if (!path.isAbsolute(value) || path.resolve(value) !== value) throw new TypeError();
  for (let current = value;; current = path.dirname(current)) {
    const info = await lstat(current);
    if (info.isSymbolicLink()) throw new TypeError();
    if (current === value && (directory ? !info.isDirectory() : !info.isFile())) throw new TypeError();
    if (path.dirname(current) === current) break;
  }
  if ((await realpath(value)).toLowerCase() !== value.toLowerCase()) throw new TypeError();
}
function inside(root, value) {
  const relative = path.relative(root, value);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) throw new TypeError();
}

async function verifyBytes(file, expectedHash, maximum, expectedSize) {
  const source = await open(file, 'r');
  try {
    const hash = createHash('sha256');
    const buffer = Buffer.alloc(1024 * 1024);
    let total = 0;
    for (;;) {
      const { bytesRead } = await source.read(buffer, 0, buffer.length, null);
      if (!bytesRead) break;
      total += bytesRead;
      if (total > maximum) throw new TypeError();
      hash.update(buffer.subarray(0, bytesRead));
    }
    if ((expectedSize !== undefined && total !== expectedSize) || hash.digest('hex') !== expectedHash) throw new TypeError();
  } finally { await source.close(); }
}

async function main(argv) {
  let output;
  try {
    if (process.platform !== 'win32' || process.env.GITHUB_ACTIONS !== 'true' ||
        process.env.RUNNER_ENVIRONMENT !== 'github-hosted') throw new TypeError();
    if (argv.length !== 4 || argv[0] !== '--package-root' || argv[2] !== '--run-root') throw new TypeError();
    const packageRoot = argv[1], root = argv[3];
    const home = path.join(root, 'isolated', 'home');
    const config = path.join(root, 'isolated', 'config');
    const dto = path.join(root, 'output', 'library-summary.json');
    const resources = path.join(root, 'official', 'resources');
    if (packageRoot !== path.join(resources, 'app.asar.unpacked', 'node_modules', '@genie', 'agent-harness')) throw new TypeError();
    const archive = path.join(resources, 'app.asar');
    const entry = path.join(packageRoot, 'lib', 'public.js');
    const virtualEntry = path.join(archive, 'node_modules', '@genie', 'agent-harness', 'lib', 'public.js');
    for (const value of [root, home, config, path.dirname(dto)]) await physical(value, true);
    for (const value of [home, config, dto, entry]) inside(root, value);
    if (home !== path.join(root, 'isolated', 'home') || config !== path.join(root, 'isolated', 'config') ||
        dto !== path.join(root, 'output', 'library-summary.json') ||
        path.resolve(homedir()).toLowerCase() !== home.toLowerCase() ||
        process.env.USERPROFILE !== home || process.env.HOME !== home ||
        process.env.CODEBUDDY_CONFIG_DIR !== config || process.env.WORKBUDDY_CONFIG_DIR !== config ||
        process.cwd() !== path.join(root, 'workspace')) throw new TypeError();
    await physical(entry, false);
    const info = await lstat(entry);
    if (info.nlink !== 1 || info.size > 1024*1024) throw new TypeError();
    await verifyBytes(entry, PUBLIC_SHA, 1024*1024, info.size);
    await physical(archive, false);
    const archiveInfo = await lstat(archive);
    if (archiveInfo.nlink !== 1 || archiveInfo.size > 1024**3) throw new TypeError();
    await verifyBytes(archive, ASAR_SHA, 1024**3, archiveInfo.size);
    // Reserve output before vendor import. On any uncertain outcome it cannot be reused.
    output = await open(dto, 'wx', 0o600);
    const result = await runHandshake(() => import(pathToFileURL(virtualEntry).href), {
      configDir: config,
      // Only the official runtime's built-in ASAR-aware fs is exercised here.
      // No physical() relaxation, NODE_PATH, copied dependencies or loader patch.
      verifyEntry: () => verifyBytes(virtualEntry, PUBLIC_SHA, 1024*1024, info.size)
    });
    await output.writeFile(JSON.stringify(result) + '\n');
    await output.sync();
    process.exitCode = complete(result) ? 0 : 1;
  } catch {
    process.exitCode = 1; // No arbitrary exception strings or paths leave this process.
  } finally { if (output) await output.close().catch(() => { process.exitCode = 1; }); }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  await main(process.argv.slice(2));
}
