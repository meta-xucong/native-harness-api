"""Fake-owner algorithm tests plus explicitly skipped real Windows lifecycle."""
import io
import copy
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from native_harness_api.supervised_acp_transport import SupervisedACPTransport, TransportError
from native_harness_api.windows_job import WindowsJobError, HANDLE, DWORD, BOOL
import test_beta15_owned_session as fixture_cleanup


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
    def exit_code(self):
        return 0 if self.exited else None
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
    def test_observe_reports_exact_exit_code_only_after_exit(self):
        owner=FakeOwner();transport=self.launch(owner)
        self.assertIsNone(transport.observe()['host_exit_code'])
        owner.exited=True
        self.assertEqual(transport.observe()['host_exit_code'],0)
        owner.exit_code=lambda:7
        self.assertEqual(transport.observe()['host_exit_code'],7)
        owner.exit_code=lambda:False
        self.assertIsNone(transport.observe()['host_exit_code'])
        transport.close()

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


class DescendantWitness:
    """Test-only retained object witnesses, never PID termination/reattachment."""
    def __init__(self, api):
        self.api = api
        self.handles = {}

    def acquire(self, root, job):
        open_process = self.api.dll.OpenProcess
        open_process.restype = HANDLE
        open_process.argtypes = [DWORD, BOOL, DWORD]
        for label in ('child', 'grandchild'):
            # Only known ready self-authored fixture files, bounded numeric data.
            with (root / (label + '.pid')).open() as file:
                raw = file.read(32)
            if not raw.isascii() or not raw.isdigit() or not 0 < int(raw) <= 0xffffffff:
                raise AssertionError('SYNTHETIC_PID_INVALID')
            handle = open_process(0x00100000 | 0x1000, False, int(raw))
            if not handle:
                raise AssertionError('SYNTHETIC_WITNESS_OPEN_FAILED')
            self.handles[label] = handle  # Retain even on membership-query failure.
            belongs = BOOL()
            if not self.api.IsProcessInJob(handle, job, ctypes.byref(belongs)) or not belongs.value:
                raise AssertionError('SYNTHETIC_WITNESS_MEMBERSHIP_FAILED')

    def immediate(self):
        # One zero-time observation per exact retained object; no waiting upgrade.
        facts = {}
        for label, handle in self.handles.items():
            status = self.api.WaitForSingleObject(handle, 0)
            facts[label] = True if status == 0 else False if status == 258 else None
        return facts

    def finish(self, timeout=2, clock=time.monotonic):
        deadline = clock() + timeout
        ok = True
        for label, handle in tuple(self.handles.items()):
            try:
                if self.api.WaitForSingleObject(handle, max(0, int((deadline-clock())*1000))) != 0:
                    ok = False
            except Exception:
                ok = False
            finally:
                try:
                    if self.api.CloseHandle(handle):
                        del self.handles[label]
                    else:
                        ok = False
                except Exception:
                    ok = False
        if not ok:
            raise AssertionError('SYNTHETIC_WITNESS_FINALIZE_FAILED')


def finish_transport_fixture(folder, transport, retained, witness):
    """No cleanup result here can replace the original product observations."""
    original = retained.get('result')
    witness_failed = False
    try:
        if not original or original.get('cleanup', {}).get('cleanup_complete') is not True:
            transport.close(timeout=2)  # Same owner only, test hygiene, never restart.
    except Exception:
        witness_failed = True
    try:
        if witness is not None:
            witness.finish()
    except Exception:
        witness_failed = True
    # Always attempt directory hygiene even if retained-object wait/close failed.
    print(json.dumps({'fixture': 'synthetic-transport-descendants',
                      'immediate_after_close': retained.get('immediate'),
                      'witness_finalize_failed': witness_failed}, sort_keys=True))
    try:
        fixture_cleanup.finish_synthetic_fixture(folder, transport, original,
            mode=retained.get('mode'), owned=True)
    finally:
        if witness_failed:
            raise AssertionError('SYNTHETIC_WITNESS_FINALIZE_FAILED')


