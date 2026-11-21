"""Existing CLI wizard fixtures only: no account access or vendor execution."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / n) for n in ('packaging', 'tools')]
import quick_start as q

class CodexWizardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.profile = SimpleNamespace(root=self.root / 'profiles' / ('codex-user-cli-' + 'a'*32), state='fresh')
        self.context = dict(cli_path='C:/official/codex.exe', cli_sha256='a'*64, codex_home='C:/User/.codex')
        self.account = dict(type='chatgpt', email='fixture@example.invalid', workspaceName='Fixture')
        self.models = [dict(model='first', displayName='First', isDefault=False), dict(model='native-default-model', displayName='Second', isDefault=True)]
        self.cli = SimpleNamespace(PREFIX='codex-user-cli-', resolve_cli=Mock(return_value=self.context), verify_version=Mock(),
            create_profile=Mock(return_value=self.profile), open_profile=Mock(return_value=self.profile), read_context=Mock(return_value=self.context),
            discover=Mock(return_value=dict(account=self.account, models=self.models)), confirm_profile=Mock(), export_target=Mock(return_value={'target': 'fixture'}),
            close_profile=Mock(), hold_retained=Mock())
        for item in (patch.dict(sys.modules, {'codex_user_cli': self.cli}), patch.object(q, 'console_required'), patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO())):
            item.start(); self.addCleanup(item.stop)

    def setup(self, inputs):
        with patch('builtins.input', side_effect=inputs), patch.object(q, 'write_connection', return_value=0) as write:
            result = q.setup_codex(self.root)
        return result, write

    def test_new_connection_default_model_and_no_login(self):
        result, write = self.setup(['', '', 'yes', 'yes', '', ''])
        self.assertEqual(result, 0)
        self.cli.resolve_cli.assert_called_once_with(None)
        self.cli.verify_version.assert_called_once_with(self.context)
        self.cli.create_profile.assert_called_once_with(self.root / 'profiles', self.context)
        self.cli.discover.assert_called_once_with(self.profile, self.context)
        self.cli.confirm_profile.assert_called_once_with(self.profile, self.account)
        self.cli.export_target.assert_called_once_with(self.profile, self.context, self.models, 'native-default-model', 'codex-local')
        write.assert_called_once_with(self.root, self.profile.root, {'target': 'fixture'})
        self.cli.close_profile.assert_called_once_with(self.profile); self.cli.hold_retained.assert_called_once()

    def test_default_catalog_choice_is_a_concrete_binding_on_all_platforms(self):
        from native_harness_api.harness_registry import make_recipe, binding_for
        with patch('builtins.input', return_value=''):
            selected = q.choose_codex_model(self.models)
        recipe = make_recipe(adapter_id='codex.user-cli.v1',
            profile_path=r'C:\Profiles\sidecar', profile_id='sidecar', account_fingerprint='b'*64,
            native_model=selected, public_alias='codex-local', **self.context)
        self.assertEqual(binding_for(recipe).native_model, 'native-default-model')

    def test_user_can_change_model_and_choose_executable(self):
        result, _ = self.setup(['', 'C:/chosen/codex.exe', 'yes', 'yes', '1', 'codex-chosen'])
        self.assertEqual(result, 0); self.cli.resolve_cli.assert_called_once_with('C:/chosen/codex.exe')
        self.assertEqual(self.cli.export_target.call_args.args[-2:], ('first', 'codex-chosen'))

    def test_cancel_before_native_has_no_profile_or_discovery(self):
        with self.assertRaises(q.SetupError): self.setup(['', '', 'no'])
        self.cli.verify_version.assert_not_called(); self.cli.create_profile.assert_not_called()
        self.cli.discover.assert_not_called(); self.cli.export_target.assert_not_called()

    def test_incompatible_version_no_fallback_or_profile(self):
        self.cli.verify_version.side_effect = ValueError('fixture incompatibility')
        with self.assertRaises(ValueError): self.setup(['', '', 'yes'])
        self.cli.create_profile.assert_not_called(); self.cli.discover.assert_not_called()
        self.cli.export_target.assert_not_called(); self.cli.resolve_cli.assert_called_once()

    def test_rejected_identity_does_not_export(self):
        with self.assertRaises(q.SetupError): self.setup(['', '', 'yes', 'no'])
        self.cli.confirm_profile.assert_not_called(); self.cli.export_target.assert_not_called()
        self.cli.close_profile.assert_called_once_with(self.profile)

    def test_cancelled_account_can_reenter_same_clean_fresh_sidecar(self):
        with self.assertRaises(q.SetupError): self.setup(['', '', 'yes', 'no'])
        self.profile.root.mkdir(parents=True)
        result, write = self.setup(['', 'yes', 'yes', '', ''])
        self.assertEqual(result, 0); write.assert_called_once()
        self.cli.create_profile.assert_called_once()
        self.cli.open_profile.assert_called_once_with(self.profile.root)
        self.assertEqual(self.cli.discover.call_count, 2)
        self.cli.confirm_profile.assert_called_once_with(self.profile, self.account)

    def test_unverified_account_has_safe_manual_login_guidance_then_same_sidecar(self):
        self.cli.discover.side_effect = ValueError('CODEX_CONNECTION_NOT_VERIFIED')
        with self.assertRaisesRegex(q.SetupError, 'codex login status'):
            self.setup(['', '', 'yes'])
        self.cli.export_target.assert_not_called()
        self.profile.root.mkdir(parents=True)
        self.cli.discover.side_effect = None
        result, _ = self.setup(['', 'yes', 'yes', '', ''])
        self.assertEqual(result, 0); self.cli.create_profile.assert_called_once()
        self.cli.open_profile.assert_called_once_with(self.profile.root)

    def test_discovery_failure_no_export_and_owned_retention_handoff(self):
        self.cli.discover.side_effect = ValueError('fixture fault')
        with self.assertRaises(ValueError): self.setup(['', '', 'yes'])
        self.cli.export_target.assert_not_called(); self.cli.close_profile.assert_called_once(); self.cli.hold_retained.assert_called_once()

    def test_existing_sidecar_reused_without_new_identity(self):
        self.profile.root.mkdir(parents=True); self.profile.state = 'authenticated_confirmed'
        result, write = self.setup(['', 'yes', '', ''])
        self.assertEqual(result, 0); write.assert_called_once(); self.cli.open_profile.assert_called_once_with(self.profile.root)
        self.cli.resolve_cli.assert_not_called(); self.cli.create_profile.assert_not_called(); self.cli.confirm_profile.assert_not_called()

    def test_unknown_sidecar_or_journal_stops_before_native(self):
        self.profile.root.mkdir(parents=True); self.profile.state = 'execution_unknown'
        with self.assertRaises(q.SetupError): self.setup([''])
        self.profile.state = 'authenticated_confirmed'; (self.profile.root / 'control' / 'journal').mkdir(parents=True)
        with self.assertRaises(q.SetupError): self.setup([''])
        self.cli.verify_version.assert_not_called(); self.cli.discover.assert_not_called(); self.cli.create_profile.assert_not_called()

    def test_empty_directory_or_invalid_choice_no_export(self):
        self.cli.discover.return_value['models'] = []
        with self.assertRaises(q.SetupError): self.setup(['', '', 'yes', 'yes'])
        self.cli.export_target.assert_not_called()
        with patch('builtins.input', return_value='3'), self.assertRaises(q.SetupError): q.choose_codex_model(self.models)

    def test_ambiguous_default_requires_explicit_selection(self):
        choices = [dict(model='a', isDefault=True), dict(model='b', isDefault=True)]
        with patch('builtins.input', return_value='2'): self.assertEqual(q.choose_codex_model(choices), 'b')
        with patch('builtins.input', return_value=''), self.assertRaises(ValueError): q.choose_codex_model(choices)

    def test_saved_codex_node_skips_new_connection_and_uses_original(self):
        path = self.root / 'original.json'; path.write_bytes(b'{ "original": true }'); q.remember(self.root, path)
        config = SimpleNamespace(node_token_env='fixture')
        with patch.object(q, 'load_node', return_value=config), patch.object(q, 'start_node', return_value=0) as run, patch.object(q, 'setup_codex') as setup:
            self.assertEqual(q.main(self.root, [], harness='codex'), 0)
        setup.assert_not_called(); self.cli.discover.assert_not_called(); run.assert_called_once_with(path, config)
        self.assertEqual(path.read_bytes(), b'{ "original": true }')

    def test_main_new_codex_dispatches_only_codex_setup(self):
        with patch('builtins.input', return_value=''), patch.object(q, 'setup_codex', return_value=0) as codex, patch.object(q, 'setup') as workbuddy:
            self.assertEqual(q.main(self.root, [], harness='codex'), 0)
        codex.assert_called_once_with(self.root); workbuddy.assert_not_called()

    def test_codex_help_no_console_or_native(self):
        with patch.object(q, 'console_required') as console, self.assertRaises(SystemExit) as result: q.main(self.root, ['--help'], harness='codex')
        self.assertEqual(result.exception.code, 0); console.assert_not_called(); self.cli.resolve_cli.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'real Windows existing CLI config round trip')
    def test_windows_codex_setup_real_recipe_and_matching_server_config(self):
        from native_harness_api.harness_registry import make_recipe, binding_for
        from dataclasses import asdict
        context = dict(cli_path=str(self.root / 'official' / 'codex.exe'), cli_sha256='a'*64,
                       codex_home=str(self.root / 'existing-home'))
        self.cli.resolve_cli.return_value = context
        def export(profile, context, models, native_model, public_alias):
            recipe = make_recipe(adapter_id='codex.user-cli.v1', profile_path=str(profile.root),
                profile_id=profile.root.name, account_fingerprint='b'*64, native_model=native_model,
                public_alias=public_alias, **context)
            return dict(recipe=recipe, binding=asdict(binding_for(recipe)), assets_root=str(Path(context['cli_path']).parent))
        self.cli.export_target.side_effect = export
        with patch('builtins.input', side_effect=['', '', 'yes', 'yes', '', '', 'https://bridge.example.com', '/var/lib/native-bridge']), patch.object(q, 'start_node') as start:
            self.assertEqual(q.setup_codex(self.root), 0)
        start.assert_not_called()
        path = self.root / 'connection' / 'windows-node.json'
        config = q.load_node(path)
        self.assertFalse(config.accepting_new)
        self.assertEqual(config.data_dir, self.profile.root / 'control')
        self.assertEqual(config.targets[0].recipe['adapter_id'], 'codex.user-cli.v1')
        node = json.loads(path.read_text(encoding='utf-8'))
        bridge = json.loads((path.parent / 'bridge.json').read_text(encoding='utf-8'))
        self.assertEqual(node['targets'][0]['binding'], bridge['harness']['bindings'][0])
        self.assertEqual(json.loads((self.root / 'launcher.json').read_text(encoding='utf-8'))['config'], str(path))
