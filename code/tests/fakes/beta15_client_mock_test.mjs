/** Self-authored wire client fake. No vendor code, package, endpoint or account. */
import assert from 'node:assert/strict';
import { runClientMock } from '../../tools/beta15_client_mock.mjs';

function fakeApi(mode = '') {
  let clients = 0;
  return {
    fromAcpStream: streams => streams,
    closeTransport: transport => transport.cleanup ? transport.cleanup() : Promise.all([transport.readable.cancel(),transport.writable.close()]),
    createClient(options) {
      assert.equal(options.account.mode, 'managed'); assert.equal(options.product.mode, 'external');
      const index = clients++;
      let reader, writer, reading, closed = false, nextId = 1;
      const handlers = new Map(), pending = new Map();
      const request = (method, params = {}, opts = {}) => {
        assert.equal(Object.hasOwn(opts,'signal'),false); // Pinned SDK option is cancellationSignal.
        if (mode === 'premature-ack' && method === 'account/read') return Promise.resolve({state:'authenticated',mode:'managed',account:{id:'synthetic-account'}});
        const id = nextId++;
        return new Promise((resolve, reject) => {
          const abort = () => {
            if(mode === 'no-cancel-wire') return;
            writer.write({jsonrpc:'2.0',method:'$/cancel_request',params:{requestId:id}}).catch(()=>{}); // SDK leaves response pending until peer settles it.
          };
          pending.set(id, {resolve, reject, method, signal:opts.cancellationSignal, abort});
          opts.cancellationSignal?.addEventListener('abort', abort, {once:true});
          const send = () => writer.write({jsonrpc:'2.0',id,method,params}).catch(reject);
          if (mode === 'delayed-login' && method === 'auth/login' && index === 3) setTimeout(send, 60);
          else send();
        });
      };
      const client = {
        on(method, handler) { handlers.set(method, handler); },
        async connect(settings, extra) {
          assert.equal(extra, undefined); assert.ok(settings.timeoutMs > 0);
          reader = settings.transport.readable.getReader(); writer = settings.transport.writable.getWriter();
          let cleanup;
          settings.transport.cleanup = () => cleanup ??= (async () => {
            await reader.cancel(); await writer.close(); await reading;
            reader.releaseLock(); writer.releaseLock();
          })();
          client.transportCleanup = settings.transport.cleanup;
          reading = (async()=> {
            for (;;) {
              const {value:m,done} = await reader.read(); if(done) break;
              if(m.method === 'elicitation/create') {
                const result = await handlers.get(m.method)(m.params);
                await writer.write({jsonrpc:'2.0',id:m.id,result});
              } else {
                const call = pending.get(m.id); if(!call) continue; pending.delete(m.id);
                call.signal?.removeEventListener('abort',call.abort);
                if(m.error) call.reject(new Error('PRIVATE_SECRET'));
                else if(call.method === 'account/read' && m.result?.state === 'authenticated' && m.result.account === null && mode !== 'missing-validator') call.reject(new TypeError('PRIVATE_SECRET'));
                else call.resolve(m.result);
              }
            }
          })(); reading.catch(()=>{});
          client.initialization = await request('initialize', {protocolVersion:1,clientInfo:options.clientInfo,capabilities:{account:options.account,product:options.product,agent:options.capabilities,config:{warning:{}}}});
          if(mode === 'bad-catalog') client.initialization.authMethods = [];
          await writer.write({jsonrpc:'2.0',method:'initialized',params:{}});
        },
        auth:{login:(p,o)=>request('auth/login',p,o),status:(p,o)=>request('auth/status',p,o)},
        account:{read:(p,o)=>request('account/read',p,o)},
        async close() {
          if(closed) return; closed = true;
          if(mode === 'cleanup-exhausts-operation' && index === 0) { const end=Date.now()+50; while(Date.now()<end) {} }
          if(mode === 'cleanup-error' && index === 0) throw Error('PRIVATE_SECRET');
          if(mode === 'cleanup-timeout' && index === 0) return new Promise(()=>{});
          for(const call of pending.values()) {call.signal?.removeEventListener('abort',call.abort);call.reject(new Error('PRIVATE_SECRET'));} pending.clear();
          await client.transportCleanup?.();
        }
      }; return client;
    }
  };
}
let count = 0;
const order = [];
const good = await runClientMock(async()=>{order.push('load');return fakeApi();},{verifyEntry:async()=>{order.push('verify');}});
assert.deepEqual(order,['verify','load']); assert.equal(good.success,true);assert.equal(good.cases_completed,4);assert.equal(good.clients_closed,4);assert.equal(good.peers_closed,4);assert.equal(good.all_account_data_synthetic,true);
assert.equal(good.native_account_verified,false);assert.equal(good.real_model_ready,false);count++;
for(const mode of ['premature-ack','missing-validator','bad-catalog']) {
 const dto=await runClientMock(async()=>fakeApi(mode),{verifyEntry:async()=>{}});
 assert.equal(dto.success,false);assert.equal(dto.native_account_verified,false);assert.equal(dto.real_model_ready,false);
 assert.ok(!JSON.stringify(dto).includes('PRIVATE_SECRET'));count++;
}
let imported=0;
const failed=await runClientMock(async()=>{imported++;return fakeApi();},{verifyEntry:async()=>{throw Error('PRIVATE_SECRET');}});
assert.equal(imported,0);assert.equal(failed.entry_verified,false);assert.equal(failed.failure_stage,1);count++;
const timed=await runClientMock(async()=>fakeApi(),{verifyEntry:()=>new Promise(()=>{}),timeoutMs:15,cleanupMs:15});
assert.equal(timed.failure_stage,7);assert.equal(timed.success,false);count++;
const close=await runClientMock(async()=>fakeApi('cleanup-timeout'),{verifyEntry:async()=>{},timeoutMs:1000,cleanupMs:15});
assert.equal(close.failure_stage,7);assert.equal(close.clients_closed,0);assert.equal(close.success,false);count++;