class DescendantWitnessTests(unittest.TestCase):
    def test_immediate_false_never_upgraded_by_later_wait(self):
        class API:
            closed = []
            def WaitForSingleObject(self, handle, ms): return 258 if ms == 0 else 0
            def CloseHandle(self, handle): self.closed.append(handle); return True
        api = API(); witness = DescendantWitness(api)
        witness.handles = {'child': 11, 'grandchild': 12}
        immediate = witness.immediate()
        witness.finish(clock=lambda: 0)
        self.assertEqual(immediate, {'child': False, 'grandchild': False})
        self.assertEqual(api.closed, [11, 12]); self.assertFalse(witness.handles)

    def test_wait_failure_still_closes_all_handles_and_fails(self):
        class API:
            closed = []
            def WaitForSingleObject(self, handle, ms): return 0xffffffff
            def CloseHandle(self, handle): self.closed.append(handle); return True
        api = API(); witness = DescendantWitness(api)
        witness.handles = {'child': 11, 'grandchild': 12}
        self.assertEqual(witness.immediate(), {'child': None, 'grandchild': None})
        with self.assertRaisesRegex(AssertionError, 'SYNTHETIC_WITNESS_FINALIZE_FAILED'):
            witness.finish(clock=lambda: 0)
        self.assertEqual(api.closed, [11, 12])

    def test_membership_failure_retains_opened_object_for_cleanup(self):
        class Open:
            def __call__(self, rights, inherit, pid):
                self.args = rights, inherit, pid
                return 11
        class API:
            def __init__(self):
                self.dll = type('DLL', (), {})()
                self.dll.OpenProcess = Open()
            def IsProcessInJob(self, handle, job, belongs): return True  # BOOL remains false.
            def WaitForSingleObject(self, handle, ms): return 0
            def CloseHandle(self, handle): return True
        api = API(); witness = DescendantWitness(api)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'child.pid').write_text('123')
            with self.assertRaisesRegex(AssertionError, 'SYNTHETIC_WITNESS_MEMBERSHIP_FAILED'):
                witness.acquire(root, 77)
        self.assertEqual(api.dll.OpenProcess.args, (0x101000, False, 123))
        self.assertEqual(witness.handles, {'child': 11})
        witness.finish(clock=lambda: 0)
        self.assertFalse(witness.handles)

    def test_failed_handle_close_is_not_forgotten(self):
        class API:
            def WaitForSingleObject(self, handle, ms): return 0
            def CloseHandle(self, handle): return False
        witness = DescendantWitness(API()); witness.handles = {'child': 11}
        with self.assertRaises(AssertionError): witness.finish(clock=lambda: 0)
        self.assertEqual(witness.handles, {'child': 11})


@unittest.skipUnless(os.name == 'nt', 'Actual Windows synthetic lifecycle NOT RUN on Linux')
class WindowsTransportTests(unittest.TestCase):
    def launch_fixture(self, mode):
        peer = Path(__file__).parent / 'fakes' / 'windows_job_tree.py'
        folder = tempfile.TemporaryDirectory()
        root = Path(folder.name)
        transport = SupervisedACPTransport()
        retained = {'result': None, 'immediate': None,
                    'mode': 'transport-orphan' if mode == 'orphan_parent' else 'transport-tree'}
        holder = {'witness': None}
        self.addCleanup(lambda: finish_transport_fixture(folder, transport, retained, holder['witness']))
        transport.start([sys.executable, str(peer.resolve()), mode, str(root)], root, dict(os.environ))
        deadline = time.monotonic() + 5
        while not (root / (mode + '.ready')).exists():
            if time.monotonic() >= deadline:
                self.fail('SYNTHETIC_TREE_STARTUP_TIMEOUT')
            time.sleep(.01)
        witness = DescendantWitness(transport._owner._api)
        holder['witness'] = witness
        witness.acquire(root, transport._owner._job)
        return transport, retained, witness

    def close_and_witness(self, transport, retained, witness):
        facts = transport.close(5)
        retained['result'] = {'cleanup': {**copy.deepcopy(facts), 'io_threads_stopped': True}}
        # Observe immediately, before reporting or unrelated assertions/cleanup.
        immediate = witness.immediate()
        retained['immediate'] = dict(immediate)
        # This fixture creates no IO worker threads; that True is test hygiene
        # metadata, not a fact inferred from the product transport.
        print(json.dumps({'fixture': 'synthetic-transport-immediate',
                          'cleanup': fixture_cleanup._cleanup_projection(facts),
                          'descendants_signaled': immediate}, sort_keys=True))
        for key in ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete'):
            self.assertIs(facts.get(key), True)
        self.assertEqual(immediate, {'child': True, 'grandchild': True},
                         'JOB_ACCOUNTING_ZERO_WITH_DESCENDANT_NOT_SIGNALED')

    def test_owned_parent_child_tree_and_binary_stdio(self):
        transport, retained, witness = self.launch_fixture('parent')
        self.assertFalse(transport.observe()['owned_job_empty'])
        self.assertIsNotNone(transport.stdin)
        self.close_and_witness(transport, retained, witness)

    def test_host_exit_still_requires_descendant_cleanup(self):
        transport, retained, witness = self.launch_fixture('orphan_parent')
        deadline = time.monotonic() + 5
        while transport.observe()['host_exited'] is not True:
            if time.monotonic() >= deadline:
                self.fail('HOST_EXIT_TIMEOUT')
            time.sleep(.01)
        self.assertFalse(transport.observe()['owned_job_empty'])
        self.assertFalse(transport.observe()['cleanup_complete'])
        self.close_and_witness(transport, retained, witness)
