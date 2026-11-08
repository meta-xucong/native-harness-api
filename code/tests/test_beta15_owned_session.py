"""Owned engine against only a self-authored Node child, never a vendor import."""
import os
import json
import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from native_harness_api.beta15_owned_session import OwnedSessionEngine, SHUTDOWN_ID
from native_harness_api.supervised_acp_transport import SupervisedACPTransport

CHILD = Path(__file__).parent / 'fakes' / 'beta15_shim_child.mjs'
NODE = shutil.which('node')

class SyntheticHostTransport:
    """Test-only single host transport, NOT production descendant-stop evidence."""
    def __init__(self): self.process=None; self.starts=0
    def start(self, command, cwd, env):
        self.starts+=1
        self.process=subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        self.stdin,self.stdout,self.stderr=self.process.stdin,self.process.stdout,self.process.stderr
    def observe(self):
        exited=self.process.poll() is not None
        return {'host_exited':exited,'owned_job_empty':exited,'host_exit_code':self.process.returncode}
    def close(self, timeout=5):
        if self.process.poll() is None:self.process.kill()
        self.process.wait(timeout=max(.1,timeout))
        for stream in (self.stdin,self.stdout,self.stderr):stream.close()
        return {'host_exited':True,'owned_job_empty':True,'handles_closed':True,'cleanup_complete':True}

# Test hygiene must not rewrite the result under test or relaunch its child.
_CLEANUP_KEYS = ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete', 'io_threads_stopped')

def _cleanup_projection(value):
    value = value if type(value) is dict else {}
    return {key: value.get(key) if type(value.get(key)) is bool else None for key in _CLEANUP_KEYS}


def finish_synthetic_fixture(folder, transport, original, *, mode="synthetic", owned=False, clock=time.monotonic,
                             sleep=time.sleep, emit=print):
    """Same-owner hygiene only; original observations remain authoritative."""
    modes = {'synthetic', 'success', 'orphan', 'exit_nonzero', 'wrong_model', 'ack_only',
             'bad_frame', 'oversized', 'stderr_overflow', 'shutdown_hang', 'late_conflict'}
    label = mode if type(mode) is str and mode in modes else 'unspecified'
    result = original if type(original) is dict else {}
    before = _cleanup_projection(result.get('cleanup'))
    # No arbitrary exception, frame, path, text, prompt or stderr is printed.
    faults = {'CLEANUP_UNCONFIRMED', 'CLOSE_UNCONFIRMED', 'EXECUTION_UNCONFIRMED',
              'TURN_NOT_COMPLETED', 'TURN_INDETERMINATE', 'INVALID_FRAME',
              'FRAME_LIMIT', 'OUTPUT_LIMIT', 'STDERR_LIMIT', 'SEND_UNCONFIRMED',
              'READ_FAILED', 'WRITE_FAILED', 'CANCEL_REQUESTED', 'SHIM_FAULT'}
    fault = result.get('fault')
    summary = {'fixture': 'synthetic-owned-cleanup', 'mode': label,
               'owned': owned is True, 'original_cleanup': before,
               'original_fault': fault if fault is None or fault in faults else 'OTHER',
               'original_outcome': result.get('outcome') if result.get('outcome') in
                   ('succeeded', 'indeterminate', 'cancelled', 'pending') else 'unknown',
               'observe_failed': False, 'hygiene_attempted': False,
               'hygiene_failed': False, 'folder_attempts': 0, 'folder_failed': False}
    try:
        # May be cached closed facts, not a fresh OS observation or new proof.
        observed = transport.observe()
        summary['observed'] = _cleanup_projection(observed)
    except Exception:
        summary['observe_failed'] = True
    if not all(before[key] is True for key in _CLEANUP_KEYS):
        summary['hygiene_attempted'] = True
        try:
            summary['hygiene_cleanup'] = _cleanup_projection(transport.close(timeout=2))
            summary['hygiene_failed'] = not all(summary['hygiene_cleanup'][key] is True
                for key in _CLEANUP_KEYS if key != 'io_threads_stopped')
        except Exception:
            summary['hygiene_failed'] = True
    deadline = clock() + 2
    while True:
        summary['folder_attempts'] += 1
        try:
            folder.cleanup()
            break
        except OSError as error:
            retryable = isinstance(error, PermissionError) and getattr(error, 'winerror', None) in (32, 5)
            remaining = deadline - clock()
            if not retryable or remaining <= 0 or summary['folder_attempts'] >= 41:
                summary['folder_failed'] = True
                break
            sleep(min(.05, remaining))
        except Exception:
            summary['folder_failed'] = True
            break
    encoded = json.dumps(summary, sort_keys=True, separators=(',', ':'))
    emit(encoded)
    if summary['hygiene_failed'] or summary['folder_failed']:
        raise AssertionError('SYNTHETIC_FIXTURE_CLEANUP_FAILED ' + encoded) from None
    return summary


