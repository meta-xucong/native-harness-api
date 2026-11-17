import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { relay } from '../tools/workbuddy_named_pipe.mjs';

function fixture() {
  const input = new PassThrough();
  const output = [];
  const listeners = {};
  const ended = Promise.withResolvers();
  const operations = [];
  const client = {
    on(method, callback) { listeners[method] = callback; },
    connection: { closed: ended.promise },
    initialization: { protocolVersion: 1, serverInfo: { name: 'sdk-fixture' } },
    async connect() {
      operations.push('connect');
      listeners['account/updated']({ fixture: 'account' });
      listeners['product/updated']({ fixture: 'product' });
    },
    async close() { operations.push('close'); ended.resolve(); },
    session: {
      async prompt(params) {
        operations.push('prompt');
        assert.deepEqual(params, { sessionId: 'fixture-session' });
        listeners['session/update']({ sessionId: params.sessionId, update: { sessionUpdate: 'state_update' } });
        return { messageId: 'fixture-message' };
      },
      async close() { throw Object.assign(new Error('private data'), { code: -32602 }); },
    },
  };
  const done = relay({ createClient: () => client, endpoint: {}, input, send: (message) => output.push(message) });
  const send = (message) => input.write(JSON.stringify({ jsonrpc: '2.0', ...message }) + '\n');
  const receive = async (index) => {
    for (let attempt = 0; attempt < 100 && !output[index]; attempt++) await delay(5);
    assert.ok(output[index], 'Adapter message not received');
    return output[index];
  };
  send({ id: 1, method: 'initialize', params: { protocolVersion: 1, capabilities: {} } });
  return { input, output, listeners, operations, ended, send, receive, done };
}

test('preserves native startup events, returns native ACK and refuses peer requests via the adapter', async () => {
  const io = fixture();
  assert.equal((await io.receive(0)).result.protocolVersion, 1);
  assert.equal(io.output.length, 1);
  io.send({ method: 'initialized', params: {} });
  assert.equal((await io.receive(1)).method, 'account/updated');
  assert.equal((await io.receive(2)).method, 'product/updated');
  const reverse = io.listeners['session/request_permission']({}, { signal: new AbortController().signal });
  const question = await io.receive(3);
  assert.equal(question.method, 'session/request_permission');
  io.send({ id: question.id, result: { outcome: { outcome: 'cancelled' } } });
  assert.deepEqual(await reverse, { outcome: { outcome: 'cancelled' } });
  io.send({ id: 2, method: 'session/prompt', params: { sessionId: 'fixture-session' } });
  assert.equal((await io.receive(4)).method, 'session/update');
  assert.deepEqual((await io.receive(5)).result, { messageId: 'fixture-message' });
  io.send({ id: 3, method: 'session/close', params: { sessionId: 'fixture-session' } });
  assert.deepEqual((await io.receive(6)).error, { code: -32602, message: 'WorkBuddy Client request failed' });
  io.input.end();
  await io.done;
  assert.deepEqual(io.operations, ['connect', 'prompt', 'close']);
});

test('a lost pipe terminates the adapter without a second connection or prompt', async () => {
  const io = fixture();
  const failure = assert.rejects(io.done, /WorkBuddy Client disconnected/);
  await io.receive(0);
  io.ended.resolve();
  await failure;
  io.input.end();
  assert.deepEqual(io.operations, ['connect', 'close']);
});
