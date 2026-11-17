// The native adapter owns this process tree. The vendor process's console is
// separate from the Python adapter's private control stream.
import { spawn } from 'node:child_process';
import { Console } from 'node:console';
import { randomBytes } from 'node:crypto';
import { rm, writeFile } from 'node:fs/promises';
import { createConnection } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';

// The method names and value schemas are provided by the fixed official Client.
const requests = {
  'product/read': 'product.read',
  'account/read': 'account.read',
  'auth/status': 'auth.status',
  'session/new': 'session.new',
  'session/set_config_option': 'session.setConfigOption',
  'session/prompt': 'session.prompt',
  'session/close': 'session.close',
};

function rpcError(error) {
  return { code: Number.isInteger(error?.code) ? error.code : -32603,
    message: 'WorkBuddy Client request failed' };
}

export async function relay({ createClient, endpoint, input, send }) {
  const reader = createInterface({ input, crlfDelay: Infinity });
  const early = [];
  const peerRequests = new Map();
  let peerId = 1000000000;
  let client;
  let initialized = false;
  let closing = false;
  let lostConnection = false;
  const output = (message) => {
    if (!closing) send({ jsonrpc: '2.0', ...message });
  };

  const reportUpdate = (method, params) => {
    if (initialized) output({ method, params });
    else {
      if (early.length >= 64) throw new Error('Initialization update limit exceeded');
      early.push({ method, params });
    }
  };
  const reverseRequest = (method, params, { signal }) => new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const id = peerId++;
    const abort = () => {
      peerRequests.delete(id);
      reject(signal.reason);
    };
    signal.addEventListener('abort', abort, { once: true });
    peerRequests.set(id, (response) => {
      signal.removeEventListener('abort', abort);
      if ('error' in response) reject(new Error('Native adapter refused the reverse request'));
      else resolve(response.result);
    });
    output({ id, method, params });
  });

  try {
    for await (const line of reader) {
      const message = JSON.parse(line);
      const { id, method, params } = message;
      if (message.jsonrpc !== '2.0') throw new Error('Invalid adapter request');
      if (method === 'initialize' && !client && params.protocolVersion === 1) {
        const { agent, account, product } = params.capabilities;
        client = createClient({ clientInfo: params.clientInfo, capabilities: agent, account, product });
        for (const event of ['account/updated', 'product/updated', 'session/update']) {
          client.on(event, (update) => reportUpdate(event, update));
        }
        for (const event of ['elicitation/create', 'session/request_permission']) {
          client.on(event, (request, context) => reverseRequest(event, request, context));
        }
        await client.connect(endpoint);
        void client.connection.closed.then(() => {
          if (!closing) {
            lostConnection = true;
            reader.close();
          }
        });
        output({ id, result: client.initialization });
      } else if (method === 'initialized' && client && !initialized) {
        // The official Client already sent initialized. The Python peer now
        // expects the managed Account and Product notifications in wire order.
        initialized = true;
        for (const update of early.splice(0)) output(update);
      } else if (initialized && method === 'session/cancel') {
        void client.session.cancel(params).catch(() => {
          lostConnection = true;
          reader.close();
        });
      } else if (initialized && Object.hasOwn(requests, method)) {
        const [group, name] = requests[method].split('.');
        void client[group][name](params).then(
          (result) => output({ id, result }),
          (error) => output({ id, error: rpcError(error) }),
        );
      } else if (!method) {
        // A reply for a reverse request already cancelled by the SDK can arrive late.
        const reply = peerRequests.get(id);
        peerRequests.delete(id);
        reply?.(message);
      } else {
        throw new Error('Unsupported adapter message');
      }
    }
  } finally {
    closing = true;
    reader.close();
    await client?.close();
  }
  if (lostConnection) throw new Error('WorkBuddy Client disconnected');
}

async function serve(launcher, args) {
  process.argv.splice(1, process.argv.length - 1, launcher, ...args);
  process.stdin.once('end', () => process.emit('SIGINT'));
  process.stdin.resume();
  await import(pathToFileURL(launcher));
}

function isListening(pipe) {
  return new Promise((resolve) => {
    const socket = createConnection(pipe);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => { socket.destroy(); resolve(false); });
  });
}

async function main(launcher, args) {
  // SDK-side console diagnostics also use stderr. Only the JSON messages below
  // use this adapter-owned stdout. The vendor CLI lives in the child process.
  globalThis.console = new Console({ stdout: process.stderr, stderr: process.stderr });
  const sdk = join(dirname(launcher), 'package', 'lib', 'client', 'public.js');
  const { createClient } = await import(pathToFileURL(sdk));
  const name = 'native-harness-workbuddy-' + randomBytes(12).toString('hex');
  const pipe = '\\\\.\\pipe\\' + name;
  const url = 'ws+unix:////./pipe/' + name + ':/rpc';
  const token = randomBytes(32).toString('hex');
  // cwd is the existing ACL-verified per-attempt workspace under the profile.
  const tokenPath = join(process.cwd(), name + '.token');
  await writeFile(tokenPath, token, { flag: 'wx' });
  let child;
  let exit;
  try {
    child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--serve', launcher, ...args,
      '--listen', url, '--ws-auth', 'capability-token', '--ws-token-file', tokenPath],
    { stdio: ['pipe', 'ignore', 'ignore'], windowsHide: true });
    child.stdin.on('error', () => {});
    exit = new Promise((resolve) => {
      child.once('exit', resolve);
      child.once('error', () => resolve(null));
    });
    const deadline = Date.now() + 30000;
    while (!await isListening(pipe)) {
      if (child.exitCode !== null || child.signalCode !== null || child.pid === undefined || Date.now() >= deadline) {
        throw new Error('WorkBuddy listener failed to start');
      }
      await delay(40);
    }
    await relay({ createClient, endpoint: { url, headers: { Authorization: 'Bearer ' + token }, timeoutMs: 90000 },
      input: process.stdin, send: (message) => process.stdout.write(JSON.stringify(message) + '\n') });
  } finally {
    child?.stdin.end();
    const code = exit ? await exit : null;
    await rm(tokenPath, { force: true });
    if (code !== 0) process.exitCode = 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]).toLowerCase() === fileURLToPath(import.meta.url).toLowerCase()) {
  const [first, ...rest] = process.argv.slice(2);
  try {
    if (first === '--serve') await serve(rest[0], rest.slice(1));
    else await main(first, rest);
  } catch {
    process.stderr.write('WorkBuddy Named Pipe connection stopped.\n');
    process.exitCode = 1;
  }
}
