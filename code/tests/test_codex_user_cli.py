"""Synthetic-only existing CLI reuse contracts. No vendor process or account."""
import json
import ntpath
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import codex_user_cli as cli
from codex_control_protocol import CodexControlProtocol
from native_harness_api.codex_session import CodexSession, account_identity_digest
from native_harness_api.harness_registry import make_recipe, binding_for, normalize_recipe
from native_harness_api import codex_trial_runtime as runtime
import test_public_harness_login_profile as profile_fixtures
from test_codex_control import ACCOUNT, response
from test_native_trial_runtime import Profile


class ExistingCliTests(unittest.TestCase):
    def setUp(self):
        profile_fixtures.ProfileFixtureTests.setUp(self)
        self.exe = self.parent / 'codex.exe'
        self.exe.write_bytes(b'not executable; self-authored fixture')
        self.home = self.parent / 'existing-home'; self.home.mkdir()
        (self.home / 'auth.json').write_text('opaque synthetic credential fixture')
        self.env = {'CODEX_HOME': str(self.home), 'HOME': 'preserved-home', 'USERPROFILE': 'preserved-user', 'APPDATA': 'preserved-appdata', 'PATH': 'preserved-path'}
        self.context = cli.resolve_cli(self.exe, self.env)
        self.addCleanup(cli.RETAINED.clear)

    def test_direct_and_path_lookup_do_not_execute(self):
        with patch.object(cli.subprocess, 'run') as run, patch.object(cli.shutil, 'which', return_value=str(self.exe)):
            self.assertEqual(cli.resolve_cli(environ=self.env), self.context)
            run.assert_not_called()
        self.assertEqual(cli.environment(self.context, self.env), self.env)

    def test_default_home_does_not_create_directory(self):
        user = self.parent / 'user'; user.mkdir(); home = user / '.codex'; home.mkdir()
        context = cli.resolve_cli(self.exe, {'USERPROFILE': str(user)})
        self.assertEqual(context['codex_home'], str(home))
        home.rmdir()
        with self.assertRaisesRegex(ValueError, 'EXISTING_CODEX_HOME_REQUIRED'):
            cli.resolve_cli(self.exe, {'USERPROFILE': str(user)})
        self.assertFalse(home.exists())

    def test_official_npm_platform_layout_not_shell(self):
        shim = self.parent / 'codex.cmd'; shim.write_text('must not execute or parse')
        exe = self.parent / 'node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        exe.parent.mkdir(parents=True); exe.write_bytes(b'fixture exe')
        self.assertEqual(cli.resolve_cli(shim, self.env)['cli_path'], str(exe))
        exe.unlink()
        with self.assertRaises(ValueError): cli.resolve_cli(shim, self.env)

    def test_missing_cli_and_wrong_filename(self):
        with patch.object(cli.shutil, 'which', return_value=None), self.assertRaises(ValueError):
            cli.resolve_cli(environ=self.env)
        with self.assertRaises(ValueError): cli.resolve_cli(self.home / 'auth.json', self.env)

    def test_version_only_no_login_or_fallback(self):
        with patch.object(cli.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'codex-cli 0.167.0\n')) as run:
            cli.verify_version(self.context)
            self.assertEqual(run.call_args.args[0], [str(self.exe), '--version'])
            self.assertEqual(run.call_args.kwargs['timeout'], 10)
            self.assertNotIn('shell', run.call_args.kwargs)
        for result in (SimpleNamespace(returncode=0, stdout=b'codex-cli 0.168.0'), SimpleNamespace(returncode=1, stdout=b'raw error')):
            with patch.object(cli.subprocess, 'run', return_value=result), self.assertRaisesRegex(ValueError, 'CODEX_VERSION_UNSUPPORTED'):
                cli.verify_version(self.context)
        with patch.object(cli.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 10)), self.assertRaises(subprocess.TimeoutExpired):
            cli.verify_version(self.context)

    def test_sidecar_never_reads_or_changes_existing_auth(self):
        original = Path.open; opened = []
        def recording(path, *a, **kw):
            opened.append(path)
            if path == self.home / 'auth.json': raise AssertionError('credential access')
            return original(path, *a, **kw)
        with patch.object(Path, 'open', recording):
            with cli.create_profile(self.parent, self.context) as p:
                self.assertEqual(cli.read_context(p), self.context)
                self.assertNotEqual(p.root, self.home)
                root = p.root
            with cli.open_profile(root) as p:
                self.assertEqual(cli.read_context(p), self.context)
        self.assertNotIn(self.home / 'auth.json', opened)
        self.assertEqual(set(p.name for p in self.home.iterdir()), {'auth.json'})
        with self.assertRaises(ValueError): cli.create_profile(self.home, self.context)

    def test_changed_executable_rejected(self):
        with cli.create_profile(self.parent, self.context) as p:
            self.exe.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'CODEX_CONTEXT_CHANGED'): cli.read_context(p)

    @staticmethod
    def result(clean=True, fault=None):
        return {'cleanup_confirmed': clean, 'fault': fault, 'session': {'fault': None,
                'terminal_observed': True, 'outcome': 'succeeded', 'account': ACCOUNT, 'models': []}}

    def test_discovery_keeps_home_and_requires_confirmation(self):
        owner = Mock(); owner.run.return_value = self.result()
        with cli.create_profile(self.parent, self.context) as p, patch.dict(cli.os.environ, self.env, clear=True):
            result = cli.discover(p, self.context, owner_factory=lambda: owner)
            self.assertEqual(p.state, 'fresh')
            self.assertIsNone(p.expected_account_digest)
            self.assertEqual(owner.run.call_args.args[0], [str(self.exe), 'app-server', '--listen', 'stdio://'])
            self.assertEqual(owner.run.call_args.args[2], self.env)
            cli.confirm_profile(p, result['account'])
            self.assertEqual(p.state, 'authenticated_confirmed')
            cli.discover(p, self.context, owner_factory=lambda: owner)
            self.assertEqual(p.state, 'authenticated_confirmed')

    def test_failed_discovery_never_confirms_and_uncertain_owner_retained(self):
        owner = Mock(); owner.run.return_value = self.result(clean=False)
        p = cli.create_profile(self.parent, self.context)
        with self.assertRaises(ValueError): cli.discover(p, self.context, owner_factory=lambda: owner)
        self.assertEqual(cli.RETAINED, [(owner, p)])
        cli.close_profile(p)
        self.assertFalse(p._closed)
        self.assertEqual(p.state, 'unknown_retained')
        self.assertIsNone(p.expected_account_digest)
        cli.RETAINED.clear(); p.close()

    def test_clean_failed_protocol_is_not_success(self):
        owner = Mock(); owner.run.return_value = self.result(fault='fixture')
        p = cli.create_profile(self.parent, self.context)
        with self.assertRaises(ValueError): cli.discover(p, self.context, owner_factory=lambda: owner)
        self.assertFalse(cli.RETAINED)
        cli.close_profile(p); self.assertTrue(p._closed)


    def test_not_logged_in_then_user_login_can_retry_same_sidecar(self):
        owner = Mock(); owner.run.return_value = self.result(fault='control_protocol')
        with cli.create_profile(self.parent, self.context) as p:
            root = p.root
            with self.assertRaises(ValueError): cli.discover(p, self.context, owner_factory=lambda: owner)
            self.assertEqual(p.state, 'fresh')
        with cli.open_profile(root) as p:
            owner.run.return_value = self.result()
            result = cli.discover(p, self.context, owner_factory=lambda: owner)
            cli.confirm_profile(p, result['account'])
            self.assertEqual(p.state, 'authenticated_confirmed')
        self.assertEqual(owner.run.call_count, 2)

    def test_cancel_identity_confirmation_then_reenter_same_sidecar(self):
        owner = Mock(); owner.run.return_value = self.result()
        with cli.create_profile(self.parent, self.context) as p:
            root = p.root
            cli.discover(p, self.context, owner_factory=lambda: owner)
            self.assertEqual(p.state, 'fresh')
        with cli.open_profile(root) as p:
            result = cli.discover(p, self.context, owner_factory=lambda: owner)
            cli.confirm_profile(p, result['account'])
            self.assertEqual(p.state, 'authenticated_confirmed')

    def test_existing_account_observation_failure_does_not_erase_confirmation(self):
        owner = Mock(); owner.run.return_value = self.result()
        with cli.create_profile(self.parent, self.context) as p:
            result = cli.discover(p, self.context, owner_factory=lambda: owner)
            cli.confirm_profile(p, result['account'])
            digest = p.expected_account_digest
            owner.run.return_value = self.result(fault='control_protocol')
            with self.assertRaises(ValueError): cli.discover(p, self.context, owner_factory=lambda: owner)
            self.assertEqual(p.state, 'authenticated_confirmed')
            self.assertEqual(p.expected_account_digest, digest)

    def test_owner_construction_failure_never_records_spawn_intent(self):
        with cli.create_profile(self.parent, self.context) as p:
            with self.assertRaises(RuntimeError):
                cli.discover(p, self.context, owner_factory=Mock(side_effect=RuntimeError('fixture')))
            self.assertEqual(p.state, 'fresh')


