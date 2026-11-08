"""Bounded pre-force observation only; self-authored child fixtures, no vendor."""
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from native_harness_api.owned_exit_probe import pre_force_exit_probe
from native_harness_api.beta15_owned_session import OwnedSessionEngine
from native_harness_api.public_sfe_owned_session import PublicSfeOwnedSession
from test_public_sfe_owned_session import SyntheticTransport, CHILD, digest

NODE = shutil.which('node')
NODE_CHILD = Path(__file__).parent / 'fakes' / 'beta15_shim_child.mjs'


class PreForceProbeTests(unittest.TestCase):
    def fixture(self, *, facts=None, eof=False, stderr=True, stdin=True):
        engine = SimpleNamespace(_closing=False, _io_ready=True, _frames=queue.Queue(),
                                 eof=eof, stderr_eof=stderr, stdin_eof_sent=stdin)
        observed = {'host_exited': True, 'owned_job_empty': True, 'host_exit_code': 0}
        if facts is not None: observed = facts
        calls = []
        def observe(): calls.append('observe'); return observed
        def forbidden(*args, **kwargs): self.fail('probe attempted process mutation')
        engine.transport = SimpleNamespace(observe=observe, close=forbidden, terminate=forbidden)
        engine.dispatched = []
        def dispatch(frame):
            engine.dispatched.append(frame)
            if frame is None: engine.eof = True
        engine._dispatch = dispatch
        return engine, calls

    def test_drains_before_and_after_observe_without_force(self):
        e, calls = self.fixture(); e._frames.put(b'early')
        original = e.transport.observe
        def observe():
            e._frames.put(b'late'); e._frames.put(None)
            return original()
        e.transport.observe = observe
        result = pre_force_exit_probe(e, 10, clock=lambda: 0)
        self.assertTrue(result['natural']); self.assertEqual(result['exit_code'], 0)
        self.assertEqual(e.dispatched, [b'early', b'late', None]); self.assertEqual(calls, ['observe'])

    def test_closed_unready_or_expired_never_observe(self):
        for state in ('closing', 'unready', 'expired'):
            e, calls = self.fixture(eof=True)
            if state == 'closing': e._closing = True
            if state == 'unready': e._io_ready = False
            result = pre_force_exit_probe(e, 0 if state == 'expired' else 10, clock=lambda: 0)
            self.assertFalse(result['natural']); self.assertEqual(calls, [])

    def test_deadline_crossing_during_observe_cannot_upgrade(self):
        e, _ = self.fixture(eof=True); now = [0]
        def observe(): now[0] = 10; return {'host_exited': True, 'owned_job_empty': True, 'host_exit_code': 0}
        e.transport.observe = observe
        self.assertFalse(pre_force_exit_probe(e, 10, clock=lambda: now[0])['natural'])

    def test_missing_eof_or_whole_job_or_stdin_not_natural(self):
        cases = [dict(eof=False), dict(eof=True, stderr=False), dict(eof=True, stdin=False),
                 dict(eof=True, facts={'host_exited': True, 'owned_job_empty': False, 'host_exit_code': 0}),
                 dict(eof=True, facts={'host_exited': 1, 'owned_job_empty': True, 'host_exit_code': 0})]
        for kwargs in cases:
            e, _ = self.fixture(**kwargs)
            self.assertFalse(pre_force_exit_probe(e, 10, require_stdin=True, clock=lambda: 0)['natural'])

    def test_failed_or_cached_cleanup_observations_are_not_natural(self):
        for extra in ({'code': 'OBSERVATION_FAILED'}, {'code': 'CLOSED'},
                      {'handles_closed': True}, {'cleanup_complete': True}):
            e, _ = self.fixture(eof=True, facts={'host_exited': True, 'owned_job_empty': True,
                                                'host_exit_code': 0, **extra})
            result = pre_force_exit_probe(e, 10, clock=lambda: 0)
            self.assertFalse(result['natural']); self.assertTrue(result['diagnostic']['observation_failed'])

    def test_probe_preserves_original_fault_and_exact_exit_code(self):
        for code in (7, None, False):
            e, _ = self.fixture(eof=True, facts={'host_exited': True, 'owned_job_empty': True, 'host_exit_code': code})
            e.fault = 'ORIGINAL_FAULT'
            result = pre_force_exit_probe(e, 10, clock=lambda: 0)
            self.assertTrue(result['natural'])  # Physical exit is not turn success.
            self.assertEqual(result['exit_code'], code if type(code) is int else None)
            self.assertEqual(e.fault, 'ORIGINAL_FAULT')

    def test_observe_exception_finite_no_success(self):
        e, _ = self.fixture(eof=True)
        def fail(): raise RuntimeError('PRIVATE')
        e.transport.observe = fail
        result = pre_force_exit_probe(e, 10, clock=lambda: 0)
        self.assertFalse(result['natural']); self.assertNotIn('PRIVATE', str(result))

    def test_bounded_drain_cannot_reach_eof_past_cap(self):
        e, _ = self.fixture()
        for _ in range(1100): e._frames.put(b'frame')
        e._frames.put(None)
        result = pre_force_exit_probe(e, 10, clock=lambda: 0)
        self.assertFalse(result['natural']); self.assertLessEqual(len(e.dispatched), 1024)
        self.assertFalse(e.eof)

    def test_deadline_crossing_during_dispatch_stops_drain(self):
        e, calls = self.fixture(); now = [0]
        e._frames.put(b'late'); e._frames.put(None)
        def dispatch(frame): e.dispatched.append(frame); now[0] = 10
        e._dispatch = dispatch
        result = pre_force_exit_probe(e, 10, clock=lambda: now[0])
        self.assertFalse(result['natural']); self.assertEqual(calls, [])


