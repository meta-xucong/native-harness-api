import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import workbuddy_login as cli
from beta15_login_flow import SCENARIOS, REVIEW_ITEMS
from beta15_login_profile import RESULT, ProfileError


class LoginCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def scripted(self, scenario):
        output = io.StringIO()
        report = cli.run_simulation(scenario, self.parent, scripted=True, json_output=True, output=output)
        self.assertEqual(json.loads(output.getvalue()), report)
        self.assertLess(len(output.getvalue()), 8192)
        self.assertNotIn(str(self.parent), output.getvalue())
        self.assertTrue(report['flow']['all_account_data_synthetic'])
        self.assertTrue(report['flow']['simulation_consent_only'])
        self.assertFalse(report['flow']['real_account_verified'])
        self.assertFalse(report['flow']['real_model_ready'])
        self.assertFalse(report['native_backend_available'])
        return report

    def test_success_requires_full_confirmation_and_readback(self):
        report = self.scripted('success')
        f = report['flow']
        self.assertEqual(f['outcome'], 'simulated_complete')
        self.assertEqual(f['acknowledgments'], len(REVIEW_ITEMS))
        for key in ['login_settled', 'account_read', 'status_read', 'manual_identity_confirmed']:
            self.assertTrue(f[key])
        folders = list(self.parent.iterdir()); self.assertEqual(len(folders), 1)
        saved = json.loads((folders[0] / 'output' / RESULT).read_text())
        self.assertEqual(saved, report['storage'])
        self.assertFalse(saved['security_boundary_verified'])

    def test_cancel_pending_has_peer_settlement_and_read(self):
        f = self.scripted('cancel-pending')['flow']
        self.assertEqual(f['outcome'], 'cancelled_no_account')
        self.assertTrue(f['cancel_delivered']); self.assertTrue(f['login_settled']); self.assertTrue(f['account_read'])
        self.assertFalse(f['cancellation_proves_rollback'])

    def test_cancel_race_retains_account_without_logout_or_model(self):
        r = self.scripted('cancel-race')
        self.assertEqual(r['flow']['outcome'], 'cancelled_account_present')
        self.assertTrue(r['flow']['simulated_account_present'])
        self.assertTrue(r['storage']['retained'])
        self.assertFalse(r['storage']['logout_performed']); self.assertFalse(r['storage']['deletion_performed'])
        self.assertFalse(r['flow']['manual_identity_confirmed'])

    def test_timeout_is_unknown_and_logically_quarantined(self):
        r = self.scripted('timeout')
        self.assertEqual(r['flow']['outcome'], 'unknown_retained')
        self.assertEqual(r['flow']['profile_disposition'], 'quarantine')
        self.assertEqual(r['storage']['outcome'], 'SIMULATED_UNKNOWN_RETAINED')
        self.assertFalse(r['storage']['security_boundary_verified'])

    def test_early_cancel_and_refused_states_do_not_touch_parent(self):
        for scenario in ['cancel-before-start', 'existing-state', 'protected-state']:
            with patch.object(cli, 'create_simulation_profile', side_effect=AssertionError('must not call')):
                r = self.scripted(scenario)
            self.assertFalse(r['flow']['profile_created']); self.assertIsNone(r['storage'])
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_identity_rejection_preserves_profile(self):
        r = self.scripted('identity-rejected')
        self.assertEqual(r['flow']['outcome'], 'identity_rejected')
        self.assertTrue(r['storage']['retained'])
        self.assertFalse(r['flow']['manual_identity_confirmed'])

    def test_interactive_decline_before_profile(self):
        out = io.StringIO()
        r = cli.run_simulation('success', self.parent, source=io.StringIO('no\n'), output=out)
        self.assertEqual(r['flow']['outcome'], 'cancelled_before_start')
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_interactive_success_and_manual_cancel(self):
        out = io.StringIO()
        r = cli.run_simulation('success', self.parent, source=io.StringIO('yes\n' * (len(REVIEW_ITEMS) + 2)), output=out)
        self.assertEqual(r['flow']['outcome'], 'simulated_complete')
        r = cli.run_simulation('success', self.parent, source=io.StringIO('yes\n' * len(REVIEW_ITEMS) + 'no\n'), output=io.StringIO())
        self.assertEqual(r['flow']['outcome'], 'cancelled_no_account')

    def test_native_mode_never_creates_profile_even_with_environment_flag(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'WORKBUDDY_NATIVE_LOGIN': '1'}), patch.object(cli, 'create_simulation_profile', side_effect=AssertionError()), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--mode', 'native', '--profile-parent', str(self.parent)])
        self.assertEqual(code, 2)
        self.assertIn('NATIVE_LOGIN_NOT_AVAILABLE', err.getvalue())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_invalid_arguments_never_echo_secret_shaped_input(self):
        secret = 'DO_NOT_ECHO_PRIVATE_TOKEN'
        for argv in [['--mode', secret], ['--mode', 'simulate', '--scenario', secret], ['--bad', secret]]:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(argv)
            self.assertEqual(code, 2)
            self.assertNotIn(secret, out.getvalue() + err.getvalue())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_invalid_interactive_value_is_not_saved_or_echoed(self):
        secret = 'DO_NOT_ECHO_PRIVATE_TOKEN'
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO(secret + '\n')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--mode', 'simulate', '--profile-parent', str(self.parent)])
        self.assertEqual(code, 2)
        self.assertNotIn(secret, out.getvalue() + err.getvalue())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_readonly_authorization_explanation(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(cli, 'create_simulation_profile', side_effect=AssertionError()):
            code = cli.main(['--explain-real-test'])
        self.assertEqual(code, 0)
        for phrase in ['默认浏览器', '存储', '模型', '未接入', 'public beta16 SFE',
                       '已通过云端匿名入口', '不证明真实登录', '完整登录仍未执行']:
            self.assertIn(phrase, out.getvalue())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_anonymous_evidence_does_not_enable_native_mode_via_explanation(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'create_simulation_profile', side_effect=AssertionError()), \
             patch.object(cli, 'run_simulation', side_effect=AssertionError()), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--mode', 'native', '--explain-real-test', '--scripted',
                             '--profile-parent', str(self.parent)])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('NATIVE_LOGIN_NOT_AVAILABLE', err.getvalue())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_invalid_choice_after_profile_preserves_it_without_echo(self):
        secret = 'DO_NOT_ECHO_PRIVATE_TOKEN'
        source = io.StringIO('yes\n' * len(REVIEW_ITEMS) + secret + '\n')
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdin', source), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--mode', 'simulate', '--profile-parent', str(self.parent)])
        self.assertEqual(code, 2)
        self.assertNotIn(secret, out.getvalue() + err.getvalue())
        self.assertIn('保留', err.getvalue())
        profiles = list(self.parent.iterdir())
        self.assertEqual(len(profiles), 1)
        self.assertTrue((profiles[0] / 'simulation-profile.json').is_file())
        self.assertFalse((profiles[0] / 'output' / RESULT).exists())

    def test_subprocess_cli_is_standalone_and_sanitized(self):
        result = subprocess.run([sys.executable, str(Path(cli.__file__)), '--mode', 'simulate', '--scenario', 'success', '--scripted', '--json', '--profile-parent', str(self.parent)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stderr, b'')
        r = json.loads(result.stdout)
        self.assertEqual(r['flow']['outcome'], 'simulated_complete')
        self.assertFalse(r['flow']['native_cleanup_verified'])


if __name__ == '__main__': unittest.main()
