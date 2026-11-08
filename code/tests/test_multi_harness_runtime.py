"""Configuration fixtures only: no vendor execution, assets or login."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from native_harness_api.contracts import Binding, digest
from native_harness_api.harness_registry import ADAPTERS, binding_for, make_recipe, normalize_recipe, parse_targets, profile_path
from native_harness_api.multi_harness_runtime import MultiHarnessConfig
from native_harness_api.native_facade_runtime import NativeFacadeConfig
from native_harness_api.node import NodePolicy
from native_harness_api.runtime import RuntimeConfig


def target(adapter='workbuddy.public-sfe.v1', alias='workbuddy-test'):
    recipe = make_recipe(adapter_id=adapter, profile_path=r'C:\Native\profile-1',
        profile_id='profile-1', account_fingerprint='a'*64, native_model='native-model', public_alias=alias)
    return {'recipe': recipe, 'binding': binding_for(recipe).wire(), 'assets_root': r'C:\Native\assets'}


class RegistryTests(unittest.TestCase):
    def test_canonical_identity_and_v1_unchanged(self):
        r=target()['recipe'];b=binding_for(r)
        self.assertEqual(r['profile_path'],r'c:\native\profile-1')
        self.assertEqual(b.binding_id,'h2-'+digest(r))
        self.assertEqual(set(b.wire()),{'binding_id','revision','model_alias','native_model'})
        self.assertEqual(b.revision,1)
        for field,val in [('profile_id','profile-2'),('account_fingerprint','b'*64),('native_model','other'),
                          ('public_alias','workbuddy-other'),('profile_path',r'C:\Native\profile-2')]:
            self.assertNotEqual(b,binding_for(normalize_recipe(dict(r,**{field:val}))))
        self.assertEqual(Binding('old',7,'workbuddy-test','native-model').wire(),
            {'binding_id':'old','revision':7,'model_alias':'workbuddy-test','native_model':'native-model'})

    def test_pins_shape_path_and_binding(self):
        for value in ('relative','C:relative',r'\\server\share',r'C:\a\..\b',r'C:\%HOME%',r'C:\$HOME'):
            with self.subTest(value=value),self.assertRaises(ValueError):profile_path(value)
        for key,value in [('recipe_version',True),('adapter_id','fixture'),('product_version','other'),
                          ('asset_identity','b'*64),('policy_id','relaxed'),('native_model','auto'),
                          ('public_alias','codex-wrong'),('account_fingerprint','secret')]:
            t=target();t['recipe'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):parse_targets([t])
        t=target();t['binding']['revision']=2
        with self.assertRaises(ValueError):parse_targets([t])
        for items in ([],[target()]*2,[target()]*33):
            with self.assertRaises(ValueError):parse_targets(items)

    def test_frozen_copy_and_same_native_models(self):
        a=target();b=target('codex.app-server.v1','codex-test');parsed=parse_targets([a,b])
        a['recipe']['native_model']='changed'
        self.assertEqual(parsed[0].recipe['native_model'],'native-model')
        self.assertNotEqual(parsed[0].binding,parsed[1].binding)
        with self.assertRaises(TypeError):parsed[0].recipe['native_model']='changed'
        with self.assertRaises(TypeError):ADAPTERS['fixture']={}


class MultiRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.value={'mode':'harness_local_trial','schema_version':2,'source':'s','ledger_instance':'l',
            'bridge_url':'http://127.0.0.1:8791','owner':['u','k'],'data_dir':str(self.root),
            'targets':[target(),target('codex.app-server.v1','codex-test')]}
        self.config=MultiHarnessConfig.from_value(self.value)

    def test_load_and_exact_workbuddy_resolution(self):
        p=self.root/'config.json';p.write_text(json.dumps(self.value));config=RuntimeConfig.load(p)
        self.assertIsInstance(config,MultiHarnessConfig)
        invoke,project=config.resolve(config.configured_bindings[0])
        self.assertEqual(invoke.__self__.expected_profile_id,'profile-1')
        self.assertEqual(invoke.__self__.expected_account_fingerprint,'a'*64)
        from native_harness_api.public_sfe_adapter import project_result
        self.assertIs(project,project_result)
        b=config.configured_bindings[0]
        with self.assertRaises(ValueError):config.resolve(Binding(b.binding_id,2,b.model_alias,b.native_model))

    def test_schema_and_unexpected_options(self):
        for key,value in [('schema_version',True),('schema_version',1),('adapter','fake'),('fake_mode','success'),('binding',target()['binding'])]:
            v=dict(self.value);v[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):MultiHarnessConfig.from_value(v)

    def test_server_only_no_recipe_disclosure(self):
        v={k:v for k,v in self.value.items() if k not in ('targets','data_dir')}
        v.update(adapter='facade_only',bindings=[b.wire() for b in self.config.configured_bindings])
        config=NativeFacadeConfig.from_value(v)
        facade=config.facade({'NATIVE_BRIDGE_SOURCE_TOKEN':'s'*32,'NATIVE_FACADE_TOKEN':'f'*32})
        self.assertEqual(facade.mode,'harness_local_trial')
        self.assertEqual(set(next(iter(facade.services.values())).bindings),{'workbuddy-test','codex-test'})
        legacy=dict(v,mode='workbuddy_local_trial');legacy.pop('schema_version')
        with self.assertRaises(ValueError):NativeFacadeConfig.from_value(legacy)

    def test_same_storage_without_opening_current_profile(self):
        with patch('native_harness_api.native_trial_runtime._tools',return_value=(None,lambda p:p,None)):
            runner=self.config.runner({'NATIVE_BRIDGE_NODE_TOKEN':'n'*32})
        self.assertEqual(runner.adapter.db,self.root/'native-evidence'/'sfe-evidence.sqlite')
        self.assertEqual(runner.policy.mode,'harness_local_trial')
        self.assertEqual(runner.policy.bindings,self.config.configured_bindings)

    def test_policy_new_mode_only(self):
        NodePolicy('s','l',frozenset({('u','k')}),self.config.configured_bindings,mode='harness_local_trial')
        with self.assertRaises(ValueError):NodePolicy('s','l',frozenset({('u','k')}),self.config.configured_bindings,mode='workbuddy_local_trial')
        with self.assertRaises(ValueError):NodePolicy('s','l',frozenset({('u','k'),('v','k')}),self.config.configured_bindings,mode='harness_local_trial')