class PreForceEngineTests(unittest.TestCase):
    def run_case(self, kind, *, reveal=True, code=None):
        base = SyntheticTransport
        class MaskedGrace(base):
            preforce = False
            close_called = False
            def observe(self):
                facts = super().observe()
                if self.preforce and reveal:
                    if code == 'missing': facts.pop('host_exit_code', None)
                    return facts
                if self.close_called: return facts
                return {'host_exited': False, 'owned_job_empty': False, 'host_exit_code': None}
            def close(self, timeout=5):
                self.close_called = True
                return super().close(timeout)
        transport = MaskedGrace()
        module = 'native_harness_api.' + ('beta15_owned_session' if kind == 'shim' else 'public_sfe_owned_session')
        invoked = []
        def reveal_at_probe(engine, deadline, **options):
            # Exact retained child has already naturally exited. No wait, restart
            # or termination is smuggled into this final observation window.
            self.assertEqual(transport.process.wait(timeout=0), 7 if code == 7 else 0)
            self.assertFalse(transport.close_called)
            transport.preforce = True; invoked.append(True)
            return pre_force_exit_probe(engine, deadline, **options)
        with tempfile.TemporaryDirectory() as directory, patch(module+'.pre_force_exit_probe', side_effect=reveal_at_probe):
            env = {k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC')}
            env['SHIM_FIXTURE_MODE'] = 'exit_nonzero' if code == 7 else 'success'
            env['SFE_FIXTURE_MODE'] = 'exit_nonzero' if code == 7 else 'success'
            engine = OwnedSessionEngine(transport) if kind == 'shim' else PublicSfeOwnedSession(transport)
            command = [NODE, str(NODE_CHILD.resolve())] if kind == 'shim' else [sys.executable, '-u', str(CHILD.resolve())]
            extra = {} if kind == 'shim' else {'expected_account_digest': digest()}
            result = engine.run(command, Path(directory), env, workspace=directory, model_id='synthetic-b',
                                prompt='synthetic', timeout=15, cleanup_timeout=2, **extra)
        self.assertEqual(invoked, [True]); self.assertEqual(transport.starts, 1)
        return result

    def test_sfe_natural_exit_recognized_only_in_preforce_window(self):
        r = self.run_case('sfe'); self.assertEqual(r['outcome'], 'succeeded', r); self.assertTrue(r['natural_exit_zero'])
        self.assertIn('natural_observation', r)

    @unittest.skipUnless(NODE, 'Self-authored Node shim fixture requires Node')
    def test_shim_natural_exit_recognized_only_in_preforce_window(self):
        r = self.run_case('shim'); self.assertEqual(r['outcome'], 'succeeded', r); self.assertTrue(r['natural_exit_zero'])
        self.assertIn('natural_observation', r)

    def test_facts_only_after_close_never_become_natural(self):
        for kind in ('sfe', 'shim') if NODE else ('sfe',):
            r = self.run_case(kind, reveal=False)
            self.assertEqual(r['outcome'], 'indeterminate'); self.assertFalse(r['natural_owned_exit'])
            self.assertTrue(r['cleanup']['cleanup_complete'])

    def test_missing_or_nonzero_code_cannot_be_success(self):
        for code in ('missing', 7):
            r = self.run_case('sfe', code=code)
            self.assertEqual(r['outcome'], 'indeterminate'); self.assertFalse(r['natural_exit_zero'])
