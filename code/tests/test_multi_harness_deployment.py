"""Data-only v2 deployment generation; native profiles are never opened."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from test_multi_harness_runtime import target
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from prepare_native_deployment import build_plan, main


class MultiDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.ids={k:k+'-test' for k in ('source','ledger','workspace','node','executor','binding')}
        self.spec={'schema_version':2,'bridge_origin':'https://bridge.example.com','user_id':7,'api_key_id':9,
            'windows_data_dir':r'C:\Profiles\old-profile\control','bridge_data_dir':'/var/lib/native-bridge',
            'targets':[target(),target('codex.app-server.v1','codex-test')]}

    def test_one_route_identity_and_exact_binding_across_configs(self):
        plan=build_plan(self.spec,self.ids)
        node=plan['windows-node.json'];facade=plan['facade.json'];bridge=plan['bridge.json']
        sub=plan['sub2api.native-harness.yaml']['gateway']['native_harness']
        bindings=[t['binding'] for t in self.spec['targets']]
        self.assertEqual(node['data_dir'],r'c:\profiles\old-profile\control')
        self.assertEqual([t['binding'] for t in node['targets']],bindings)
        self.assertEqual(facade['bindings'],bindings);self.assertEqual(bridge['harness']['bindings'],bindings)
        self.assertEqual(node['source'],facade['source']);self.assertEqual(node['ledger_instance'],facade['ledger_instance'])
        self.assertEqual(bridge['harness']['execution_mode'],'harness_local_trial')
        self.assertEqual(facade['mode'],node['mode']);self.assertEqual(node['schema_version'],2)
        self.assertFalse(node['accepting_new']);self.assertFalse(sub['accepting_new'])
        for b in bindings:
            route=sub['routes'][b['model_alias']]
            self.assertEqual({k:route[k] for k in b},b)
            self.assertEqual(route['execution_mode'],'harness_local_trial')
            self.assertEqual(route['source'],self.ids['source'])
        for name in ('facade.json','bridge.json','sub2api.native-harness.yaml'):
            serialized=json.dumps(plan[name])
            for private in ('profile_path','profile_id','account_fingerprint','assets_root','a'*64):
                self.assertNotIn(private,serialized)

    def test_invalid_spec_and_frozen_binding(self):
        for key,value in [('schema_version',True),('schema_version',1),('windows_data_dir','relative'),('targets',[])]:
            with self.subTest(key=key),self.assertRaises(ValueError):build_plan(dict(self.spec,**{key:value}),self.ids)
        changed=copy.deepcopy(self.spec);changed['targets'][0]['recipe']['profile_id']='new-profile'
        with self.assertRaises(ValueError):build_plan(changed,self.ids)
        with self.assertRaises(ValueError):build_plan(self.spec,{})

    def test_cli_reuses_old_identity_file_without_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);spec=root/'spec.json';old=root/'old.json';out=root/'new'
            spec.write_text(json.dumps(self.spec));old.write_text(json.dumps({'schema_version':1,'identities':self.ids}))
            original=old.read_bytes()
            args=['--spec',str(spec),'--identities',str(old),'--output',str(out)]
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args),0)
                emitted=(out/'deployment-identities.json').read_bytes()
                self.assertEqual(main(args),1)
            self.assertEqual(old.read_bytes(),original)
            self.assertEqual((out/'deployment-identities.json').read_bytes(),emitted)
            self.assertEqual(json.loads(emitted)['identities'],self.ids)
            self.assertEqual(json.loads(emitted)['specification'],self.spec)
            malformed=root/'malformed.json';malformed.write_text('{"identities": {}}')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--spec',str(spec),'--identities',str(malformed),'--output',str(root/'bad')]),1)
            self.assertFalse((root/'bad').exists())
