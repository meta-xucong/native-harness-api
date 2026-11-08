"""No vendor launch, authentication or network. Explicit synthetic protocol evidence."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from codex_control_protocol import CodexControlProtocol
from native_harness_api.codex_session import account_identity_digest
import codex_profile
import public_harness_login_profile as storage
import test_public_harness_login_profile as profile_fixtures

ACCOUNT = {'type': 'chatgpt', 'email': 'test@example.invalid', 'workspaceName': 'Fixture', 'planType': 'plus'}
INIT = dict(userAgent='fixture', codexHome='fixture', platformFamily='windows', platformOs='windows')


def response(p, frame, result):
    request = json.loads(frame)
    return p.receive(json.dumps({'id': request['id'], 'result': result}).encode())


def notify(p, method, params):
    return p.receive(json.dumps({'method': method, 'params': params}).encode())


class ControlTests(unittest.TestCase):
    def ready(self, mode='login', device=None):
        p = CodexControlProtocol(deadline=100, clock=lambda: 1, mode=mode,
            expected_account_digest=account_identity_digest(ACCOUNT) if mode == 'models' else None, on_device=device)
        frames = response(p, p.start()[0], INIT)
        self.assertEqual(json.loads(frames[0])['method'], 'initialized')
        return p, frames[1]

    def login(self, early=False):
        devices = []
        p, read = self.ready(device=lambda url, code: devices.append((url, code)))
        login = response(p, read, {'account': None})[0]
        self.assertEqual(json.loads(login)['params'], {'type': 'chatgptDeviceCode'})
        completion = dict(loginId='fixture', success=True)
        if early:
            self.assertEqual(notify(p, 'account/login/completed', completion), ())
        frames = response(p, login, dict(type='chatgptDeviceCode', loginId='fixture', verificationUrl='https://auth.openai.com/codex/device', userCode='fixture'))
        if not early:
            frames = notify(p, 'account/login/completed', completion)
        response(p, frames[0], {'account': ACCOUNT})
        self.assertTrue(p.terminal_observed)
        self.assertIsNone(p.fault)
        self.assertEqual(p.evidence()['account']['email'], ACCOUNT['email'])
        self.assertNotIn('userCode', json.dumps(p.evidence()))
        self.assertEqual(len(devices), 1)
        return p

    def test_login_and_early_completion(self):
        self.login()
        self.login(True)

    def test_new_profile_must_not_import_account(self):
        p, read = self.ready()
        self.assertEqual(response(p, read, {'account': ACCOUNT}), ())
        self.assertIsNotNone(p.fault)
        self.assertFalse(p.terminal_observed)

    def test_wrong_login_completion(self):
        p, read = self.ready()
        login = response(p, read, {'account': None})[0]
        response(p, login, dict(type='chatgptDeviceCode', loginId='one', verificationUrl='url', userCode='code'))
        notify(p, 'account/login/completed', dict(loginId='two', success=True))
        self.assertIsNotNone(p.fault)

    def test_models_paginate_native_model_not_display_id(self):
        p, read = self.ready('models')
        listing = response(p, read, {'account': ACCOUNT})[0]
        model = dict(id='display-id', model='native-model', displayName='Name')
        next_page = response(p, listing, {'data': [model], 'nextCursor': 'two'})[0]
        self.assertEqual(json.loads(next_page)['params']['cursor'], 'two')
        response(p, next_page, {'data': [], 'nextCursor': None})
        self.assertTrue(p.terminal_observed)
        self.assertEqual(p.models[0]['model'], 'native-model')

    def test_catalog_wrong_account_and_api_key_rejected(self):
        for account in ({**ACCOUNT, 'email': 'other@example.invalid'}, {'type': 'apiKey'}):
            p, read = self.ready('models')
            response(p, read, {'account': account})
            self.assertIsNotNone(p.fault)
            self.assertFalse(p.terminal_observed)

    def test_plan_refresh_does_not_change_identity(self):
        self.assertEqual(account_identity_digest(ACCOUNT), account_identity_digest({**ACCOUNT, 'planType': 'pro'}))

    def test_timeout_never_success(self):
        p, _ = self.ready()
        p.deadline = 0
        self.assertFalse(p.tick())
        self.assertFalse(p.terminal_observed)


class CodexProfileTests(unittest.TestCase):
    setUp = profile_fixtures.ProfileFixtureTests.setUp
    def test_separate_policy_uses_same_storage_and_lock(self):
        with codex_profile.create_profile(self.parent) as p:
            root = p.root
            self.assertTrue(root.name.startswith('codex-managed-'))
            p.record('login_started')
            p.record('authenticated_confirmed', account_identity_digest(ACCOUNT))
            p.begin_execution()
            p.finish_execution(cleanup_confirmed=True)
        with self.assertRaises(storage.ProfileError):
            storage.open_profile(root)
        with codex_profile.open_profile(root) as p:
            self.assertEqual(p.state, 'authenticated_confirmed')
            self.assertEqual(p.policy, codex_profile.POLICY)


class ControlRunnerTests(unittest.TestCase):
    setUp = profile_fixtures.ProfileFixtureTests.setUp

    def invoke(self, profile, mode, result):
        import codex_login
        class Owner:
            def run(self, command, cwd, env, **kwargs):
                self.command, self.kwargs = command, kwargs
                return result
        owner = Owner()
        with patch.object(codex_login, 'environment', return_value={}):
            value = codex_login.run_control(profile, Path('fixture/bin/codex-app-server.exe'),
                        mode=mode, owner_factory=lambda: owner)
        self.assertEqual(owner.command[-2:], ['--listen', 'stdio://'])
        return value

    def result(self, *, cleanup=True, fault=None):
        return {'cleanup_confirmed': cleanup, 'fault': fault, 'session': {
            'terminal_observed': True, 'outcome': 'succeeded', 'fault': None,
            'account': ACCOUNT, 'models': []}}

    def test_login_cleanup_does_not_authorize_identity_without_user(self):
        with codex_profile.create_profile(self.parent) as profile:
            self.invoke(profile, 'login', self.result())
            self.assertEqual(profile.state, 'login_started')
            self.assertIsNone(profile.expected_account_digest)

    def test_model_directory_releases_only_its_execution_gate(self):
        with codex_profile.create_profile(self.parent) as profile:
            profile.record('login_started')
            profile.record('authenticated_confirmed', account_identity_digest(ACCOUNT))
            self.invoke(profile, 'models', self.result())
            self.assertEqual(profile.state, 'authenticated_confirmed')

    def test_model_protocol_fault_quarantines_identity(self):
        with codex_profile.create_profile(self.parent) as profile:
            profile.record('login_started')
            profile.record('authenticated_confirmed', account_identity_digest(ACCOUNT))
            with self.assertRaises(ValueError):
                self.invoke(profile, 'models', self.result(fault='protocol'))
            self.assertEqual(profile.state, 'execution_unknown')
            self.assertIsNone(profile.expected_account_digest)

    def test_unknown_cleanup_retains_lock_and_never_authenticates(self):
        import codex_login
        profile = codex_profile.create_profile(self.parent)
        self.addCleanup(profile.close)
        self.addCleanup(codex_login.RETAINED.clear)
        with self.assertRaises(ValueError):
            self.invoke(profile, 'login', self.result(cleanup=False))
        self.assertEqual(profile.state, 'unknown_retained')
        self.assertFalse(profile._closed)
        self.assertIs(codex_login.RETAINED[-1][1], profile)


class CodexConsentTests(unittest.TestCase):
    def test_cached_yes_cannot_confirm_later_account(self):
        import codex_login
        import public_harness_login
        import contextlib
        import io
        class Console:
            queued = list('yes\r')
            def kbhit(self): return bool(self.queued)
            def getwch(self): return self.queued.pop(0)
        ui = public_harness_login.WindowsTTY.__new__(public_harness_login.WindowsTTY)
        ui.console, ui.pending, ui.buffer = Console(), False, ''
        with patch.object(codex_login.time, 'monotonic', side_effect=[0, 0, 121]), patch.object(codex_login.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(codex_login.confirm(ui, 'Confirm intended account?'))
        self.assertEqual(ui.console.queued, [])
        self.assertFalse(ui.pending)

    def test_target_uses_frozen_recipe_and_native_model(self):
        import codex_login
        from pathlib import PureWindowsPath
        from types import SimpleNamespace
        from native_harness_api.harness_registry import ExecutionTarget
        profile = SimpleNamespace(root=PureWindowsPath('C:/profiles/codex-managed-' + 'a' * 32),
                                  expected_account_digest=account_identity_digest(ACCOUNT))
        target = codex_login.export_target(profile, PureWindowsPath('C:/bundle/assets'),
                 [{'model': 'native-model', 'id': 'wrong-id'}], 'native-model', 'codex-fixture')
        parsed = ExecutionTarget.from_value(target)
        self.assertEqual(parsed.binding.native_model, 'native-model')
        with self.assertRaises(ValueError):
            codex_login.export_target(profile, PureWindowsPath('C:/bundle/assets'),
                     [{'model': 'native-model', 'id': 'wrong-id'}], 'wrong-id', 'codex-fixture')


if __name__ == '__main__':
    unittest.main()
