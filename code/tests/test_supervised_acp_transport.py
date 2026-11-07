"""Fake-owner algorithm tests plus explicitly skipped real Windows lifecycle."""
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from native_harness_api.supervised_acp_transport import SupervisedACPTransport, TransportError
from native_harness_api.windows_job import WindowsJobError


class FakeOwner:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
        self.pid = 123
        self.exited, self.empty = False, False
        self.fail_close, self.fail_query = False, False
        self.calls = []
    def host_exited(self):
        if self.fail_query: raise OSError('secret diagnostic must not escape')
        return self.exited
    def wait_empty(self, timeout):
        return {'owned_job_empty': self.empty}
    def close(self, timeout):
        self.calls.append(timeout)
        if self.fail_close: raise OSError('secret diagnostic must not escape')
        self.exited = self.empty = True
        for stream in (self.stdin, self.stdout, self.stderr): stream.close()
        return {'owned_job_empty': True}


class TransportTests(unittest.TestCase):
    def launch(self, owner):
        transport = SupervisedACPTransport()
        with patch('native_harness_api.supervised_acp_transport.OwnedWindowsJob.spawn', return_value=owner) as spawn:
            result = transport.start(['synthetic.exe'], Path.cwd(), {})
            self.assertEqual(result, {'pid': 123, 'host_started': True})
            with self.assertRaises(TransportError): transport.start([], Path.cwd(), {})
            self.assertEqual(spawn.call_count, 1)
        return transport
    def test_streams_facts_and_idempotent_close(self):
        owner = FakeOwner(); transport = self.launch(owner)
        self.assertIs(transport.stdin, owner.stdin)
        self.assertIs(transport.stdout, owner.stdout)
        self.assertIs(transport.stderr, owner.stderr)
        self.assertFalse(transport.observe()['host_exited'])
        facts = transport.close(.2)
        self.assertTrue(facts['handles_closed'])
        self.assertTrue(facts['cleanup_complete'])
        self.assertTrue(facts['owned_job_empty'])
        self.assertNotIn('stopped_confirmed', facts)
        self.assertEqual(transport.close(.1), facts)
        self.assertEqual(owner.calls, [.2])
        with self.assertRaises(TransportError): _ = transport.stdin
    def test_failed_cleanup_keeps_owner_and_retries(self):
        owner = FakeOwner(); transport = self.launch(owner); owner.fail_close = True
        facts = transport.close(.1)
        self.assertIsNone(facts['owned_job_empty'])
        self.assertFalse(facts['cleanup_complete'])
        self.assertIs(transport._owner, owner)
        self.assertNotIn('secret', repr(facts))
        owner.fail_close = False
        self.assertTrue(transport.close(.2)['cleanup_complete'])
        self.assertEqual(owner.calls, [.1, .2])
    def test_partial_spawn_failure_retains_owner_without_restart(self):
        owner = FakeOwner(); transport = SupervisedACPTransport()
        with patch('native_harness_api.supervised_acp_transport.OwnedWindowsJob.spawn', side_effect=WindowsJobError('FAKE_FAILURE', owner=owner)) as spawn:
            with self.assertRaisesRegex(TransportError, '^TRANSPORT_START_FAILED$'):
                transport.start(['x'], Path.cwd(), {})
            with self.assertRaises(TransportError): transport.start(['x'], Path.cwd(), {})
            self.assertEqual(spawn.call_count, 1)
        self.assertIs(transport._owner, owner)
        self.assertTrue(transport.close()['cleanup_complete'])
    def test_observation_failure_is_unknown(self):
        owner = FakeOwner(); transport = self.launch(owner); owner.fail_query = True
        facts = transport.observe()
        self.assertIsNone(facts['host_exited'])
        self.assertIsNone(facts['owned_job_empty'])
        self.assertFalse(facts['cleanup_complete'])
    def test_close_before_start_seals_object(self):
        transport = SupervisedACPTransport()
        self.assertFalse(transport.close()['cleanup_complete'])
        with self.assertRaises(TransportError): transport.start([], Path.cwd(), {})
    def test_invalid_timeout_does_not_close(self):
        owner = FakeOwner(); transport = self.launch(owner)
        for value in (-1, True, float('inf'), float('nan'), 301, '1'):
            with self.assertRaises(ValueError): transport.close(value)
        self.assertEqual(owner.calls, [])
    @unittest.skipIf(os.name == 'nt', 'Linux unsupported-platform guard')
    def test_non_windows_no_fallback_no_second_attempt(self):
        transport = SupervisedACPTransport()
        with self.assertRaisesRegex(TransportError, '^WINDOWS_REQUIRED$'):
            transport.start([sys.executable], Path.cwd(), dict(os.environ))
        with self.assertRaises(TransportError): transport.start([sys.executable], Path.cwd(), {})
        self.assertIsNone(transport.close()['owned_job_empty'])


@unittest.skipUnless(os.name == 'nt', 'Actual Windows synthetic lifecycle NOT RUN on Linux')
class WindowsTransportTests(unittest.TestCase):
    def test_owned_parent_child_tree_and_binary_stdio(self):
        # Existing synthetic fixture only; never WorkBuddy/vendor code.
        peer = Path(__file__).parent / 'fakes' / 'windows_job_tree.py'
        with tempfile.TemporaryDirectory() as root:
            transport = SupervisedACPTransport()
            result = transport.start([sys.executable, str(peer.resolve()), 'parent', root], Path(root), dict(os.environ))
            self.assertTrue(result['host_started'])
            try:
                deadline = time.monotonic()+5
                while not (Path(root)/'parent.ready').exists():
                    if time.monotonic() >= deadline: self.fail('Synthetic tree startup timeout')
                    time.sleep(.01)
                self.assertFalse(transport.observe()['owned_job_empty'])
                self.assertIsNotNone(transport.stdin)
                facts = transport.close(5)
                self.assertTrue(facts['host_exited'])
                self.assertTrue(facts['owned_job_empty'])
                self.assertTrue(facts['handles_closed'])
                self.assertTrue(facts['cleanup_complete'])
            finally:
                self.assertTrue(transport.close(5)['cleanup_complete'])

    def test_host_exit_still_requires_descendant_cleanup(self):
        peer = Path(__file__).parent / 'fakes' / 'windows_job_tree.py'
        with tempfile.TemporaryDirectory() as root:
            transport = SupervisedACPTransport()
            transport.start([sys.executable, str(peer.resolve()), 'orphan_parent', root], Path(root), dict(os.environ))
            try:
                deadline = time.monotonic()+5
                while transport.observe()['host_exited'] is not True:
                    if time.monotonic() >= deadline: self.fail('Host exit timeout')
                    time.sleep(.01)
                self.assertFalse(transport.observe()['owned_job_empty'])
                self.assertFalse(transport.observe()['cleanup_complete'])
                self.assertTrue(transport.close(5)['owned_job_empty'])
            finally:
                self.assertTrue(transport.close(5)['cleanup_complete'])
