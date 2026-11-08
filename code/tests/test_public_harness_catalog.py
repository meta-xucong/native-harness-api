"""Authored pipes and a hosted-Windows authored peer only. No vendor execution."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import public_harness_catalog as probe
from test_public_harness_catalog_protocol import init, product, reply


def transcript():
    return b'\n'.join((reply(1, init()), reply(2, product()),
                      reply(3, dict(state='unauthenticated', mode=None, account=None)),
                      reply(4, {'authenticated': False}))) + b'\n'


class Capture(io.BytesIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


class Owner:
    def __init__(self, stdout=None, stderr=b'', *, exited=True, exitcode=0, descendants=0):
        self.stdin = Capture()
        self.stdout = io.BytesIO(transcript() if stdout is None else stdout)
        self.stderr = io.BytesIO(stderr)
        self.exited, self.code, self.descendants = exited, exitcode, descendants
        self.killed = False

    def host_exited(self): return self.exited or self.killed
    def active_processes(self): return self.descendants if self.exited else (0 if self.killed else 1)
    def exit_code(self): return self.code if self.exited else 1
    def terminate(self, _): self.killed = True; return {'owned_job_empty': True}
    def close(self, _): return {'owned_job_empty': True}


class SupervisorTests(unittest.TestCase):
    def run_owner(self, owner, **options):
        with patch.object(probe.OwnedWindowsJob, 'spawn', return_value=owner):
            result = probe.supervise([], Path.cwd(), {}, **options)
        self.assertNotIn('PRIVATE_SECRET', json.dumps(result))
        self.assertFalse(result['native_account_verified'])
        self.assertFalse(result['real_model_ready'])
        return result

    def test_happy_order_closes_stdin_and_proves_natural_exit(self):
        owner = Owner()
        result = self.run_owner(owner)
        self.assertTrue(result['success'])
        self.assertTrue(result['natural_exit_zero'])
        self.assertTrue(result['natural_job_empty'])
        self.assertTrue(result['cleanup_complete'])
        self.assertTrue(result['stdin_closed'])
        messages = [json.loads(x) for x in owner.stdin.saved.splitlines()]
        self.assertEqual([x['method'] for x in messages],
                         ['initialize', 'initialized', 'product/read', 'account/read', 'auth/status'])
        self.assertEqual(result['stdin_bytes'], len(owner.stdin.saved))
        self.assertEqual(result['stdout_bytes'], len(transcript()))

    def test_stderr_discarded_not_exported(self):
        result = self.run_owner(Owner(stderr=b'PRIVATE_SECRET\n'))
        self.assertTrue(result['success'])
        self.assertEqual(result['stderr_bytes'], 15)

    def test_reverse_cancel_does_not_start_auth(self):
        frame = json.dumps(dict(jsonrpc='2.0', id=7, method='elicitation/create',
                                params={'url': 'PRIVATE_SECRET'})).encode() + b'\n'
        owner = Owner(stdout=frame)
        result = self.run_owner(owner)
        self.assertFalse(result['success'])
        self.assertTrue(result['protocol']['elicitation_rejected'])
        self.assertTrue(result['cleanup_complete'])
        messages = [json.loads(x) for x in owner.stdin.saved.splitlines()]
        self.assertEqual(messages[-1], dict(jsonrpc='2.0', id=7, result={'action': 'cancel'}))
        self.assertEqual(len(messages), 2)

    def test_early_eof_malformed_and_extra_protocol(self):
        for raw in (b'', b'PRIVATE_SECRET\n', b'{}', transcript() + b'{}\n', b'\n'):
            with self.subTest(raw_length=len(raw)):
                result = self.run_owner(Owner(stdout=raw))
                self.assertFalse(result['success'])
                self.assertTrue(result['cleanup_complete'])

    def test_output_limits(self):
        for stdout, stderr in ((b'x' * (probe.FRAME_LIMIT + 1), b''),
                               (b'', b'PRIVATE_SECRET' * 100000)):
            result = self.run_owner(Owner(stdout=stdout, stderr=stderr))
            self.assertFalse(result['success'])
            self.assertEqual(result['failure_stage'], 2)
            self.assertTrue(result['cleanup_complete'])

    def test_nonzero_exit_and_descendants_are_not_success(self):
        for owner in (Owner(exitcode=1), Owner(descendants=1)):
            result = self.run_owner(owner)
            self.assertFalse(result['success'])
            self.assertTrue(result['cleanup_complete'])

    def test_deadline_after_valid_protocol_not_cleanup_claim(self):
        result = self.run_owner(Owner(exited=False), timeout=.03)
        self.assertFalse(result['success'])
        self.assertEqual(result['failure_stage'], 4)
        self.assertFalse(result['natural_exit_zero'])
        self.assertTrue(result['cleanup_complete'])

    def test_blocked_writer_released_by_owned_termination(self):
        release = threading.Event()
        class Blocking(Capture):
            def write(self, value):
                release.wait(2)
                return super().write(value)
        owner = Owner(exited=False)
        owner.stdin = Blocking()
        original = owner.terminate
        def terminate(timeout):
            release.set()
            return original(timeout)
        owner.terminate = terminate
        result = self.run_owner(owner, timeout=.03)
        self.assertFalse(result['success'])
        self.assertTrue(result['io_threads_stopped'])
        self.assertTrue(result['cleanup_complete'])

    def test_short_writes_are_completed(self):
        class Short(Capture):
            def write(self, value): return super().write(value[:1])
        owner = Owner(); owner.stdin = Short()
        result = self.run_owner(owner)
        self.assertTrue(result['success'])
        self.assertEqual(len(owner.stdin.saved.splitlines()), 5)

    def test_spawn_failure_owner_is_recovered(self):
        owner = Owner()
        error = OSError('PRIVATE_SECRET'); error.owner = owner
        with patch.object(probe.OwnedWindowsJob, 'spawn', side_effect=error):
            result = probe.supervise([], Path.cwd(), {})
        self.assertFalse(result['success'])
        self.assertTrue(result['cleanup_complete'])
        self.assertNotIn('PRIVATE_SECRET', json.dumps(result))

    def test_uncertain_thread_start_retains_owner(self):
        owner = Owner()
        with patch.object(probe.OwnedWindowsJob, 'spawn', return_value=owner), \
                patch.object(probe.threading.Thread, 'start', side_effect=RuntimeError()):
            result = probe.supervise([], Path.cwd(), {})
        self.assertFalse(result['success'])
        self.assertFalse(result['cleanup_complete'])
        self.assertIn(owner, probe.RETAINED)
        probe.RETAINED.remove(owner)

    def test_limits_rejected_before_spawn(self):
        with patch.object(probe.OwnedWindowsJob, 'spawn') as spawn:
            for options in ({'timeout': 0}, {'timeout': float('nan')}, {'cleanup_timeout': 11}, {'timeout': True}):
                with self.assertRaises(ValueError):
                    probe.supervise([], Path.cwd(), {}, **options)
        spawn.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'actual Windows owned pipes')
    def test_real_owned_windows_authored_peer(self):
        # Own Python peer, not the official Harness. Dynamic replies require the
        # actual ordered requests to arrive, then EOF before natural exit.
        responses = {1: init(), 2: product(), 3: dict(state='unauthenticated', mode=None, account=None),
                     4: {'authenticated': False}}
        code = """import json,sys
responses=json.loads(sys.argv[1]); methods=['initialize','initialized','product/read','account/read','auth/status']; seen=[]
for line in sys.stdin.buffer:
 m=json.loads(line); seen.append(m['method']); assert seen[-1]==methods[len(seen)-1]
 if 'id' in m: print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':responses[str(m['id'])]}),flush=True)
assert seen==methods
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = probe.supervise([sys.executable, '-I', '-B', '-c', code, json.dumps(responses)],
                                     Path(tmp).resolve(), {'SystemRoot': os.environ['SystemRoot']}, timeout=10)
        self.assertTrue(result['success'], result)
        self.assertTrue(result['natural_job_empty'])


if __name__ == '__main__': unittest.main()