class FixtureCleanupTests(unittest.TestCase):
    def fixture(self, failures=0, *, permanent=False, close_error=False, folder_error=False):
        class Clock:
            value = 0
            def now(self): return self.value
            def sleep(self, seconds): self.value += seconds
        clock = Clock()
        class Folder:
            calls = 0
            def cleanup(self):
                self.calls += 1
                if folder_error: raise RuntimeError('PRIVATE')
                if permanent or self.calls <= failures:
                    error = PermissionError('PRIVATE'); error.winerror = 32; raise error
        class Transport:
            closes = 0
            def observe(self): return {'host_exited': True, 'owned_job_empty': False}
            def close(self, timeout):
                self.closes += 1
                if close_error: raise RuntimeError('PRIVATE')
                return {key: True for key in _CLEANUP_KEYS}
        return Folder(), Transport(), clock

    def test_hygiene_does_not_upgrade_original_false(self):
        folder, transport, clock = self.fixture()
        original = {'cleanup': {'owned_job_empty': False}, 'fault': 'CLEANUP_UNCONFIRMED',
                    'outcome': 'indeterminate', 'session': {'text': 'PRIVATE'}}
        saved = copy.deepcopy(original); lines = []
        summary = finish_synthetic_fixture(folder, transport, original, mode='PRIVATE_LABEL', owned=True, clock=clock.now, sleep=clock.sleep, emit=lines.append)
        self.assertEqual(original, saved); self.assertEqual(transport.closes, 1)
        self.assertFalse(summary['original_cleanup']['owned_job_empty'])
        self.assertTrue(summary['hygiene_cleanup']['owned_job_empty'])
        self.assertNotIn('PRIVATE', ''.join(lines))
        self.assertEqual(summary['mode'], 'unspecified'); self.assertTrue(summary['owned'])

    def test_folder_release_tail_retries_only_cleanup(self):
        folder, transport, clock = self.fixture(failures=2)
        original = {'cleanup': {key: True for key in _CLEANUP_KEYS}}
        summary = finish_synthetic_fixture(folder, transport, original, clock=clock.now, sleep=clock.sleep, emit=lambda _: None)
        self.assertEqual(summary['folder_attempts'], 3); self.assertEqual(transport.closes, 0)

    def test_permanent_lock_bounded_with_original_diagnostic(self):
        folder, transport, clock = self.fixture(permanent=True)
        with self.assertRaises(AssertionError) as caught:
            finish_synthetic_fixture(folder, transport, {'cleanup': {'owned_job_empty': False}}, clock=clock.now, sleep=clock.sleep, emit=lambda _: None)
        self.assertLessEqual(folder.calls, 41); self.assertLessEqual(clock.value, 2)
        self.assertIn('"owned_job_empty":false', str(caught.exception)); self.assertNotIn('PRIVATE', str(caught.exception))

    def test_nonsharing_permission_error_is_not_retried(self):
        folder, transport, clock = self.fixture()
        calls = []
        def denied():
            calls.append(True)
            error = PermissionError('PRIVATE'); error.winerror = 1314; raise error
        folder.cleanup = denied
        with self.assertRaises(AssertionError):
            finish_synthetic_fixture(folder, transport, {}, clock=clock.now, sleep=clock.sleep, emit=lambda _: None)
        self.assertEqual(len(calls), 1); self.assertEqual(clock.value, 0)

    def test_cleanup_exceptions_are_not_swallowed(self):
        for kwargs in ({'close_error': True}, {'folder_error': True}):
            folder, transport, clock = self.fixture(**kwargs)
            with self.assertRaises(AssertionError):
                finish_synthetic_fixture(folder, transport, {}, clock=clock.now, sleep=clock.sleep, emit=lambda _: None)
            self.assertEqual(folder.calls, 1)

