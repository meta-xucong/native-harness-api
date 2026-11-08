"""Only self-authored Python child; Windows cases use actual owned Job machinery."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from native_harness_api.public_sfe_owned_session import PublicSfeOwnedSession
from native_harness_api.supervised_acp_transport import SupervisedACPTransport

CHILD = Path(__file__).parent / 'fakes' / 'public_sfe_child.py'

class SyntheticTransport:
    """Host-only test double. Not evidence of production descendant ownership."""
    def __init__(self): self.process = None; self.starts = 0
    def start(self, command, cwd, env):
        self.starts += 1
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.stdin, self.stdout, self.stderr = self.process.stdin, self.process.stdout, self.process.stderr
    def observe(self):
        exited = self.process.poll() is not None
        return {'host_exited': exited, 'owned_job_empty': exited, 'host_exit_code': self.process.returncode}
    def close(self, timeout=5):
        if self.process.poll() is None: self.process.kill()
        self.process.wait(timeout=max(.1, timeout))
        for stream in (self.stdin, self.stdout, self.stderr): stream.close()
        return {'host_exited': True, 'owned_job_empty': True, 'handles_closed': True, 'cleanup_complete': True}


def digest():
    from native_harness_api.public_sfe_session import account_identity_digest
    return account_identity_digest({'id': 'synthetic-account', 'authMethodId': 'workbuddy-desktop'})

class PublicSfeOwnedTests(unittest.TestCase):
    def run_mode(self, mode='success', *, transport=None, timeout=15, cleanup_timeout=1, engine_class=PublicSfeOwnedSession, expected=None):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root).resolve()
            env = {k: v for k, v in os.environ.items() if k.upper() in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC')}
            env['SFE_FIXTURE_MODE'] = mode
            transport = transport or SyntheticTransport()
            engine = engine_class(transport)
            result = engine.run([sys.executable, '-u', str(CHILD.resolve())], path, env,
                workspace=str(path), model_id='synthetic-b', prompt='synthetic',
                expected_account_digest=expected or digest(), timeout=timeout, cleanup_timeout=cleanup_timeout)
            if mode == 'orphan': self.assertTrue((path / 'synthetic-orphan.ready').exists(), result)
            return result, engine, transport

    def test_success_native_eof_no_shim_ack(self):
        result, engine, transport = self.run_mode()
        self.assertEqual(result['outcome'], 'succeeded', result)
        for key in ('cleanup_confirmed', 'natural_exit_zero', 'stdin_eof_sent', 'stdout_eof_observed', 'stderr_eof_observed'):
            self.assertTrue(result[key], result)
        self.assertEqual(result['session']['turn']['text'], 'synthetic answer')
        self.assertFalse(result['retry_safe']); self.assertEqual(result['liveness'], 'unknown')
        self.assertNotIn('shutdown_acknowledged', result)
        with self.assertRaises(ValueError): engine.run([], None, {}, workspace='', model_id='', prompt='', expected_account_digest=digest())
        self.assertEqual(transport.starts, 1)

    def test_precancel_does_not_launch(self):
        transport = SyntheticTransport(); engine = PublicSfeOwnedSession(transport)
        event = threading.Event(); event.set()
        result = engine.run([], None, {}, workspace='', model_id='', prompt='', expected_account_digest=digest(), cancellation=event)
        self.assertEqual(result['outcome'], 'cancelled'); self.assertEqual(transport.starts, 0)

    def test_nonzero_exit_not_cleanup_confirmed(self):
        result, _, _ = self.run_mode('exit_nonzero')
        self.assertEqual(result['outcome'], 'indeterminate'); self.assertEqual(result['host_exit_code_before_cleanup'], 7)
        self.assertFalse(result['cleanup_confirmed'])

    def test_ack_not_terminal(self):
        class ExpireAfterAck(PublicSfeOwnedSession):
            def _dispatch(self, raw):
                super()._dispatch(raw)
                c = self.protocol.coordinator
                if c.reducer is not None and c.reducer.insert_acknowledged:
                    c.deadline = time.monotonic() - 1
        result, _, _ = self.run_mode('ack_only', engine_class=ExpireAfterAck)
        self.assertEqual(result['outcome'], 'indeterminate', result)
        self.assertTrue(result['session']['turn']['insert_acknowledged'])

    def test_late_conflict_after_stdin_eof_still_seen(self):
        result, _, _ = self.run_mode('late_conflict')
        self.assertEqual(result['outcome'], 'indeterminate', result)

    def test_wrong_model_does_not_prompt(self):
        result, _, _ = self.run_mode('wrong_model')
        self.assertFalse(result['session']['prompt_intent_issued']); self.assertEqual(result['outcome'], 'indeterminate')

    def test_anonymous_does_not_start_session(self):
        result, _, _ = self.run_mode('anonymous')
        self.assertFalse(result['session']['prompt_intent_issued']); self.assertEqual(result['outcome'], 'indeterminate')

    def test_account_binding_mismatch(self):
        result, _, _ = self.run_mode(expected='0' * 64)
        self.assertFalse(result['session']['prompt_intent_issued']); self.assertEqual(result['outcome'], 'indeterminate')

    def test_malformed_and_oversized_frames_fail(self):
        for mode in ('bad_frame', 'oversized', 'stderr_overflow'):
            result, _, _ = self.run_mode(mode)
            self.assertEqual(result['outcome'], 'indeterminate', (mode, result))
            self.assertNotIn('PRIVATE', str(result))

    def test_hung_eof_requires_force_is_not_normal_completion(self):
        result, _, _ = self.run_mode('shutdown_hang', cleanup_timeout=.3)
        self.assertEqual(result['outcome'], 'indeterminate'); self.assertFalse(result['natural_owned_exit'])
        self.assertTrue(result['cleanup']['cleanup_complete']); self.assertFalse(result['cleanup_confirmed'])

    def test_close_ack_missing_not_success(self):
        result, _, _ = self.run_mode('close_hang', cleanup_timeout=.3)
        self.assertEqual(result['outcome'], 'indeterminate')

    def test_bootstrap_timeout_is_bounded(self):
        start = time.monotonic(); result, _, _ = self.run_mode('init_hang', timeout=.2, cleanup_timeout=.3)
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(result['outcome'], 'indeterminate'); self.assertFalse(result['session']['prompt_intent_issued'])

    def test_missing_job_empty_or_exit_code_never_confirms(self):
        for field in ('owned_job_empty', 'host_exit_code'):
            class Missing(SyntheticTransport):
                def observe(self):
                    result = super().observe(); result.pop(field); return result
            result, _, _ = self.run_mode(transport=Missing(), cleanup_timeout=.3)
            self.assertFalse(result['cleanup_confirmed']); self.assertEqual(result['outcome'], 'indeterminate')

    def test_exited_host_without_stdout_eof_is_not_confirmed(self):
        class DelayedEOF(SyntheticTransport):
            def start(self, *args):
                super().start(*args)
                self.release = threading.Event()
                stream, release = self.stdout, self.release
                class Pipe:
                    def readline(self, size):
                        value = stream.readline(size)
                        if not value: release.wait(3)
                        return value
                    def close(self): stream.close()
                self.stdout = Pipe()
            def close(self, timeout=5):
                self.release.set()
                return super().close(timeout)
        result, _, _ = self.run_mode(transport=DelayedEOF(), cleanup_timeout=.3)
        self.assertFalse(result['natural_owned_exit']); self.assertFalse(result['cleanup_confirmed'])

    def test_cleanup_true_requires_literal_bool(self):
        class LooseCleanup(SyntheticTransport):
            def close(self, timeout=5):
                result = super().close(timeout); result['owned_job_empty'] = 1; return result
        result, _, _ = self.run_mode(transport=LooseCleanup())
        self.assertFalse(result['cleanup_confirmed']); self.assertEqual(result['outcome'], 'indeterminate')

    def test_partial_launch_failure_cleanup_attempted(self):
        class Broken(SyntheticTransport):
            closed = False
            def start(self, *args): raise OSError('PRIVATE')
            def close(self, timeout=5):
                self.closed = True
                return {'host_exited': None, 'owned_job_empty': None, 'handles_closed': False, 'cleanup_complete': False}
        transport = Broken(); result, _, _ = self.run_mode(transport=transport)
        self.assertTrue(transport.closed); self.assertEqual(result['outcome'], 'indeterminate')
        self.assertNotIn('PRIVATE', str(result))

    @unittest.skipUnless(os.name == 'nt', 'Windows Job test')
    def test_windows_owned_job_success(self):
        result, _, _ = self.run_mode(transport=SupervisedACPTransport(), cleanup_timeout=4)
        self.assertEqual(result['outcome'], 'succeeded', result); self.assertTrue(result['cleanup_confirmed'])

    @unittest.skipUnless(os.name == 'nt', 'Windows Job test')
    def test_windows_orphan_blocks_natural_completion(self):
        result, _, _ = self.run_mode('orphan', transport=SupervisedACPTransport(), cleanup_timeout=4)
        self.assertEqual(result['outcome'], 'indeterminate', result); self.assertFalse(result['cleanup_confirmed'])
        self.assertTrue(result['cleanup']['owned_job_empty'])
