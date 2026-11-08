import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from native_harness_api.runtime import RuntimeConfig
from native_harness_api.native_trial_runtime import NativeTrialConfig, _QUARANTINED_PROFILES, _QUARANTINED_ENGINES, hold_retained

class Profile:
 def __init__(self,root):self.root=root;self.state='authenticated_confirmed';self.expected_account_digest='a'*64;self.calls=[]
 def verify(self):self.calls.append('verify')
 def begin_execution(self):self.calls.append('begin');self.state='execution_started'
 def environment(self,node):self.calls.append('env');return {'HOME':str(self.root/'home')}
 def finish_execution(self,**kw):self.calls.append('complete');self.state='authenticated_confirmed'
 def quarantine(self):self.calls.append('quarantine');self.state='unknown_retained'
 def close(self):self.calls.append('close')
 def __enter__(self):return self
 def __exit__(self,*_):self.close()

class NativeRuntimeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();(self.root/'workspace').mkdir();(self.root/'control').mkdir()
  self.value={'mode':'workbuddy_local_trial','adapter':'public_sfe','source':'s','ledger_instance':'l','bridge_url':'http://127.0.0.1:8791','binding':{'binding_id':'b','revision':1,'model_alias':'workbuddy-test','native_model':'model'},'owner':['u','k'],'profile_root':str(self.root),'assets_root':str(self.root/'assets')}
  self.config=NativeTrialConfig.from_value(self.value);self.profile=Profile(self.root)
  self.assets={'executable':self.root/'assets/sfe.exe','host_node':self.root/'assets/node.exe','product_config':self.root/'assets/product.json'}
 def tearDown(self):_QUARANTINED_PROFILES.clear();_QUARANTINED_ENGINES.clear();self.tmp.cleanup()
 def tools(self):return (lambda _:self.assets,lambda p:p,lambda _:self.profile)
 def test_load_config(self):
  p=self.root/'config.json';p.write_text(json.dumps(self.value));c=RuntimeConfig.load(p)
  self.assertIsInstance(c,NativeTrialConfig);self.assertEqual(c.data_dir,self.root/'control')
 def test_reject_options(self):
  for key,val in [('adapter','fake'),('execution_timeout',float('nan')),('execution_timeout',True),('profile_root','relative'),('data_dir',str(self.root/'outside'))]:
   v=dict(self.value);v[key]=val
   with self.subTest(key=key),self.assertRaises(ValueError):NativeTrialConfig.from_value(v)
 def test_synthetic_not_admitted(self):
  v=dict(self.value);v['binding']=dict(v['binding'],model_alias='synthetic-test')
  with self.assertRaises(ValueError):NativeTrialConfig.from_value(v)
 def test_native_facade_owner(self):
  f=self.config.facade({'NATIVE_BRIDGE_SOURCE_TOKEN':'s'*32,'NATIVE_FACADE_TOKEN':'f'*32});self.assertEqual(f.mode,'workbuddy_local_trial');self.assertEqual(next(iter(f.services.values())).owner,('u','k'))
 def run_engine(self,result=None,exc=None):
  with patch('native_harness_api.native_trial_runtime._tools',self.tools),patch('native_harness_api.public_sfe_owned_session.PublicSfeOwnedSession') as engine,patch('native_harness_api.supervised_acp_transport.SupervisedACPTransport'):
   engine.return_value.run.return_value=result
   if exc:engine.return_value.run.side_effect=exc
   out=self.config.invoke(model_id='model',messages=[{'role':'user','content':'hi'}]);return out,engine.return_value.run.call_args
 def test_clean_lifecycle(self):
  r,args=self.run_engine({'cleanup_confirmed':True});self.assertEqual(r,{'cleanup_confirmed':True})
  self.assertEqual(self.profile.calls,['verify','begin','env','complete','close'])
  self.assertEqual(args.args[0],[str(self.assets['executable']),'--stdio','--strict-mcp-config','--product-config',str(self.assets['product_config'])]);self.assertEqual(args.kwargs['expected_account_digest'],'a'*64);self.assertEqual(args.kwargs['prompt'],'hi');self.assertFalse(_QUARANTINED_PROFILES)
 def test_unknown_retains(self):
  self.run_engine({'cleanup_confirmed':False});self.assertEqual(self.profile.calls[-1],'quarantine');self.assertEqual(_QUARANTINED_PROFILES,[self.profile]);self.assertEqual(len(_QUARANTINED_ENGINES),1)
 def test_numeric_cleanup_not_true(self):
  self.run_engine({'cleanup_confirmed':1});self.assertEqual(self.profile.state,'unknown_retained')
 def test_exception_retains(self):
  with self.assertRaises(RuntimeError):self.run_engine(exc=RuntimeError('raw'))
  self.assertEqual(self.profile.state,'unknown_retained');self.assertNotIn('close',self.profile.calls)
 def test_not_authenticated_never_begins(self):
  self.profile.state='unknown_retained'
  with self.assertRaises(ValueError):self.run_engine({})
  self.assertEqual(self.profile.calls,['close'])
 def test_prompt_gate(self):
  with self.assertRaises(ValueError):self.config.invoke(model_id='other',messages=[{'role':'user','content':'hi'}])
  with self.assertRaises(ValueError):self.config.invoke(model_id='model',messages=[{'role':'assistant','content':'hi'}])
 def test_runner_constructs_no_inference(self):
  with patch('native_harness_api.native_trial_runtime._tools',self.tools):
   self.profile.state='unknown_retained';r=self.config.runner({'NATIVE_BRIDGE_NODE_TOKEN':'n'*32})
  self.assertEqual(r.policy.mode,'workbuddy_local_trial');self.assertNotIn('begin',self.profile.calls)
 def test_owned_fixture_through_native_adapter_once(self):
  import os,sys
  from dataclasses import replace
  from native_harness_api.contracts import Binding,digest
  from native_harness_api.public_sfe_adapter import PublicSfeAdapter
  from native_harness_api.public_sfe_session import account_identity_digest
  from test_public_sfe_owned_session import SyntheticTransport,CHILD
  from native_harness_api.supervised_acp_transport import SupervisedACPTransport
  starts=[]
  base=SupervisedACPTransport if os.name=='nt' else SyntheticTransport
  class FixtureTransport(base):
   def start(child,command,cwd,env):
    starts.append(command)
    return super().start([sys.executable,'-u',str(CHILD.resolve())],cwd,env)
  binding=Binding('fixture-binding',1,'workbuddy-fixture','synthetic-b')
  config=replace(self.config,binding=binding,execution_timeout=15,cleanup_timeout=5)
  self.profile.expected_account_digest=account_identity_digest({'id':'synthetic-account','authMethodId':'workbuddy-desktop'})
  self.profile.environment=lambda _:{k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP')}
  r={'model':binding.model_alias,'messages':[{'role':'user','content':'fixture'}],'stream':False,'n':1}
  t={'remote_task_id':'t','identity':{'source':'s','tenant':'u','principal':'k','operation':'o'},'ledger_instance':'l','request_digest':digest(r),'request':{'binding':binding.wire(),'client_request':r,'messages':r['messages']}}
  a=PublicSfeAdapter(self.root/'control',binding=binding,invoke=config.invoke)
  with patch('native_harness_api.native_trial_runtime._tools',self.tools),patch('native_harness_api.supervised_acp_transport.SupervisedACPTransport',FixtureTransport):
   result=a.start(t,{'start_grant_id':'g'});replay=a.start(t,{'start_grant_id':'g'})
  self.assertEqual(result['outcome'],'succeeded',result);self.assertEqual(result,replay);self.assertEqual(len(starts),1)
  self.assertEqual(result['output_blocks'],[{'type':'text','text':'synthetic answer'}]);self.assertEqual(self.profile.state,'authenticated_confirmed')

 def test_hold_retained_ignores_interrupt(self):
  _QUARANTINED_PROFILES.append(self.profile)
  count=[]
  def sleep(_):
   count.append(1)
   if len(count)==1:raise KeyboardInterrupt()
   _QUARANTINED_PROFILES.clear()
  with patch('native_harness_api.native_trial_runtime.time.sleep',sleep):hold_retained()
  self.assertEqual(len(count),2)
 def test_main_holds_on_once_and_output_failure(self):
  from unittest.mock import Mock
  from native_harness_api.runtime import main
  for failure in (None,BrokenPipeError(),KeyboardInterrupt()):
   with self.subTest(failure=type(failure).__name__),patch('native_harness_api.runtime.RuntimeConfig.load',return_value=Mock()),patch('native_harness_api.runtime.node_tick',return_value={'outcome':'pending'}),patch('builtins.print',side_effect=failure),patch('native_harness_api.native_trial_runtime.hold_retained') as hold:
    try:main(['node','--config','unused','--once'])
    except (BrokenPipeError,KeyboardInterrupt):pass
    hold.assert_called_once_with()
