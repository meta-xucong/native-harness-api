"""Secret-free ordinary provider setup; no native runtime or account access."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / n) for n in ('tools', 'packaging')]
from native_harness_api.native_facade_runtime import NativeFacadeConfig
from native_harness_api.facade import Facade
from prepare_native_deployment import build_plan, main
from test_multi_harness_runtime import target


class OpenAIProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.ids = {k: k + '-fixture' for k in ('source', 'ledger', 'workspace', 'node', 'executor', 'binding')}
        self.spec = {'schema_version': 2, 'ingress': 'openai', 'bridge_origin': 'https://bridge.example.com',
            'windows_data_dir': r'C:\Profiles\provider\control', 'bridge_data_dir': '/var/lib/native-bridge',
            'targets': [target(), target('codex.app-server.v1', 'codex-test')]}

    def test_provider_plan_needs_no_sub2_identity_and_matches_existing_node_bridge(self):
        plan = build_plan(self.spec, self.ids)
        self.assertEqual(set(plan), {'bridge.json', 'facade.json', 'windows-node.json',
                                    'openai-provider.json', 'deployment-identities.json'})
        facade, node, bridge = [plan[n] for n in ('facade.json', 'windows-node.json', 'bridge.json')]
        self.assertEqual(facade['owner'], ['provider', self.ids['source']])
        self.assertEqual(node['owner'], facade['owner'])
        self.assertEqual(bridge['harness']['allowed_owners'], [facade['owner']])
        self.assertEqual(facade['bindings'], bridge['harness']['bindings'])
        self.assertEqual([t['binding'] for t in node['targets']], facade['bindings'])
        self.assertEqual(facade['ingress'], 'openai'); self.assertEqual(facade['wait_seconds'], 180)
        self.assertNotIn('ingress', node)
        self.assertFalse(facade['accepting_new']); self.assertFalse(node['accepting_new'])
        self.assertFalse(bridge['harness']['accepting_new']); self.assertFalse(bridge['accepting_new_tasks'])
        hints = plan['openai-provider.json']
        self.assertEqual(hints['extra'], {'openai_responses_mode': 'force_chat_completions'})
        self.assertEqual(hints['api_key_source_env'], 'NATIVE_FACADE_TOKEN'); self.assertNotIn('api_key', hints)
        self.assertEqual(set(hints['model_mapping']), {b['model_alias'] for b in facade['bindings']})
        for name in ('bridge.json', 'facade.json', 'openai-provider.json'):
            text = json.dumps(plan[name])
            for private in ('profile_path', 'account_fingerprint', 'assets_root', 'a' * 64):
                self.assertNotIn(private, text)

    def test_old_v2_spec_keeps_native_ingress_and_dedicated_gateway_output(self):
        old = {k:v for k,v in self.spec.items() if k != 'ingress'}; old.update(user_id=7, api_key_id=9)
        plan = build_plan(old, self.ids)
        self.assertIn('sub2api.native-harness.yaml', plan); self.assertNotIn('openai-provider.json', plan)
        self.assertNotIn('ingress', plan['facade.json'])
        self.assertEqual(plan['facade.json']['owner'], ['user-7', 'key-9'])
        self.assertEqual(NativeFacadeConfig.from_value(plan['facade.json']).ingress, 'native')

    def test_invalid_provider_spec_and_bounded_wait(self):
        for change in ({'ingress':'native'}, {'ingress':None}, {'user_id':7}, {'schema_version':True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_plan(dict(self.spec, **change), self.ids)
        value = build_plan(self.spec, self.ids)['facade.json']
        self.assertEqual(NativeFacadeConfig.from_value(dict(value, wait_seconds=300)).wait_seconds, 300)
        for change in ({'ingress':'other'}, {'wait_seconds':301}, {'wait_seconds':True},
                       {'wait_seconds':float('nan')}, {'ingress':'native', 'wait_seconds':31}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                NativeFacadeConfig.from_value(dict(value, **change))

    def test_factory_uses_existing_environment_credentials_without_io(self):
        config = NativeFacadeConfig.from_value(build_plan(self.spec, self.ids)['facade.json'])
        module = ModuleType('native_harness_api.openai_provider'); module.OpenAIProvider = Mock(return_value='provider-fixture')
        credentials = {'NATIVE_BRIDGE_SOURCE_TOKEN':'source-fixture-token', 'NATIVE_FACADE_TOKEN':'provider-fixture-token'}
        with patch.dict(sys.modules, {'native_harness_api.openai_provider':module}):
            self.assertEqual(config.facade(credentials), 'provider-fixture')
        args = module.OpenAIProvider.call_args
        self.assertEqual(args.kwargs, {'wait_seconds':180, 'mode':'harness_local_trial'})
        self.assertEqual(set(args.args[0]), {'provider-fixture-token'})
        self.assertEqual(next(iter(args.args[0].values())).owner, ('provider','source-fixture'))
        with self.assertRaises(ValueError): config.facade({})
        with self.assertRaises(ValueError): config.facade({k:'same-fixture-token' for k in credentials})
        native = dict(build_plan(self.spec, self.ids)['facade.json'], ingress='native', wait_seconds=10)
        self.assertIsInstance(NativeFacadeConfig.from_value(native).facade(credentials), Facade)

    def test_cli_writes_new_plan_once_and_keeps_saved_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); spec = root/'spec.json'; ids = root/'ids.json'; out = root/'plan'
            spec.write_text(json.dumps(self.spec)); ids.write_text(json.dumps({'identities':self.ids}))
            args = ['--spec',str(spec),'--identities',str(ids),'--output',str(out)]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 0)
                original = (out/'deployment-identities.json').read_bytes()
                self.assertEqual(main(args), 1)
            self.assertEqual((out/'deployment-identities.json').read_bytes(), original)
            self.assertTrue((out/'openai-provider.json').is_file())
            self.assertFalse((out/'sub2api.native-harness.yaml').exists())


if __name__ == '__main__': unittest.main()
