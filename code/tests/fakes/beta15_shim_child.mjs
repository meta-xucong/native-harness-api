/** Self-authored synthetic child. No SDK, account or vendor code. */
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { createExternalRuntime } from '../../tools/beta15_external_runtime.mjs';
const mode = process.env.SHIM_FIXTURE_MODE || 'success';
const handlers = new Map();
let current = 'synthetic-a';
const catalog = () => [{configId:'model',name:'Model',type:'select',currentValue:current,
  options:[{value:'synthetic-a',name:'A'},{value:'synthetic-b',name:'B'}]}];
function update(sessionUpdate, extra={}) {
  handlers.get('session/update')?.({sessionId:'synthetic-root',update:{sessionUpdate,
    _meta:{'codebuddy.ai/requestId':'synthetic-run','codebuddy.ai/rootRequestId':'synthetic-run'},...extra}});
}
const client = {
  initialization:{protocolVersion:1},
  async connect(){},
  async close(){},
  on(name, handler) { handlers.set(name, handler); return () => handlers.delete(name); },
  session: {
    async new() { return {sessionId:'synthetic-root',configOptions:catalog()}; },
    async setConfigOption(params) { if(mode!=='wrong_model') current=params.value; return {configOptions:catalog()}; },
    async prompt() {
      if (mode==='orphan') spawn(process.execPath,['-e','setTimeout(()=>{},60000)'],{stdio:'ignore'}).unref();
      if (mode==='oversized') process.stdout.write('x'.repeat(300000)+'\n');
      if (mode==='bad_frame') process.stdout.write('{"id":3,"id":3}\n');
      if (mode==='stderr_overflow') process.stderr.write('x'.repeat(70000));
      if (mode!=='ack_only') {
        update('agent_message_chunk',{messageId:'synthetic-answer',content:{type:'text',text:'synthetic answer'}});
        update('state_update',{state:'idle',stopReason:'end_turn'});
      }
      return {messageId:'synthetic-inserted',_meta:{'codebuddy.ai/requestId':'synthetic-run'}};
    },
    async cancel() { update('state_update',{state:'idle',stopReason:'cancelled'}); },
    async close() { return {}; }
  }
};
const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
let settleHarness;
const harness={closed:new Promise(resolve=>{settleHarness=resolve;}),async close(){
  if(mode==='shutdown_hang') return new Promise(()=>{setInterval(()=>{},1000);});
  settleHarness();
}};
const shim=await createExternalRuntime({verifyEntry:async()=>true,
  loadApi:async()=>({createHarness:async()=>harness,createClient:()=>client}),
  configDir:process.cwd(),expectedCwd:process.cwd(),emit:line=>{
  process.stdout.write(line);
  if(mode==='late_conflict' && line.includes('"closed":true'))
    process.stdout.write('{"method":"session/update","params":{"sessionId":"synthetic-root","update":{"sessionUpdate":"state_update","state":"idle","stopReason":"cancelled","_meta":{"codebuddy.ai/requestId":"synthetic-run"}}}}\n');
}});
lines.on('line',line=>{
  let request; try {request=JSON.parse(line);} catch {lines.close();process.stdin.destroy();return;}
  Promise.resolve(shim.dispatch(request)).finally(()=>{
    if(request.method==='shim/close' && mode!=='shutdown_hang') {
      if(mode==='exit_nonzero') process.exitCode=7;
      lines.close();process.stdin.destroy();
    }
  });
});
