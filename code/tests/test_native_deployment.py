import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from native_harness_api.runtime import RuntimeConfig
from native_harness_api.native_facade_runtime import NativeFacadeConfig

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from prepare_native_deployment import build_plan, write_plan

class DeploymentTests(unittest.TestCase):
 def setUp(self):
  self.spec={'bridge_origin':'https://bridge.example.com','user_id':7,'api_key_id':9,'model_alias':'workbuddy-test','native_model':'vendor-model', 'windows_bundle_root':'C:/NativeHarness','windows_profile_root':'C:/Profiles/new-profile','bridge_data_dir':'/var/lib/native-bridge'}
  self.ids={k:k+'-test' for k in ('source','ledger','workspace','node','executor','binding')}
  self.plan=build_plan(self.spec,self.ids)
 def test_exact_scope_in_all_configs(self):
  bridge,facade,node,sub=(self.plan[n] for n in ('bridge.json','facade.json','windows-node.json','sub2api.native-harness.yaml'))
  route=sub['gateway']['native_harness']['routes']['workbuddy-test']
  self.assertEqual(facade['owner'],['user-7','key-9']);self.assertEqual(bridge['harness']['allowed_owners'],[facade['owner']]);self.assertEqual(node['owner'],facade['owner'])
  for k in ('source','ledger_instance'):
   self.assertEqual(node[k],facade[k]);self.assertEqual(route[k],facade[k])
  for k,v in node['binding'].items():self.assertEqual(route[k],v)
  self.assertEqual(bridge['harness']['bindings'],[node['binding']])
  self.assertFalse(bridge['accepting_new_tasks']);self.assertFalse(bridge['harness']['accepting_new']);self.assertFalse(facade['accepting_new']);self.assertFalse(sub['gateway']['native_harness']['accepting_new'])
 def test_server_config_has_no_local_native_paths(self):
  data=self.plan['facade.json'];self.assertNotIn('assets_root',data);self.assertNotIn('profile_root',data)
  c=NativeFacadeConfig.from_value(data)
  with patch('native_harness_api.bridge_client.HTTPTransport.request',side_effect=AssertionError('No network')):
   f=c.facade({'NATIVE_BRIDGE_SOURCE_TOKEN':'s'*32,'NATIVE_FACADE_TOKEN':'f'*32})
  self.assertEqual(f.mode,'workbuddy_local_trial')
  with self.assertRaises(ValueError):c.runner()
 def test_loader_dispatch_and_no_overwrite(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'plan';write_plan(path,self.plan)
   c=RuntimeConfig.load(path/'facade.json');self.assertIsInstance(c,NativeFacadeConfig)
   with self.assertRaises(FileExistsError):write_plan(path,self.plan)
   self.assertEqual(json.loads((path/'deployment-identities.json').read_text())['identities'],self.ids)
 def test_bad_spec_rejected(self):
  for k,v in [('user_id',True),('api_key_id',0),('bridge_origin','http://bridge.example.com'),('bridge_origin','https://localhost'),('model_alias','synthetic-test'),('native_model','auto'),('native_model','fake-foo'),('windows_profile_root','relative'),('windows_bundle_root','C:/a/../b'),('bridge_data_dir','relative')]:
   with self.subTest(k=k,v=v),self.assertRaises((ValueError,TypeError)):
    build_plan(dict(self.spec,**{k:v}),self.ids)
 def test_facade_rejects_native_fields_and_modes(self):
  for change in ({'profile_root':'C:/x'},{'adapter':'public_sfe'},{'owner':['u']},{'wait_seconds':float('nan')},{'source_token_env':'NATIVE_FACADE_TOKEN'}):
   with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
    NativeFacadeConfig.from_value(dict(self.plan['facade.json'],**change))
 def test_config_json_is_yaml_compatible_and_secret_free(self):
  raw=json.dumps(self.plan);self.assertNotIn('api_key',self.plan['facade.json']);self.assertNotIn('token_value',raw)
  self.assertEqual(self.plan['sub2api.native-harness.yaml']['gateway']['native_harness']['allowed_api_key_id'],9)
 def test_preflight_server_only_get(self):
  from public_harness_preflight import check
  config=NativeFacadeConfig.from_value(self.plan['facade.json']);calls=[]
  def request(_self,method,path,payload=None):
   calls.append((method,path))
   if path=='/bridge/v1/capabilities':return {'harness_contract':'native-harness-bridge-v1','ledger_instance':'ledger-test'}
   return {'object':'list','data':[]}
  from native_harness_api.contracts import CONTRACT
  def capabilities(_self):calls.append(('GET','/bridge/v1/capabilities'));return {'harness_contract':CONTRACT,'ledger_instance':'ledger-test'}
  with patch('public_harness_preflight.BridgeClient.capabilities',capabilities),patch('public_harness_preflight.HTTPTransport.request',request):
   result=check(config,{'NATIVE_BRIDGE_SOURCE_TOKEN':'s'*32,'NATIVE_FACADE_TOKEN':'f'*32})
  self.assertEqual(calls,[('GET','/bridge/v1/capabilities'),('GET','/v1/models')]);self.assertFalse(result['inference_started']);self.assertFalse(result['native_account_verified'])
 def test_node_preflight_never_constructs_native_engine(self):
  from public_harness_preflight import check
  from native_harness_api.native_trial_runtime import NativeTrialConfig
  from unittest.mock import MagicMock
  with tempfile.TemporaryDirectory() as root:
   data=dict(self.plan['windows-node.json'],assets_root=root,profile_root=root)
   config=NativeTrialConfig.from_value(data)
   profile=MagicMock();profile.__enter__.return_value=profile;profile.state='execution_unknown'
   with patch('public_harness_runtime_assets.verify_runtime_assets') as verify,patch('public_harness_login_profile.open_profile',return_value=profile),patch('public_harness_preflight.NodeClient') as node,patch('native_harness_api.public_sfe_owned_session.PublicSfeOwnedSession',side_effect=AssertionError('No native')):
    result=check(config,{'NATIVE_BRIDGE_NODE_TOKEN':'n'*32})
   verify.assert_called_once_with(config.assets_root);node.return_value.next_task.assert_called_once_with()
   self.assertFalse(result['profile_metadata_confirmed']);self.assertFalse(result['native_account_verified']);self.assertFalse(result['inference_started'])
   profile.begin_execution.assert_not_called()
 def test_sub2api_preflight_is_explicit_get_only(self):
  from public_harness_preflight import check_sub2api
  config=NativeFacadeConfig.from_value(self.plan['facade.json'])
  value={'object':'list','data':[],'native_harness':{'accepting_new':False,'live_readiness_checked':False}}
  with patch('public_harness_preflight.HTTPTransport') as http:
   http.return_value.request.return_value=value
   result=check_sub2api(config,'https://sub2.example.com',{'NATIVE_SUB2API_TEST_KEY':'u'*32})
   http.return_value.request.assert_called_once_with('GET','/v1/native-harness/models')
  self.assertTrue(result['sub2api_auth_verified']);self.assertFalse(result['sub2api_model_listed']);self.assertFalse(result['sub2api_live_readiness_checked'])
  with self.assertRaises(ValueError):check_sub2api(config,'https://sub2.example.com',{})
 def test_preflight_cli_exit_and_optional_key_are_explicit(self):
  from public_harness_preflight import main
  import contextlib,io
  config=NativeFacadeConfig.from_value(self.plan['facade.json'])
  for ready in (False,True):
   with patch('public_harness_preflight.RuntimeConfig.load',return_value=config),patch('public_harness_preflight.check',return_value={'checks_passed':ready}),patch('public_harness_preflight.check_sub2api',side_effect=AssertionError('No optional key')) as sub,contextlib.redirect_stdout(io.StringIO()):
    self.assertEqual(main(['--config','unused']),0 if ready else 1)
    sub.assert_not_called()
  with patch('public_harness_preflight.RuntimeConfig.load',return_value=config),patch('public_harness_preflight.check',return_value={'checks_passed':True}),patch('public_harness_preflight.check_sub2api',return_value={'sub2api_auth_verified':True}) as sub,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(main(['--config','unused','--sub2api-origin','https://sub2.example.com']),0)
   sub.assert_called_once_with(config,'https://sub2.example.com')