let transportCalls = 0;
const slowCreation = fakeApi(); const createSlow = slowCreation.createClient;
slowCreation.createClient = opts => { const client=createSlow(opts); const end=Date.now()+50;while(Date.now()<end){} return client; };
slowCreation.fromAcpStream = streams => {transportCalls++;return streams;};
const expiredCreation = await runClientMock(async()=>slowCreation,{verifyEntry:async()=>{},timeoutMs:20});
assert.equal(expiredCreation.failure_stage,7);assert.equal(transportCalls,0);count++;
const slowCleanup = fakeApi('cleanup-exhausts-operation'); const createNormal=slowCleanup.createClient;let creationCount=0;
slowCleanup.createClient = opts => {creationCount++;return createNormal(opts);};
const expiredNext = await runClientMock(async()=>slowCleanup,{verifyEntry:async()=>{},timeoutMs:30});
assert.equal(expiredNext.failure_stage,7);assert.equal(creationCount,1);assert.equal(expiredNext.peers_closed,1);count++;

const noCancel = await runClientMock(async()=>fakeApi('no-cancel-wire'),{verifyEntry:async()=>{},timeoutMs:150});
assert.equal(noCancel.success,false);assert.equal(noCancel.failure_stage,7);assert.equal(noCancel.cases_completed,3);
assert.equal(noCancel.abort_wire_seen,false);assert.equal(noCancel.timeout_rejected,false);assert.equal(noCancel.clients_closed,4);assert.equal(noCancel.peers_closed,4);count++;

const delayed = await runClientMock(async()=>fakeApi('delayed-login'),{verifyEntry:async()=>{},timeoutMs:2000});
assert.equal(delayed.success,true);assert.equal(delayed.abort_wire_seen,true);assert.equal(delayed.late_ack_not_ready,true);assert.equal(delayed.peers_closed,4);count++;

const fallback = await runClientMock(async()=>fakeApi('cleanup-error'),{verifyEntry:async()=>{}});
assert.equal(fallback.success,false);assert.equal(fallback.clients_closed,0);assert.equal(fallback.peers_closed,1);count++;
const missingClose = await runClientMock(async()=>{const api=fakeApi();delete api.closeTransport;return api;},{verifyEntry:async()=>{}});
assert.equal(missingClose.failure_stage,1);assert.equal(missingClose.import_ok,false);count++;
for(const opts of [{timeoutMs:0},{cleanupMs:0},{timeoutMs:30001},{cleanupMs:10001}]) {await assert.rejects(runClientMock(async()=>fakeApi(),{verifyEntry:async()=>{},...opts}));count++;}
for(const value of Object.values(good)) assert.ok(typeof value==='boolean'||Number.isSafeInteger(value));
console.log(`synthetic_client_mock_tests=${count}`);
