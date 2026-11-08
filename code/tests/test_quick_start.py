"""Launcher fixtures: no vendor executable, authentication or inference."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / n) for n in ('packaging', 'tools')]
import quick_start as q
import public_harness_login as login
import public_harness_models as models
import public_harness_login_profile as storage
import public_harness_runtime_assets as assets
import prepare_native_deployment as deployment

class QuickStartTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for item in (patch.object(q, 'console_required'), patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO())):
            item.start(); self.addCleanup(item.stop)
        self.config = SimpleNamespace(node_token_env='QUICK_START_FIXTURE_TOKEN')

    def profile(self, state='authenticated_confirmed'):
        path = self.root / 'profiles' / (storage.PREFIX + 'a' * 32); path.mkdir(parents=True)
        closed = []
        return SimpleNamespace(root=path, state=state, expected_account_digest='a'*64, close=lambda: closed.append(True), closed=closed)

    def test_saved_node_restores_without_login_or_catalog(self):
        path = self.root / 'existing.json'; path.write_text('{}'); q.remember(self.root, path)
        with patch.object(q, 'load_node', return_value=self.config), patch.object(q, 'start_node', return_value=0) as run, patch.object(q, 'setup') as setup, patch.object(q, 'discover_target') as discover:
            self.assertEqual(q.main(self.root, []), 0)
        run.assert_called_once_with(path, self.config); setup.assert_not_called(); discover.assert_not_called()

    def test_existing_selected_config_not_rewritten(self):
        path = self.root / 'original.json'; path.write_bytes(b'{ "keep": "identity-and-data-dir" }'); before = path.read_bytes()
        with patch('builtins.input', return_value=str(path)), patch.object(q, 'load_node', return_value=self.config), patch.object(q, 'start_node', return_value=0):
            self.assertEqual(q.main(self.root, []), 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads((self.root / 'launcher.json').read_text())['config'], str(path))

    def test_explicit_config_reselects_pointer(self):
        q.remember(self.root, self.root / 'old.json'); new = self.root / 'chosen.json'
        with patch.object(q, 'load_node', return_value=self.config), patch.object(q, 'start_node', return_value=0):
            self.assertEqual(q.main(self.root, ['--config', str(new)]), 0)
        self.assertEqual(json.loads((self.root / 'launcher.json').read_text())['config'], str(new))

    def test_bad_saved_pointer_does_not_start_new_setup(self):
        (self.root / 'launcher.json').write_text('{')
        with patch.object(q, 'setup') as setup: self.assertEqual(q.main(self.root, []), 1)
        setup.assert_not_called()

    def test_interrupted_plan_reuses_config_not_new_identities(self):
        folder = self.root / 'connection'; folder.mkdir()
        for name in ('windows-node.json', 'bridge.json', 'facade.json', 'sub2api.native-harness.yaml', 'deployment-identities.json'): (folder / name).write_text('{}')
        with patch.object(q, 'load_node', return_value=self.config), patch.object(q, 'start_node', return_value=0) as run, patch.object(q, 'setup') as setup:
            self.assertEqual(q.main(self.root, []), 0)
        setup.assert_not_called(); self.assertEqual(run.call_args.args[0], folder / 'windows-node.json')

    def test_partial_plan_never_regenerates(self):
        (self.root / 'connection').mkdir()
        with patch.object(q, 'setup') as setup, patch.object(q, 'start_node') as run: self.assertEqual(q.main(self.root, []), 1)
        setup.assert_not_called(); run.assert_not_called()

    def test_incomplete_profile_is_not_filtered_into_new_login(self):
        profile = self.profile('unknown_retained')
        with patch('builtins.input', return_value=''), patch.object(login, 'main') as sign_in: self.assertEqual(q.choose_profile(self.root), profile.root)
        sign_in.assert_not_called()

    def test_multiple_profiles_require_choice(self):
        a = self.profile().root; b = a.with_name(storage.PREFIX + 'b'*32); b.mkdir()
        with patch('builtins.input', side_effect=['', '2']), patch.object(login, 'main') as sign_in: self.assertEqual(q.choose_profile(self.root), b)
        sign_in.assert_not_called()

    def test_first_login_once_and_cancel_no_retry(self):
        def sign_in(_args): self.profile(); return 0
        with patch('builtins.input', return_value=''), patch.object(login, 'main', side_effect=sign_in) as run:
            self.assertTrue(q.choose_profile(self.root).name.startswith(storage.PREFIX))
        run.assert_called_once(); other = self.root / 'other'; other.mkdir()
        with patch('builtins.input', return_value=''), patch.object(login, 'main', return_value=1) as run, self.assertRaises(q.SetupError): q.choose_profile(other)
        run.assert_called_once()

    def test_unknown_or_executed_profile_stops_before_catalog(self):
        profile = self.profile('unknown_retained')
        with patch.object(assets, 'verify_runtime_assets', return_value={}), patch.object(storage, 'open_profile', return_value=profile), patch.object(models, 'discover_models') as discover:
            with self.assertRaises(q.SetupError): q.discover_target(self.root, profile.root)
            profile.state = 'authenticated_confirmed'; (profile.root / 'control' / 'journal').mkdir(parents=True)
            with self.assertRaises(q.SetupError): q.discover_target(self.root, profile.root)
        discover.assert_not_called(); self.assertEqual(len(profile.closed), 2)

    def test_catalog_once_frozen_identity_export_and_retained_handoff(self):
        profile = self.profile(); result = {'success': True, 'catalog': {'models': [{'id': 'native-a', 'name': 'Model A'}]}}
        def discover(*_): profile.expected_account_digest = 'b'*64; return result
        with patch.object(assets, 'verify_runtime_assets', return_value={}), patch.object(storage, 'open_profile', return_value=profile), patch.object(models, 'discover_models', side_effect=discover) as read, patch.object(models, 'export_target', return_value={'target': 1}) as export, patch.object(models, 'hold_retained') as hold, patch('builtins.input', side_effect=['1', '']):
            self.assertEqual(q.discover_target(self.root, profile.root), {'target': 1})
        read.assert_called_once(); hold.assert_called_once()
        self.assertEqual(export.call_args.kwargs['account_fingerprint'], 'a'*64); self.assertEqual(export.call_args.kwargs['native_model'], 'native-a')

    def test_catalog_failure_retains_owner(self):
        profile = self.profile()
        with patch.object(assets, 'verify_runtime_assets', return_value={}), patch.object(storage, 'open_profile', return_value=profile), patch.object(models, 'discover_models', return_value={'success': False}), patch.object(models, 'hold_retained') as hold:
            with self.assertRaises(q.SetupError): q.discover_target(self.root, profile.root)
        hold.assert_called_once(); self.assertEqual(profile.closed, [])

    def test_new_setup_writes_once_never_starts_node(self):
        profile = self.root / 'profile'
        def write(output, plan): output.mkdir(); (output / 'windows-node.json').write_text('{}')
        with patch.object(q, 'choose_profile', return_value=profile), patch.object(q, 'discover_target', return_value={'target': 1}) as discover, patch.object(deployment, 'build_plan', return_value={'plan': 1}) as build, patch.object(deployment, 'write_plan', side_effect=write) as write_plan, patch.object(q, 'load_node', return_value=self.config), patch.object(q, 'start_node') as run, patch('builtins.input', side_effect=['', 'https://bridge.example', '7', '9', '/var/lib/bridge']): self.assertEqual(q.main(self.root, []), 0)
        discover.assert_called_once(); build.assert_called_once(); write_plan.assert_called_once(); run.assert_not_called()
        spec = build.call_args.args[0]; self.assertEqual(spec['windows_data_dir'], str(profile / 'control')); self.assertEqual(spec['targets'], [{'target': 1}])

    def test_prompted_token_process_only_removed_after_failure(self):
        secret = 'fixture-not-real-secret'
        def runtime(args): self.assertEqual(os.environ[self.config.node_token_env], secret); raise RuntimeError('fixture')
        with patch.dict(os.environ, {}, clear=True), patch.object(q.getpass, 'getpass', return_value=secret), patch('native_harness_api.runtime.main', side_effect=runtime):
            with self.assertRaises(RuntimeError): q.start_node(self.root / 'node.json', self.config)
            self.assertNotIn(self.config.node_token_env, os.environ)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_token_no_prompt_no_mutation(self):
        with patch.dict(os.environ, {self.config.node_token_env: 'fixture'}), patch.object(q.getpass, 'getpass') as prompt, patch('native_harness_api.runtime.main', return_value=0):
            self.assertEqual(q.start_node(self.root / 'node.json', self.config), 0); self.assertEqual(os.environ[self.config.node_token_env], 'fixture')
        prompt.assert_not_called()

    def test_wrong_config_kind_rejected(self):
        with patch('native_harness_api.runtime.RuntimeConfig.load', return_value=object()), self.assertRaises(q.SetupError): q.load_node(self.root / 'facade.json')

    def test_help_has_no_console_or_native_side_effect(self):
        with patch.object(q, 'console_required') as console, self.assertRaises(SystemExit) as out: q.main(self.root, ['--help'])
        self.assertEqual(out.exception.code, 0); console.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'real Windows path/config round trip')
    def test_windows_setup_real_recipe_and_config_round_trip(self):
        profile = self.profile()
        result = {'success': True, 'cleanup_confirmed': True, 'profile_restored': True,
                  'profile_closed': True, 'catalog': {'models': [{'id': 'native-fixture', 'name': 'Fixture'}]}}
        with patch.object(assets, 'verify_runtime_assets', return_value={}), patch.object(storage, 'open_profile', return_value=profile), patch.object(models, 'discover_models', return_value=result), patch.object(q, 'start_node') as run, patch('builtins.input', side_effect=['', str(profile.root), '1', '', 'https://bridge.example.com', '7', '9', '/var/lib/native-bridge']):
            self.assertEqual(q.main(self.root, []), 0)
        run.assert_not_called()
        path = self.root / 'connection' / 'windows-node.json'
        config = q.load_node(path)
        self.assertFalse(config.accepting_new)
        self.assertEqual(config.data_dir, profile.root / 'control')
        self.assertEqual(config.targets[0].recipe['native_model'], 'native-fixture')
        node = json.loads(path.read_text(encoding='utf-8'))
        bridge = json.loads((path.parent / 'bridge.json').read_text(encoding='utf-8'))
        self.assertEqual(node['targets'][0]['binding'], bridge['harness']['bindings'][0])
