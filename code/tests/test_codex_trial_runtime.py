import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from native_harness_api import codex_trial_runtime as runtime
from test_native_trial_runtime import Profile


class CodexRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'workspace').mkdir()
        self.profile = Profile(self.root)
        self.target = SimpleNamespace(recipe=dict(profile_path=str(self.root),
            profile_id=self.root.name, account_fingerprint='a'*64, native_model='model'),
            assets_root=self.root/'assets')

    def tearDown(self):
        runtime._QUARANTINED_PROFILES.clear()
        runtime._QUARANTINED_ENGINES.clear()
        self.tmp.cleanup()

    def invoke(self, result=None, failure=None):
        with patch('codex_profile.open_profile', return_value=self.profile), \
             patch('codex_profile.environment', return_value={'CODEX_HOME': str(self.root/'config')}), \
             patch('codex_assets.verify_assets', return_value=self.root/'assets/bin/codex-app-server.exe'), \
             patch.object(runtime, 'CodexOwnedSession') as owner, \
             patch.object(runtime, 'SupervisedACPTransport'):
            owner.return_value.run.return_value = result
            owner.return_value.run.side_effect = failure
            value = runtime.invoke(self.target, execution_timeout=120, cleanup_timeout=10,
                model_id='model', messages=[dict(role='user',content='hello')])
            return value, owner.return_value.run.call_args

    def test_clean_owned_command_and_account(self):
        value, call = self.invoke(dict(cleanup_confirmed=True))
        self.assertEqual(call.args[0], [str(self.root/'assets/bin/codex-app-server.exe'), '--listen', 'stdio://'])
        self.assertEqual(call.kwargs['expected_account_digest'], 'a'*64)
        self.assertEqual(call.kwargs['prompt'], 'hello')
        self.assertEqual(self.profile.calls, ['verify','begin','complete','close'])
        self.assertFalse(runtime._QUARANTINED_PROFILES)

    def test_unknown_and_exception_retain(self):
        self.invoke(dict(cleanup_confirmed=False))
        self.assertEqual(runtime._QUARANTINED_PROFILES, [self.profile])
        self.assertEqual(self.profile.calls[-1], 'quarantine')

    def test_exception_retains(self):
        with self.assertRaises(RuntimeError):
            self.invoke(failure=RuntimeError('fixture'))
        self.assertNotIn('close', self.profile.calls)
        self.assertEqual(len(runtime._QUARANTINED_ENGINES), 1)

    def test_wrong_frozen_identity_does_not_launch(self):
        for key in ('profile_id','account_fingerprint'):
            original = self.target.recipe[key]
            self.target.recipe[key] = 'wrong'
            with self.assertRaises(ValueError):
                self.invoke({})
            self.target.recipe[key] = original
        self.assertEqual(self.profile.calls, ['close','close'])
