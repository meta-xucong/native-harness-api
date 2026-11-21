"""Client wiring fixtures and real Windows Jobs running synthetic ACP only."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from native_harness_api.workbuddy_acp import ACPClient, ACPError

PEER = Path(__file__).parent/'fakes'/'workbuddy_acp_peer.py'
TRANSPORT = 'native_harness_api.supervised_acp_transport.SupervisedACPTransport'

class MemoryTransport:
    """Test-only lifecycle decisions, never Windows containment evidence."""
    def __init__(self):
        self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
        self.starts = self.closes = 0
    def start(self, command, cwd, env):
        self.starts += 1
        return {'pid':123, 'host_started':True}
    def close(self, timeout):
        self.closes += 1
        return {'host_exited':True, 'owned_job_empty':True,
                'handles_closed':True, 'cleanup_complete':True}

class SupervisedACPFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def client(self):
        c = ACPClient([sys.executable], self.root, dict(os.environ), supervised=True)
        self.addCleanup(c.close)
        return c
    def test_supervision_requires_bool(self):
        for value in ('yes', 1, None):
            with self.assertRaises(ValueError):
                ACPClient([sys.executable], self.root, {}, supervised=value)
    def test_close_before_start_seals_launch(self):
        c = self.client(); result = c.close()
        self.assertIsNone(result['owned_job_empty'])
        self.assertFalse(result['descendants_stopped'])
        with patch(TRANSPORT) as factory, self.assertRaises(ACPError): c.start()
        factory.assert_not_called()
    def test_no_popen_fallback_and_no_late_write(self):
        transport = MemoryTransport()
        with patch(TRANSPORT, return_value=transport), patch('native_harness_api.workbuddy_acp.subprocess.Popen') as popen:
            c = self.client(); self.assertEqual(c.start(), {'pid':123,'host_started':True})
            c._send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})
            self.assertIn(b'initialize', transport.stdin.getvalue())
            result = c.close()
        popen.assert_not_called()
        self.assertTrue(result['io_threads_stopped']); self.assertTrue(result['cleanup_complete'])
        self.assertFalse(result['descendants_stopped']); self.assertEqual(transport.starts, 1)
        with self.assertRaises(ACPError): c.start()
        before = transport.stdin.getvalue()
        with self.assertRaises(ACPError): c._send({'late':True})
        self.assertEqual(transport.stdin.getvalue(), before)
    def test_failed_start_never_falls_back_or_restarts(self):
        transport = MemoryTransport()
        def fail(*args):
            transport.starts += 1
            raise RuntimeError('private source text')
        transport.start = fail
        with patch(TRANSPORT, return_value=transport), patch('native_harness_api.workbuddy_acp.subprocess.Popen') as popen:
            c = self.client()
            with self.assertRaises(ACPError) as exc: c.start()
            self.assertEqual(exc.exception.code, 'ACP_LAUNCH_FAILED')
            self.assertNotIn('private', str(exc.exception))
            with self.assertRaises(ACPError): c.start()
        popen.assert_not_called(); self.assertEqual(transport.starts, 1)
        self.assertGreaterEqual(transport.closes, 1)
    def test_closed_client_rejects_before_durable_intent_without_eof(self):
        with patch(TRANSPORT, return_value=MemoryTransport()):
            c = self.client(); c.start()
        c.session_id, c.model_id = 'synthetic-session', 'fake-a'
        c.close()
        while not c.frames.empty(): c.frames.get_nowait()
        intents = []
        with self.assertRaises(ACPError): c.prompt('synthetic', lambda:intents.append(True))
        with self.assertRaises(ACPError): c._rpc('initialize', {}, lambda:intents.append(True))
        self.assertEqual(intents, []); self.assertFalse(c.prompt_attempted)
    def test_live_io_prevents_cleanup_success_and_can_be_reobserved(self):
        with patch(TRANSPORT, return_value=MemoryTransport()):
            c = self.client(); c.start()
        class PendingIO:
            alive = True
            ident = 123
            budgets = []
            def join(self, timeout): self.budgets.append(timeout)
            def is_alive(self): return self.alive
        pending = PendingIO(); c._io_threads.append({'thread':pending,'start_confirmed':True})
        first = c.close()
        self.assertFalse(first['io_threads_stopped']); self.assertFalse(first['cleanup_complete'])
        self.assertTrue(first['owned_job_empty']); self.assertFalse(first['descendants_stopped'])
        pending.alive = False
        self.assertTrue(c.close()['cleanup_complete'])
        self.assertTrue(all(0 <= budget <= 5 for budget in pending.budgets))
    def test_owner_failure_not_upgraded_by_reader_exit(self):
        transport = MemoryTransport()
        transport.close = lambda timeout: {'host_exited':None,'owned_job_empty':None,
            'handles_closed':False,'cleanup_complete':False,'code':'SYNTHETIC_UNKNOWN'}
        with patch(TRANSPORT, return_value=transport):
            c = self.client(); c.start()
        result = c.close()
        self.assertTrue(result['io_threads_stopped']); self.assertFalse(result['cleanup_complete'])
        self.assertIsNone(result['owned_job_empty'])
    def test_start_close_race_waits_for_launch_then_closes(self):
        transport = MemoryTransport(); launched = threading.Event(); release = threading.Event()
        original = transport.start
        def start(*args):
            launched.set()
            if not release.wait(2): raise RuntimeError('fixture deadline')
            return original(*args)
        transport.start = start
        c = self.client(); results = []
        with patch(TRANSPORT, return_value=transport):
            starter = threading.Thread(target=lambda:results.append(c.start()))
            closer = threading.Thread(target=lambda:results.append(c.close()))
            starter.start(); self.assertTrue(launched.wait(2)); closer.start()
            self.assertEqual(transport.closes, 0)
            release.set(); starter.join(2); closer.join(2)
        self.assertFalse(starter.is_alive()); self.assertFalse(closer.is_alive())
        self.assertEqual(transport.starts, 1); self.assertEqual(transport.closes, 1)
        self.assertTrue(any(r.get('cleanup_complete') for r in results))
    def test_interruption_after_native_thread_start_keeps_live_reader_registered(self):
        transport = MemoryTransport(); release = threading.Event(); entered = threading.Event()
        class BlockedReader:
            def readline(self, size):
                entered.set(); release.wait(10); return b''
        transport.stdout = BlockedReader()
        original_start = threading.Thread.start
        def interrupted_start(thread):
            original_start(thread)
            raise KeyboardInterrupt()
        calls = iter([0, 0, 6, 6, 6])
        c = self.client()
        try:
            with patch(TRANSPORT, return_value=transport), patch('threading.Thread.start', interrupted_start), \
                 patch('native_harness_api.workbuddy_acp.time.monotonic', side_effect=lambda:next(calls, 6)):
                with self.assertRaises(ACPError): c.start()
            self.assertTrue(entered.wait(1)); self.assertEqual(len(c._io_threads), 1)
            record = c._io_threads[0]
            self.assertFalse(record['start_confirmed']); self.assertTrue(record['thread'].is_alive())
            # No five-second delay in this deterministic failure snapshot.
            with patch('native_harness_api.workbuddy_acp.time.monotonic', side_effect=[0, 6, 6]):
                result = c.close()
            self.assertFalse(result['io_threads_stopped']); self.assertFalse(result['cleanup_complete'])
        finally:
            release.set()
        self.assertTrue(c.close()['cleanup_complete'])
    def test_failed_thread_start_is_unknown_not_success_or_join_exception(self):
        with patch(TRANSPORT, return_value=MemoryTransport()), \
             patch('threading.Thread.start', side_effect=RuntimeError('synthetic start failure')):
            c = self.client()
            with self.assertRaises(ACPError): c.start()
        self.assertEqual(len(c._io_threads), 1)
        self.assertIsNone(c._io_threads[0]['thread'].ident)
        result = c.close()
        self.assertFalse(result['io_threads_stopped']); self.assertFalse(result['cleanup_complete'])
        with self.assertRaises(ACPError): c.start()
    @unittest.skipIf(os.name == 'nt', 'Non-Windows guard')
    def test_unsupported_platform_never_uses_popen(self):
        with patch('native_harness_api.workbuddy_acp.subprocess.Popen') as popen:
            c = self.client()
            with self.assertRaises(ACPError): c.start()
        popen.assert_not_called()
        self.assertFalse(c.close()['cleanup_complete'])

@unittest.skipUnless(os.name == 'nt', 'Real Windows Job + synthetic ACP; NOT_RUN on this host')
class SupervisedACPWindowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ACP supervised 测试 ')
        self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name); self.count = self.root/'count'
    def ready(self, mode='ok', handler=None, descendant=False):
        peer = PEER.resolve()
        if descendant:
            wrapper = self.root/'synthetic wrapper.py'
            wrapper.write_text('import runpy, subprocess, sys\n'
                'child = subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"], '
                'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
                f'runpy.run_path({str(peer)!r}, run_name="__main__")\n', encoding='utf-8')
            peer = wrapper
        c = ACPClient([sys.executable,str(peer),mode,str(self.count)], self.root,
            dict(os.environ), timeout=8, permission_handler=handler, supervised=True)
        self.addCleanup(c.close); c.start(); c.initialize(); c.new_session(); c.set_model('fake-a')
        return c
    def test_turn_and_job_cleanup(self):
        c = self.ready(); result = c.prompt('synthetic', lambda:None)
        self.assertEqual(result['text'], 'synthetic answer'); self.assertEqual(self.count.read_text(), '1')
        evidence = c.close()
        for field in ('host_exited','owned_job_empty','handles_closed','io_threads_stopped','cleanup_complete'):
            self.assertIs(evidence[field], True, field)
        self.assertFalse(evidence['descendants_stopped'])
        with self.assertRaises(ACPError): c.prompt('again', lambda:None)
    def test_turn_completion_does_not_hide_live_owned_descendant(self):
        c = self.ready(descendant=True)
        self.assertGreaterEqual(c._transport._owner.active_processes(), 2)
        c.prompt('synthetic', lambda:None)
        self.assertFalse(c._transport.observe()['owned_job_empty'])
        evidence = c.close()
        self.assertTrue(evidence['owned_job_empty']); self.assertTrue(evidence['cleanup_complete'])
        self.assertFalse(evidence['descendants_stopped'])
    def test_permission_stays_per_request(self):
        decisions = []
        c = self.ready('permission', lambda value:(decisions.append(value) or 'reject_once'))
        self.assertEqual(c.prompt('synthetic', lambda:None)['permissions'], ['reject_once'])
        self.assertEqual(len(decisions), 1); self.assertTrue(c.close()['cleanup_complete'])
    def test_timeout_never_replays(self):
        c = self.ready('timeout'); c.deadline = time.monotonic()+.3
        with self.assertRaises(ACPError): c.prompt('synthetic', lambda:None)
        self.assertTrue(c.close()['cleanup_complete'])
        with self.assertRaises(ACPError): c.prompt('synthetic', lambda:None)
        self.assertEqual(self.count.read_text(), '1')
    def test_failed_intent_no_prompt(self):
        c = self.ready()
        def fail(): raise RuntimeError('synthetic')
        with self.assertRaises(ACPError): c.prompt('synthetic', fail)
        self.assertFalse(self.count.exists()); self.assertTrue(c.close()['cleanup_complete'])

if __name__ == '__main__': unittest.main()
