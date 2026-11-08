/** Self-authored API fakes only. No vendor imports or credentials. */
import assert from 'node:assert/strict';
import { runManagedCatalog } from '../../tools/beta15_managed_catalog.mjs';
const productConfig = {schemaVersion:1,kind:'overlay',configuration:{productName:'WorkBuddy',applicationName:'WorkBuddy',productVersion:'5.9.3',authentication:{id:'workbuddy-desktop',type:'account-scoped-external-link',attributes:{platform:'workbuddy'}}}};
const publicProduct = {state:'ready',mode:'managed',productName:'WorkBuddy',applicationName:'WorkBuddy',productVersion:'5.9.3'};
const options = extra => ({verifyEntry:async()=>{},verifyEmptyRoots:async()=>true,loadProduct:async()=>structuredClone(productConfig),configDir:'/synthetic',...extra});
function fixture(failure='') {
  const calls=[];let elicitation,signal;
  const harness={closed:Promise.resolve(),close:async()=>{calls.push('harness.close');if(failure==='harness.close')throw Error('PRIVATE_SECRET');}};
  const client={initialization:{protocolVersion:1,authMethods:[{type:'agent',methodId:'workbuddy-desktop',providerType:'account-scoped-external-link'}]},
    on(method,handler){assert.equal(method,'elicitation/create');elicitation=handler;},
    async connect(h){calls.push('connect');assert.equal(h,harness);if(failure==='connect')throw Error('PRIVATE_SECRET');if(failure==='elicitation'){assert.deepEqual(elicitation(new Proxy({},{get(){throw Error('must not inspect');}})),{action:'cancel'});assert.equal(signal.aborted,true);}},
    product:{read:async(params,opts)=>{assert.deepEqual(params,{});assert.ok(opts.cancellationSignal instanceof AbortSignal);assert.equal(Object.hasOwn(opts,'signal'),false);calls.push('product.read');return failure==='identity'?{...publicProduct,productName:'CodeBuddy'}:publicProduct;}},
    account:{read:async(params,opts)=>{assert.deepEqual(params,{});assert.ok(opts.cancellationSignal instanceof AbortSignal);assert.equal(Object.hasOwn(opts,'signal'),false);calls.push('account.read');if(failure==='account.deadline')return new Promise(()=>{});return failure==='account'?{state:'authenticated',mode:'managed',account:{id:'synthetic'}}:{state:'unauthenticated',mode:null,account:null};}},
    auth:{status:async(params,opts)=>{assert.deepEqual(params,{});assert.ok(opts.cancellationSignal instanceof AbortSignal);assert.equal(Object.hasOwn(opts,'signal'),false);calls.push('auth.status');return{authenticated:failure==='status'};}},
    close:async()=>{calls.push('client.close');if(failure==='close.elicitation')elicitation();if(failure==='client.close')throw Error('PRIVATE_SECRET');}
  };
  if(failure==='catalog')client.initialization.authMethods[0].providerType='cli-external-link';
  if(failure==='protocol')client.initialization.protocolVersion=2;
  const load=async()=>{calls.push('load');if(failure==='import')throw Error('PRIVATE_SECRET');return{
    createHarness:async opts=>{calls.push('createHarness');assert.deepEqual(opts.productConfig,productConfig);assert.equal(opts.configDir,'/synthetic');assert.equal(opts.strictMcpConfig,true);signal=opts.signal;if(failure==='harness')throw Error('PRIVATE_SECRET');return harness;},
    createClient:opts=>{calls.push('createClient');assert.deepEqual(opts,{account:{mode:'managed'},product:{mode:'managed'},capabilities:{elicitation:{form:{},url:{}}}});if(failure==='client')throw Error('PRIVATE_SECRET');return client;}
  };};
  return{calls,load};
}
let count=0;
for(const [failure,stage] of [['',0],['import',1],['harness',2],['client',3],['connect',4],['protocol',4],['elicitation',4],['identity',5],['catalog',6],['account',7],['status',8],['client.close',9],['harness.close',9],['close.elicitation',4],['account.deadline',10]]) {
 const f=fixture(failure);const dto=await runManagedCatalog(f.load,options({timeoutMs:failure==='account.deadline'?20:1000}));
 assert.equal(dto.failure_stage,stage,failure);assert.equal(dto.success,stage===0,failure);
 assert.equal(dto.native_account_verified,false);assert.equal(dto.real_model_ready,false);assert.ok(!JSON.stringify(dto).includes('PRIVATE_SECRET'));
 assert.ok(Object.values(dto).every(v=>typeof v==='boolean'||Number.isSafeInteger(v)));
 if(failure==='')assert.deepEqual(f.calls,['load','createHarness','createClient','connect','product.read','account.read','auth.status','client.close','harness.close']);
 if(['account','catalog','identity','elicitation'].includes(failure))assert.equal(f.calls.includes('auth.status'),false);
 count++;
}
for(const value of [false,undefined,null]) {
 let imported=false;const dto=await runManagedCatalog(async()=>{imported=true;},options({verifyEmptyRoots:async()=>value}));
 assert.equal(dto.failure_stage,1);assert.equal(dto.empty_roots_verified,false);assert.equal(imported,false);count++;
}
const ordering=[];const f=fixture();await runManagedCatalog(async()=>{ordering.push('load');return f.load();},options({verifyEntry:async()=>{ordering.push('entry');},verifyEmptyRoots:async()=>{ordering.push('empty');return true;},loadProduct:async()=>{ordering.push('product');return productConfig;}}));
assert.deepEqual(ordering,['entry','empty','product','load']);count++;
for(const mutate of [p=>{p.configuration.authentication.attributes.token='synthetic';},p=>{p.configuration.include='synthetic';},p=>{p.configuration.authentication.type='cli-external-link';}]) {
 const p=structuredClone(productConfig);mutate(p);let imported=false;const dto=await runManagedCatalog(async()=>{imported=true;},options({loadProduct:async()=>p}));assert.equal(dto.failure_stage,1);assert.equal(imported,false);count++;
}
let release,lateClosed=0;const waiting=new Promise(resolve=>{release=resolve;});
const late=await runManagedCatalog(async()=>({createHarness:async({signal})=>{await waiting;assert.equal(signal.aborted,true);return{close:async()=>{lateClosed++;},closed:Promise.resolve()};},createClient(){throw Error('forbidden');}}),options({timeoutMs:20}));
assert.equal(late.failure_stage,10);release();await new Promise(resolve=>setImmediate(resolve));assert.equal(lateClosed,1);count++;
let verifyImports=0;const expired=await runManagedCatalog(async()=>{verifyImports++;},options({verifyEntry:()=>new Promise(()=>{}),timeoutMs:15}));assert.equal(expired.failure_stage,10);assert.equal(verifyImports,0);count++;
for(const extra of [{timeoutMs:0},{cleanupMs:0},{timeoutMs:30001},{cleanupMs:10001}]){await assert.rejects(runManagedCatalog(async()=>({}),options(extra)));count++;}
console.log(`synthetic_managed_catalog_tests=${count}`);