class ExistingCliProtocolTests(unittest.TestCase):
    def initialize(self, protocol, home):
        return response(protocol, protocol.start()[0], dict(userAgent='fixture', codexHome=home, platformFamily='windows', platformOs='windows'))

    def test_connect_lists_without_login_or_turn(self):
        p = CodexControlProtocol(deadline=10, clock=lambda: 1, expected_account_digest=None,
                                 mode='connect', expected_codex_home=r'C:\Users\Owner\.codex')
        frames = self.initialize(p, r'c:\users\owner\.codex')
        self.assertEqual(json.loads(frames[1])['method'], 'account/read')
        listing = response(p, frames[1], {'account': ACCOUNT})
        self.assertEqual(json.loads(listing[0])['method'], 'model/list')
        response(p, listing[0], {'data': [{'model': 'fixture', 'isDefault': True}], 'nextCursor': None})
        self.assertTrue(p.terminal_observed)
        self.assertEqual(p.account['email'], ACCOUNT['email'])

    def test_connect_missing_account_never_authenticates(self):
        p = CodexControlProtocol(deadline=10, clock=lambda: 1, expected_account_digest=None, mode='connect')
        frames = self.initialize(p, 'fixture')
        self.assertEqual(response(p, frames[1], {'account': None}), ())
        self.assertIsNotNone(p.fault); self.assertFalse(p.terminal_observed)

    def test_home_mismatch_blocks_discovery_and_every_new_execution(self):
        for cls in (CodexSession, CodexControlProtocol):
            kwargs = {'mode': 'connect'} if cls is CodexControlProtocol else {}
            p = cls(deadline=10, clock=lambda: 1, expected_account_digest=account_identity_digest(ACCOUNT),
                    expected_codex_home=r'C:\Original', **kwargs)
            self.assertEqual(self.initialize(p, r'C:\Different'), ())
            self.assertIsNotNone(p.fault)
            if cls is CodexSession:
                self.assertEqual(p.new_session('workspace', 'model'), ())
                self.assertFalse(p.ready)

    def test_legacy_does_not_add_home_constraint(self):
        p = CodexSession(deadline=10, clock=lambda: 1, expected_account_digest=account_identity_digest(ACCOUNT))
        self.assertTrue(self.initialize(p, 'legacy-fixture'))


