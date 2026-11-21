"""Self-authored process fixtures. SyntheticTransport is NOT Windows tree proof."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from native_harness_api.supervised_acp_transport import SupervisedACPTransport
from native_harness_api.codex_owned_session import CodexOwnedSession
from native_harness_api.codex_session import account_identity_digest, project_result
from test_public_sfe_owned_session import SyntheticTransport

CHILD = Path(__file__).parent / 'fakes' / 'codex_child.py'
DIGEST = account_identity_digest(dict(type='chatgpt', email='fixture@example.test', workspaceName=None))

class CodexOwnedTests(unittest.TestCase):
    def run_mode(self, mode='natural', transport=None, timeout=4, cleanup_timeout=.6):
        transport = transport or SyntheticTransport()
        engine = CodexOwnedSession(transport)
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, CODEX_FIXTURE_MODE=mode)
            result = engine.run([sys.executable, '-u', str(CHILD.resolve())], root, env,
                                workspace=root, model_id='fixture-model', prompt='fixture',
                                expected_account_digest=DIGEST, timeout=timeout, cleanup_timeout=cleanup_timeout)
            if mode == 'descendant':
                self.assertTrue((Path(root) / 'codex-descendant.ready').exists(), result)
        return result, project_result(result, 'fixture-model'), transport

    def test_natural_exit(self):
        result, projected, _ = self.run_mode()
        self.assertEqual(projected['outcome'], 'succeeded', result)
        self.assertTrue(result['graceful_exit'])
        self.assertFalse(result['forced_cleanup'])
        self.assertEqual(projected['output_blocks'][0]['text'], 'fixture answer')

    def test_terminal_then_idle_force_is_success(self):
        result, projected, _ = self.run_mode('idle_hang')
        self.assertEqual(projected['outcome'], 'succeeded', result)
        self.assertTrue(result['forced_cleanup'])
        self.assertFalse(result['forced_before_terminal'])
        self.assertFalse(result['graceful_exit'])

    def test_force_before_terminal_unknown(self):
        result, projected, _ = self.run_mode('preterminal_hang', timeout=.25)
        self.assertEqual(projected['outcome'], 'pending', result)
        self.assertTrue(result['forced_before_terminal'])

    def test_cleanup_unknown_not_success(self):
        class UnknownCleanup(SyntheticTransport):
            def close(self, timeout=5):
                result = super().close(timeout)
                result['owned_job_empty'] = None
                return result
        result, projected, _ = self.run_mode(transport=UnknownCleanup())
        self.assertEqual(projected['outcome'], 'pending', result)
        self.assertFalse(result['cleanup_confirmed'])

    def test_post_terminal_conflict_not_washed_away(self):
        result, projected, _ = self.run_mode('late_conflict')
        self.assertEqual(projected['outcome'], 'pending', result)
        self.assertEqual(result['session']['fault'], 'post_terminal_event')

    @unittest.skipUnless(os.name == 'nt', 'Windows Job integration')
    def test_windows_job_natural_terminal(self):
        result, projected, _ = self.run_mode(transport=SupervisedACPTransport(), timeout=15, cleanup_timeout=4)
        self.assertEqual(projected['outcome'], 'succeeded', result)
        self.assertTrue(result['graceful_exit'], result)
        self.assertTrue(result['cleanup']['owned_job_empty'], result)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job integration')
    def test_windows_job_terminal_idle_forced(self):
        result, projected, _ = self.run_mode('idle_hang', transport=SupervisedACPTransport(), timeout=15, cleanup_timeout=4)
        self.assertEqual(projected['outcome'], 'succeeded', result)
        self.assertTrue(result['forced_cleanup'], result)
        self.assertFalse(result['forced_before_terminal'], result)
        self.assertTrue(result['cleanup']['owned_job_empty'], result)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job integration')
    def test_windows_job_no_terminal_unknown(self):
        result, projected, _ = self.run_mode('preterminal_hang', transport=SupervisedACPTransport(), timeout=2, cleanup_timeout=4)
        self.assertEqual(projected['outcome'], 'pending', result)
        self.assertTrue(result['forced_before_terminal'], result)
        self.assertTrue(result['cleanup']['owned_job_empty'], result)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job integration')
    def test_windows_job_cleans_descendant_after_host_exit(self):
        # Child's detached descendant stays in the owned Job; host exits on EOF.
        result, projected, _ = self.run_mode('descendant', transport=SupervisedACPTransport(), timeout=15, cleanup_timeout=4)
        self.assertEqual(projected['outcome'], 'succeeded', result)
        self.assertFalse(result['graceful_exit'], result)
        self.assertTrue(result['forced_cleanup'], result)
        self.assertFalse(result['forced_before_terminal'], result)
        self.assertTrue(result['cleanup']['owned_job_empty'], result)
        self.assertTrue(result['cleanup_confirmed'], result)

    def test_precancel_never_launches(self):
        transport = SyntheticTransport()
        event = threading.Event()
        event.set()
        result = CodexOwnedSession(transport).run([], None, {}, workspace='', model_id='', prompt='', expected_account_digest=DIGEST, cancellation=event)
        self.assertEqual(transport.starts, 0)
        self.assertFalse(result['launch_attempted'])
        self.assertEqual(result['outcome'], 'cancelled')

if __name__ == '__main__':
    unittest.main()
