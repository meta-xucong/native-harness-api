"""Generated configs against real Bridge/Facade HTTP; no vendor, accounts or tasks."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_native_deployment import build_plan
from public_harness_preflight import check
from render_bridge.runtime import RuntimeConfig as BridgeRuntime
from native_harness_api.runtime import RuntimeConfig
from native_harness_api.bridge_client import NodeClient
from test_real_bridge import bridge_server, facade_server

class DeploymentPlanHTTPTests(unittest.TestCase):
 def test_generated_server_config_and_preflight(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   spec={'bridge_origin':'https://bridge.example.com','user_id':7,'api_key_id':9,'model_alias':'workbuddy-fixture','native_model':'fixture-model','windows_bundle_root':'C:/Client','windows_profile_root':'C:/Profile','bridge_data_dir':str(root/'state')}
   plan=build_plan(spec)
   for name,value in plan.items():(root/name).write_text(json.dumps(value))
   bridge=BridgeRuntime.load(root/'bridge.json')
   app=bridge.build_app({'BRIDGE_SOURCE_TOKEN':'s'*32,'BRIDGE_NODE_TOKEN':'n'*32})
   env={'NATIVE_BRIDGE_SOURCE_TOKEN':'s'*32,'NATIVE_FACADE_TOKEN':'f'*32}
   with bridge_server(app) as url:
    # Only transport origin is changed for this loopback fixture; all generated
    # identities, owner and model contracts remain byte-for-byte unchanged.
    config=replace(RuntimeConfig.load(root/'facade.json'),bridge_url=url)
    with facade_server(config.facade(env)) as facade_url:
     config=replace(config,port=urlsplit(facade_url).port)
     result=check(config,env)
     self.assertTrue(result['bridge_contract']);self.assertTrue(result['facade_auth'])
     self.assertFalse(result['inference_started']);self.assertFalse(result['native_account_verified'])
     self.assertIsNone(NodeClient(url,'n'*32).next_task())
   import sqlite3
   from contextlib import closing
   with closing(sqlite3.connect(root/'state/bridge.sqlite')) as con:
    self.assertEqual(con.execute('select count(*) from tasks').fetchone()[0],0)