@unittest.skipUnless(NODE,'Node needed for self-authored child')
class OwnedEngineTests(unittest.TestCase):
    def run_mode(self, mode='success', *, owned=False, timeout=15,cleanup_timeout=2, transport_override=None, engine_type=OwnedSessionEngine):
        folder = tempfile.TemporaryDirectory()
        path = Path(folder.name).resolve()
        env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC')}
        env['SHIM_FIXTURE_MODE']=mode
        transport=transport_override or (SupervisedACPTransport() if owned else SyntheticHostTransport())
        engine=engine_type(transport)
        retained = {'result': None}
        # Runs after caller assertions; a directory-sharing error cannot prevent
        # the test from seeing the ORIGINAL engine cleanup evidence.
        self.addCleanup(lambda: finish_synthetic_fixture(folder, transport, retained['result'], mode=mode, owned=owned))
        result=engine.run([NODE,str(CHILD.resolve())],path,env,workspace=str(path),
                          model_id='synthetic-b',prompt='synthetic prompt',timeout=timeout,
                          cleanup_timeout=cleanup_timeout)
        retained['result'] = copy.deepcopy(result)
        if mode=='orphan':
            self.assertTrue((path/'synthetic-orphan.ready').is_file(),result)
        return result,engine,transport

    def test_shutdown_ack_requires_actual_boolean_and_issued_intent(self):
        for issued, closed in ((False, True), (True, 1), (True, 'true')):
            e=OwnedSessionEngine(SyntheticHostTransport());e._shutdown_issued=issued
            e._dispatch(('{"id":%d,"result":{"closed":%s}}' % (SHUTDOWN_ID, __import__('json').dumps(closed))).encode())
            self.assertFalse(e.shutdown_ack)
            self.assertEqual(e.fault,'INVALID_SHUTDOWN_RESPONSE')

    def test_pre_cancel_does_not_launch_or_cleanup(self):
        t=SyntheticHostTransport();e=OwnedSessionEngine(t)
        cancelled=threading.Event();cancelled.set()
        result=e.run([],None,{},workspace='',model_id='',prompt='',cancellation=cancelled)
        self.assertEqual(t.starts,0)
        self.assertEqual(result['outcome'],'cancelled')
        self.assertFalse(result['launch_attempted'])
        self.assertTrue(result['cleanup_not_required'])
        self.assertEqual(result['liveness'],'not_started')

    def test_cleanup_timeout_contract_checked_before_start(self):
        t=SyntheticHostTransport(); e=OwnedSessionEngine(t)
        with self.assertRaises(ValueError):
            e.run([],None,{},workspace='',model_id='',prompt='',cleanup_timeout=301)
        self.assertEqual(t.starts,0)

    def test_exited_host_without_pipe_eof_is_not_graceful(self):
        class DelayedEOFTransport(SyntheticHostTransport):
            def start(self,*args):
                super().start(*args)
                self.release=threading.Event(); stream=self.stdout; release=self.release
                class Wrapper:
                    hold=False
                    def readline(self,size):
                        if self.hold: release.wait(3)
                        value=stream.readline(size)
                        if b'"closed":true' in value: self.hold=True
                        return value
                    def close(self): stream.close()
                self.stdout=Wrapper()
            def close(self,timeout=5):
                self.release.set()
                return super().close(timeout)
        result,_,_=self.run_mode(transport_override=DelayedEOFTransport(),cleanup_timeout=.3)
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertFalse(result['natural_owned_exit'])

    def test_js_dispatcher_fixtures(self):
        for name in ('beta15_command_shim_test.mjs','beta15_external_runtime_test.mjs'):
            result=subprocess.run([NODE,str(CHILD.parent / name)],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf8',errors='replace'))

    def test_success_correlates_and_naturally_closes(self):
        result,engine,transport=self.run_mode()
        self.assertEqual(result['outcome'],'succeeded',result)
        self.assertTrue(result['natural_owned_exit'])
        self.assertTrue(result['cleanup']['io_threads_stopped'])
        self.assertEqual(result['session']['turn']['text'],'synthetic answer')
        self.assertFalse(result['retry_safe']);self.assertEqual(result['liveness'],'unknown')
        with self.assertRaises(ValueError):engine.run([],None,{},workspace='',model_id='',prompt='')
        self.assertEqual(transport.starts,1)

    def test_nonzero_exit_after_valid_shutdown_not_success(self):
        result,_,_=self.run_mode('exit_nonzero')
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertEqual(result['host_exit_code_before_cleanup'],7)
        self.assertTrue(result['natural_owned_exit'])
        self.assertFalse(result['natural_exit_zero'])

    def test_wrong_model_never_prompts(self):
        result,_,_=self.run_mode('wrong_model')
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertFalse(result['session']['prompt_intent_issued'])
        self.assertTrue(result['cleanup']['cleanup_complete'])

    def test_ack_is_not_completion_timeout(self):
        class ExpireAfterAcknowledgment(OwnedSessionEngine):
            def _dispatch(self,raw):
                super()._dispatch(raw)
                if self.coordinator.reducer is not None and self.coordinator.reducer.insert_acknowledged:
                    self.coordinator.deadline=time.monotonic()-1
        result,_,_=self.run_mode('ack_only',engine_type=ExpireAfterAcknowledgment)
        self.assertEqual(result['outcome'],'indeterminate',result)
        self.assertIsNotNone(result['session']['turn'],result)
        self.assertTrue(result['session']['turn']['insert_acknowledged'],result)
        self.assertFalse(result['retry_safe'])

    def test_duplicate_json_keys_fail(self):
        result,_,_=self.run_mode('bad_frame')
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertEqual(result['fault'],'INVALID_FRAME')

    def test_oversized_frame_is_rejected(self):
        result,_,_=self.run_mode('oversized')
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertEqual(result['fault'],'FRAME_LIMIT')

    def test_missing_exit_code_cannot_claim_clean_exit(self):
        class NoCode(SyntheticHostTransport):
            def observe(self):
                result=super().observe();result.pop('host_exit_code');return result
        result,_,_=self.run_mode(transport_override=NoCode())
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertFalse(result['natural_exit_zero'])

    def test_stderr_budget_is_not_exposed(self):
        result,_,_=self.run_mode('stderr_overflow')
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertNotIn('xxxxx',str(result))

    def test_hung_shutdown_forced_cleanup_not_success(self):
        result,_,_=self.run_mode('shutdown_hang',cleanup_timeout=.5)
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertFalse(result['natural_owned_exit'])
        self.assertTrue(result['cleanup']['cleanup_complete'])

    def test_late_conflicting_terminal_cannot_hide_behind_close(self):
        result,_,_=self.run_mode('late_conflict')
        self.assertEqual(result['outcome'],'indeterminate')

    @unittest.skipUnless(os.name=='nt','Actual owned Job requires Windows')
    def test_actual_windows_orphan_requires_forced_cleanup(self):
        result,_,_=self.run_mode('orphan',owned=True,cleanup_timeout=4)
        self.assertEqual(result['outcome'],'indeterminate')
        self.assertFalse(result['natural_owned_exit'])
        self.assertTrue(result['cleanup']['owned_job_empty'])
        self.assertTrue(result['cleanup']['cleanup_complete'])

    @unittest.skipUnless(os.name=='nt','Actual owned Job requires Windows')
    def test_actual_windows_owned_job_with_synthetic_child(self):
        result,_,_=self.run_mode(owned=True)
        self.assertEqual(result['outcome'],'succeeded',result)
        self.assertTrue(result['cleanup']['owned_job_empty'])
        self.assertTrue(result['cleanup']['io_threads_stopped'])