class ExistingCliRecipeTests(unittest.TestCase):
    def recipe(self, adapter='codex.user-cli.v1', **extra):
        args = dict(adapter_id=adapter, profile_path=r'C:\Profiles\sidecar', profile_id='sidecar',
                    account_fingerprint='a'*64, native_model='model', public_alias='codex-test')
        if adapter == 'codex.user-cli.v1':
            args.update(cli_path=r'C:\Codex\codex.exe', cli_sha256='b'*64, codex_home=r'C:\Users\Owner\.codex')
        args.update(extra)
        return make_recipe(**args)

    def test_each_context_field_changes_binding_and_old_shape_stays(self):
        base = self.recipe()
        for key, value in dict(cli_path=r'C:\Other\codex.exe', cli_sha256='c'*64, codex_home=r'C:\OtherHome').items():
            self.assertNotEqual(binding_for(base), binding_for(self.recipe(**{key: value})))
        legacy = self.recipe('codex.app-server.v1')
        self.assertEqual(len(legacy), 12)
        self.assertNotIn('codex_home', legacy)
        with self.assertRaises(ValueError): normalize_recipe(dict(legacy, codex_home=r'C:\Other'))

    def test_cli_shape_and_digest_required(self):
        for extra in ({'cli_path': r'C:\bin\codex.cmd'}, {'cli_sha256': 'invalid'}, {'codex_home': 'relative'}):
            with self.assertRaises(ValueError): self.recipe(**extra)

    def test_new_runtime_uses_frozen_cli_and_existing_protocol(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'workspace').mkdir()
            p = Profile(root)
            recipe = self.recipe(profile_path=r'C:\Profiles\sidecar', profile_id=root.name)
            context = {k: recipe[k] for k in cli.CONTEXT_FIELDS}
            target = SimpleNamespace(recipe=recipe, assets_root=r'C:\Codex')
            with patch.object(cli, 'open_profile', return_value=p), patch.object(cli, 'read_context', return_value=context), \
                    patch.object(cli, 'environment', return_value={'CODEX_HOME': context['codex_home']}), \
                    patch.object(runtime, 'CodexOwnedSession') as owner, patch.object(runtime, 'SupervisedACPTransport'):
                owner.return_value.run.return_value = {'cleanup_confirmed': True}
                runtime.invoke(target, execution_timeout=10, cleanup_timeout=2, model_id='model', messages=[{'role': 'user', 'content': 'fixture'}])
                self.assertEqual(owner.return_value.run.call_args.args[0], [context['cli_path'], 'app-server', '--listen', 'stdio://'])
                self.assertEqual(owner.call_args.kwargs['protocol_factory'].keywords['expected_codex_home'], context['codex_home'])
                self.assertEqual(p.calls, ['verify', 'begin', 'complete', 'close'])
