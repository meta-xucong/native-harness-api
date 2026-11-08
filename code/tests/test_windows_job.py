"""Real Win32 acceptance tests: skipped, not simulated, on other platforms.

Failure tests inject membership/resume failure before a real suspended process
can run. No mock return value is used to claim successful Win32 supervision.
"""
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
import time
import unittest
from native_harness_api.windows_job import OwnedWindowsJob, WindowsJobError, UnsupportedPlatform, _timeout

PEER = Path(__file__).parent/'fakes'/'windows_job_tree.py'


class PortableJobTests(unittest.TestCase):
    def test_non_windows_import_safe_and_spawn_rejected(self):
        if os.name == 'nt':
            self.skipTest('Non-Windows import guard only')
        with self.assertRaises(UnsupportedPlatform):
            OwnedWindowsJob.spawn([sys.executable], Path.cwd(), dict(os.environ))

    def test_timeout_validation(self):
        for value in (True, -1, 301, float('nan'), float('inf'), '1'):
            with self.assertRaises(ValueError):_timeout(value)
        self.assertEqual(_timeout(0), 0)


class PortableWaitAlgorithmTests(unittest.TestCase):
    """Fake API algorithm contracts only; never evidence of Win32 execution."""
    def owner(self, statuses, counts=(0,), *, query_failure=False):
        from native_harness_api.windows_job import _Accounting
        owner = object.__new__(OwnedWindowsJob)
        owner._state_lock = threading.RLock()
        owner._closed = owner._empty_observed = False
        owner._assigned = True
        owner._tracking_lock = threading.RLock()
        owner._known = {(202, 1): 202}
        owner._unclassified = set()
        owner._tracking_fault = None
        owner._tracker_stop = threading.Event()
        owner._last_total = 0
        owner._stop_proven = False
        owner._job, owner._process = 101, 202
        owner._error = lambda code: WindowsJobError(code, owner=owner)
        class API:
            waits = 0
            queries = 0
            def TerminateJobObject(self, handle, code):
                return 1
            def QueryInformationJobObject(self, handle, kind, info, size, returned):
                if query_failure:
                    return 0
                if kind == 3:
                    ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32))[0] = 0
                    ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32))[1] = 0
                    return 1
                ctypes.cast(info, ctypes.POINTER(_Accounting)).contents.TotalProcesses = 1
                count = counts[min(self.queries, len(counts)-1)]
                self.queries += 1
                ctypes.cast(info, ctypes.POINTER(_Accounting)).contents.ActiveProcesses = count
                return 1
            def WaitForSingleObject(self, handle, milliseconds):
                status = statuses[min(self.waits, len(statuses)-1)]
                self.waits += 1
                return status
        owner._api = API()
        return owner

    def clock(self):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        return now, sleep

    def test_accounting_zero_waits_for_retained_host_signal(self):
        owner = self.owner((258, 258, 0))
        now, sleep = self.clock()
        with patch('native_harness_api.windows_job.time.monotonic', side_effect=lambda: now[0]), patch('native_harness_api.windows_job.time.sleep', side_effect=sleep):
            self.assertEqual(owner.terminate(.1), {'owned_job_empty': True})
        self.assertGreaterEqual(owner._api.waits, 3)
        self.assertGreater(now[0], 0)
        self.assertTrue(owner.host_exited())
        self.assertTrue(owner._empty_observed)

    def test_unsignalled_host_times_out_retaining_ownership(self):
        owner = self.owner((258,))
        now, sleep = self.clock()
        with patch('native_harness_api.windows_job.time.monotonic', side_effect=lambda: now[0]), patch('native_harness_api.windows_job.time.sleep', side_effect=sleep):
            self.assertEqual(owner.terminate(.05), {'owned_job_empty': False})
        self.assertAlmostEqual(now[0], .05)
        self.assertFalse(owner._empty_observed)
        self.assertEqual((owner._job, owner._process), (101, 202))
        self.assertFalse(owner._closed)

    def test_query_or_wait_failure_is_not_empty_success(self):
        owner = self.owner((0,), query_failure=True)
        self.assertFalse(owner.terminate(0)['owned_job_empty'])
        owner = self.owner((0xFFFFFFFF,))
        with self.assertRaises(WindowsJobError): owner.terminate(0)
        self.assertFalse(owner._empty_observed)
        self.assertEqual((owner._job, owner._process), (101, 202))

    def test_host_signal_alone_does_not_prove_job_empty(self):
        owner = self.owner((0,), counts=(1,))
        self.assertEqual(owner.terminate(0), {'owned_job_empty': False})
        self.assertFalse(owner._empty_observed)


class PortableTrackingTests(unittest.TestCase):
    """Object-lifetime/census algorithm tests, not Windows runtime evidence."""
    def owner(self):
        from native_harness_api.windows_job import _Accounting, _FileTime, SIZE_T, DWORD, LPVOID
        class API:
            pids = []
            total = 1
            active = 0
            statuses = {100: 0}
            identities = {100: (10, 1)}
            opens = {10: 100}
            members = {100: True}
            closed = []
            fail_close = None
            def QueryInformationJobObject(self, job, kind, ptr, size, returned):
                if kind == 1:
                    info = ctypes.cast(ptr, ctypes.POINTER(_Accounting)).contents
                    info.TotalProcesses, info.ActiveProcesses = self.total, self.active
                else:
                    head = ctypes.cast(ptr, ctypes.POINTER(DWORD))
                    head[0] = head[1] = len(self.pids)
                    array = ctypes.cast(ctypes.addressof(ptr._obj) + 8, ctypes.POINTER(SIZE_T))
                    for i, pid in enumerate(self.pids): array[i] = pid
                return 1
            def WaitForSingleObject(self, handle, ms): return self.statuses.get(handle, 0xffffffff)
            def OpenProcess(self, rights, inherit, pid):
                self.last_rights = rights, inherit
                return self.opens.get(pid)
            def IsProcessInJob(self, handle, job, ptr):
                ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int32)).contents.value = self.members.get(handle, False)
                return 1
            def GetProcessTimes(self, handle, created, *unused):
                if handle not in self.identities: return 0
                ctypes.cast(created, ctypes.POINTER(_FileTime)).contents.low = self.identities[handle][1]
                return 1
            def TerminateJobObject(self, *args): return 1
            def CloseHandle(self, handle):
                if handle == self.fail_close: return 0
                self.closed.append(handle); return 1
        with patch('native_harness_api.windows_job._Win32', return_value=API()):
            owner = OwnedWindowsJob()
        owner._job, owner._process, owner.pid = 999, 100, 10
        owner._assigned = True
        owner._error = lambda code: WindowsJobError(code, owner=owner)
        owner._retain_process(100, 10, host=True)
        return owner

    def add_child(self, owner, *, pid=20, handle=200, created=2, status=258):
        api = owner._api
        # Avoid sharing the fake class defaults across owner instances.
        api.statuses = {**api.statuses, handle: status}
        api.identities = {**api.identities, handle: (pid, created)}
        api.opens = {**api.opens, pid: handle}
        api.members = {**api.members, handle: True}
        api.total += 1
        owner._capture_pid(pid)

    def test_accounting_zero_is_insufficient_until_every_object_signaled(self):
        owner = self.owner(); self.add_child(owner)
        self.assertFalse(owner.wait_empty(0)['owned_job_empty'])
        owner._api.statuses[200] = 0
        self.assertTrue(owner.wait_empty(0)['owned_job_empty'])

    def test_uncaptured_short_lived_birth_never_upgraded_by_empty_snapshots(self):
        owner = self.owner(); owner._api.total = 2
        for _ in range(3): self.assertFalse(owner.wait_empty(0)['owned_job_empty'])
        with self.assertRaises(WindowsJobError): owner.close(0)
        self.assertEqual(owner._process, 100); self.assertEqual(owner._job, 999)
        self.assertFalse(owner._closed)

    def test_snapshot_catches_new_birth_but_final_counter_catches_gap(self):
        owner = self.owner(); self.add_child(owner, status=0)
        original = owner._api.QueryInformationJobObject
        calls = [0]
        def query(*args):
            if args[1] == 1:
                calls[0] += 1
                if calls[0] == 2: owner._api.total += 1
            return original(*args)
        owner._api.QueryInformationJobObject = query
        self.assertFalse(owner.wait_empty(0)['owned_job_empty'])

    def test_creation_identity_distinguishes_reused_numeric_pid(self):
        owner = self.owner(); self.add_child(owner, status=0)
        self.add_child(owner, pid=20, handle=201, created=3, status=0)
        self.assertEqual(len(owner._known), 3)
        self.assertTrue(owner.wait_empty(0)['owned_job_empty'])
        self.assertEqual(owner._api.last_rights, (0x101000, False))

    def test_foreign_member_and_open_failure_cannot_become_exit_proof(self):
        owner = self.owner(); owner._api.pids = [20]; owner._api.total = 2
        self.assertFalse(owner.wait_empty(0)['owned_job_empty'])
        self.add_child(owner, status=0)
        owner._api.pids = []
        owner._api.total = 2
        # A captured object can recover a transient snapshot open failure only
        # because exact lifetime census subsequently covers every process.
        self.assertTrue(owner.wait_empty(0)['owned_job_empty'])
        owner = self.owner(); owner._api.opens = {20: 200}; owner._api.members = {200: False}
        with self.assertRaises(WindowsJobError): owner._capture_pid(20)
        self.assertIn(200, owner._unclassified)
        self.assertFalse(owner.wait_empty(0)['owned_job_empty'])

    def test_total_regression_and_limit_are_rejected(self):
        for total in (0, 4097):
            owner = self.owner(); owner._last_total = 1; owner._api.total = total
            with self.assertRaises(WindowsJobError): owner.wait_empty(0)
            self.assertFalse(owner._empty_observed)

    def test_invalid_duplicate_pid_list_is_not_complete(self):
        for pids in ([0], [20, 20]):
            owner = self.owner(); owner._api.pids = pids
            self.assertFalse(owner.wait_empty(0)['owned_job_empty'])

    def test_partial_handle_close_failure_keeps_proof_and_remaining_handles(self):
        owner = self.owner(); self.add_child(owner, status=0)
        owner._api.fail_close = 200
        with self.assertRaises(WindowsJobError): owner.close(.1)
        self.assertTrue(owner._stop_proven); self.assertIn((20, 2), owner._known)
        self.assertEqual(owner._job, 999); self.assertFalse(owner._closed)
        owner._api.fail_close = None
        self.assertTrue(owner.close(.1)['owned_job_empty'])
        self.assertTrue(owner._closed); self.assertFalse(owner._known)

    def test_tracker_only_new_message_captures_and_wrong_key_fails_closed(self):
        from native_harness_api.windows_job import DWORD, SIZE_T, LPVOID
        for message, key, expected in ((6, 1, [20]), (7, 1, []), (6, 2, [])):
            owner = self.owner(); captured = []
            owner._capture_pid = captured.append
            def dequeue(port, msg, ckey, value, timeout):
                ctypes.cast(msg, ctypes.POINTER(DWORD)).contents.value = message
                ctypes.cast(ckey, ctypes.POINTER(SIZE_T)).contents.value = key
                ctypes.cast(value, ctypes.POINTER(LPVOID)).contents.value = 20
                owner._tracker_stop.set()
                return 1
            owner._api.GetQueuedCompletionStatus = dequeue
            owner._track_processes()
            self.assertEqual(captured, expected)
            self.assertEqual(owner._tracking_fault is not None, key != 1)

    def test_tracker_open_failure_is_sticky_even_after_empty_census(self):
        from native_harness_api.windows_job import DWORD, SIZE_T, LPVOID
        owner = self.owner()
        def dequeue(port, msg, key, value, timeout):
            ctypes.cast(msg, ctypes.POINTER(DWORD)).contents.value = 6
            ctypes.cast(key, ctypes.POINTER(SIZE_T)).contents.value = 1
            ctypes.cast(value, ctypes.POINTER(LPVOID)).contents.value = 20
            return 1
        owner._api.GetQueuedCompletionStatus = dequeue
        owner._track_processes()
        self.assertIsNotNone(owner._tracking_fault)
        self.assertFalse(owner.wait_empty(0)['owned_job_empty'])

    def test_process_list_grows_on_more_data_and_rejects_oversize(self):
        from native_harness_api.windows_job import DWORD, SIZE_T
        owner = self.owner(); calls = []
        def query(job, kind, ptr, size, returned):
            calls.append(size)
            head = ctypes.cast(ptr, ctypes.POINTER(DWORD)); head[0], head[1] = 40, 0
            if len(calls) == 1: return 0
            head[1] = 40
            array = ctypes.cast(ctypes.addressof(ptr._obj) + 8, ctypes.POINTER(SIZE_T))
            for i in range(40): array[i] = i + 1
            return 1
        owner._api.QueryInformationJobObject = query
        with patch('native_harness_api.windows_job.C.get_last_error', return_value=234, create=True):
            self.assertEqual(owner._current_pids(), list(range(1, 41)))
        self.assertGreater(calls[1], calls[0])
        def too_many(job, kind, ptr, size, returned):
            ctypes.cast(ptr, ctypes.POINTER(DWORD))[0] = 4097
            return 0
        owner._api.QueryInformationJobObject = too_many
        with self.assertRaises(WindowsJobError): owner._current_pids()

    def test_tracker_join_timeout_retains_handles_for_same_owner_retry(self):
        owner = self.owner()
        class Thread:
            ident = 1
            alive = True
            def join(self, timeout): self.timeout = timeout
            def is_alive(self): return self.alive
        owner._tracker = Thread()
        with self.assertRaisesRegex(WindowsJobError, 'TRACKER_STOP_UNCONFIRMED'): owner.close(0)
        self.assertFalse(owner._stop_proven); self.assertEqual(owner._process, 100)
        owner._tracker.alive = False
        self.assertTrue(owner.close(.1)['owned_job_empty'])

    def test_tracker_worker_host_duplicate_uses_original_retained_host(self):
        owner = self.owner()
        owner._known = {(10, 1): 200}
        owner._api.identities = {100: (10, 1), 200: (10, 1)}
        owner._api.members = {100: True, 200: True}
        owner._retain_process(100, 10, host=True)
        self.assertEqual(owner._known, {(10, 1): 100})
        self.assertIn(200, owner._api.closed)

    def test_message_overflow_is_sticky_failure(self):
        from native_harness_api.windows_job import DWORD, SIZE_T
        owner = self.owner(); owner.MAX_TRACKING_MESSAGES = 2
        def dequeue(port, message, key, value, timeout):
            ctypes.cast(message, ctypes.POINTER(DWORD)).contents.value = 7
            ctypes.cast(key, ctypes.POINTER(SIZE_T)).contents.value = 1
            return 1
        owner._api.GetQueuedCompletionStatus = dequeue
        owner._track_processes()
        self.assertTrue(owner._tracker_stop.is_set())
        self.assertFalse(owner.wait_empty(0)['owned_job_empty'])

    def test_port_is_associated_before_tracker_consumer_runs(self):
        owner = self.owner(); calls = []
        owner._api.CreateIoCompletionPort = lambda *args: calls.append('create') or 444
        owner._api.SetInformationJobObject = lambda job, kind, *args: calls.append(('associate', job, kind)) or 1
        def consume(): calls.append('consume')
        owner._track_processes = consume
        owner._start_tracking(); owner._stop_tracking(time.monotonic() + 1)
        self.assertEqual(calls, ['create', ('associate', 999, 7), 'consume'])


@unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Object APIs; NOT VERIFIED on this host')
class WindowsJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='job Unicode 测试 ')
        self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.owners=[]
        self.addCleanup(self.cleanup_jobs)
    def cleanup_jobs(self):
        for owner in self.owners:owner.close(timeout=5)
    def spawn(self, mode='parent', *extra, cls=OwnedWindowsJob):
        owner=cls.spawn([sys.executable,str(PEER.resolve()),mode,str(self.root),*extra], self.root, dict(os.environ))
        self.owners.append(owner)
        return owner
    def wait_file(self, name):
        deadline=time.monotonic()+5
        while not (self.root/name).exists():
            if time.monotonic()>deadline:self.fail('Synthetic child did not become ready')
            time.sleep(.01)
        return self.root/name
    def await_host_exit(self, owner):
        deadline=time.monotonic()+5
        while not owner.host_exited():
            if time.monotonic()>deadline:self.fail('Synthetic host did not exit')
            time.sleep(.01)
    def test_parent_child_grandchild_share_job_and_terminate(self):
        owner=self.spawn();self.wait_file('parent.ready')
        self.assertEqual(owner.active_processes(),3)
        self.assertFalse(owner.wait_empty(.05)['owned_job_empty'])
        self.assertTrue(owner.terminate(5)['owned_job_empty'])
        self.assertTrue(owner.host_exited())
        self.assertEqual(owner.close(),{'owned_job_empty':True})
    def test_parent_exit_does_not_mean_job_empty(self):
        owner=self.spawn('orphan_parent');self.wait_file('orphan_parent.ready')
        self.await_host_exit(owner)
        self.assertEqual(owner.active_processes(),2)
        self.assertFalse(owner.wait_empty(.05)['owned_job_empty'])
        self.assertTrue(owner.close(5)['owned_job_empty'])
    def test_close_terminates_live_tree_and_is_idempotent(self):
        owner=self.spawn();self.wait_file('parent.ready')
        self.assertTrue(owner.close()['owned_job_empty'])
        self.assertTrue(owner.close()['owned_job_empty'])
        self.assertIsNone(owner._job);self.assertIsNone(owner._process)
        self.assertIsNone(owner.stdin);self.assertEqual(owner._raw,set())
    def test_last_job_handle_close_kills_children(self):
        # Keep process handles opened while their identities are alive, then
        # observe those same objects, never a later PID lookup after reuse.
        from native_harness_api.windows_job import HANDLE, DWORD, BOOL
        owner=self.spawn();self.wait_file('parent.ready')
        api=owner._api
        open_process=api.dll.OpenProcess
        open_process.restype=HANDLE;open_process.argtypes=[DWORD,BOOL,DWORD]
        handles=[]
        try:
            for name in ('parent','child','grandchild'):
                pid=int((self.root/(name+'.pid')).read_text())
                handle=open_process(0x00100000,False,pid)  # SYNCHRONIZE only.
                self.assertTrue(handle);handles.append(handle)
            self.assertTrue(api.CloseHandle(owner._job))
            owner._job=None;owner._assigned=False
            for handle in handles:self.assertEqual(api.WaitForSingleObject(handle,5000),0)
            # Lost job handle means no query-based emptiness claim, even though
            # test independently witnessed the known process objects exit.
            self.assertFalse(owner.close()['owned_job_empty'])
        finally:
            for handle in handles:api.CloseHandle(handle)
    def test_no_breakaway(self):
        owner=self.spawn('breakaway')
        value=json.loads(self.wait_file('breakaway.json').read_text())
        self.assertEqual(value['ordinary_exit_code'],0)
        self.assertTrue(value['ordinary_marker'])
        self.assertFalse(value['escaped'])
        self.assertEqual(value['winerror'],5)  # ERROR_ACCESS_DENIED, not any OSError.
        self.assertFalse((self.root/'executed').exists())
        self.assertTrue(owner.wait_empty(5)['owned_job_empty'])
    def test_membership_failure_never_runs_suspended_child(self):
        class RefuseAssign(OwnedWindowsJob):
            def _verify_membership(self):raise WindowsJobError('INJECTED_MEMBERSHIP_FAILURE',owner=self)
        with self.assertRaises(WindowsJobError) as exc:self.spawn('marker',cls=RefuseAssign)
        self.assertEqual(exc.exception.code,'INJECTED_MEMBERSHIP_FAILURE')
        self.assertTrue(exc.exception.owner._closed)
        self.assertFalse((self.root/'executed').exists())
    def test_unsupported_job_list_never_creates_process_or_falls_back(self):
        class UnsupportedJobList(OwnedWindowsJob):
            def _install_job_attribute(self, storage, jobs):
                raise WindowsJobError('JOB_LIST_UNSUPPORTED_OR_FAILED',owner=self)
        with self.assertRaises(WindowsJobError) as exc:
            self.spawn('marker',cls=UnsupportedJobList)
        self.assertEqual(exc.exception.code,'JOB_LIST_UNSUPPORTED_OR_FAILED')
        self.assertIsNone(exc.exception.owner.pid)
        self.assertTrue(exc.exception.owner._closed)
        self.assertFalse((self.root/'executed').exists())
    def test_exact_owned_job_membership_precedes_resume(self):
        evidence=[]
        class InspectMembership(OwnedWindowsJob):
            def _verify_membership(self):
                super()._verify_membership()
                evidence.append(('verified',self._assigned,self._resumed))
            def _resume(self):
                evidence.append(('resuming',self._assigned,self._resumed))
                super()._resume()
        owner=self.spawn('marker',cls=InspectMembership)
        self.assertEqual(evidence,[('verified',True,False),('resuming',True,False)])
        self.assertTrue(owner.wait_empty(5)['owned_job_empty'])

    def test_resume_failure_never_runs_child_and_cleans_handles(self):
        class RefuseResume(OwnedWindowsJob):
            def _resume(self):raise WindowsJobError('INJECTED_RESUME_FAILURE',owner=self)
        with self.assertRaises(WindowsJobError) as exc:self.spawn('marker',cls=RefuseResume)
        self.assertEqual(exc.exception.code,'INJECTED_RESUME_FAILURE')
        self.assertTrue(exc.exception.owner._closed)
        self.assertFalse((self.root/'executed').exists())
    def test_unicode_spaces_quotes_empty_argument_and_stdio(self):
        args=['space here','Unicode 中文','quote"value','',r'backslash\tail\\']
        owner=self.spawn('args',*args)
        owner.stdin.write(b'hello\n');owner.stdin.close();owner.stdin=None
        self.assertTrue(owner.wait_empty(5)['owned_job_empty'])
        self.assertEqual(json.loads((self.root/'argv.json').read_text(encoding='utf-8')),args)
        self.assertEqual(owner.exit_code(),0)
        self.assertEqual(owner.stdout.read(),b'hello\n')
        self.assertIn(b'synthetic stderr',owner.stderr.read())
    def test_pid_mutation_cannot_redirect_control(self):
        owner=self.spawn();self.wait_file('parent.ready')
        owner.pid=os.getpid()  # Diagnostic tampering must not target this test runner.
        self.assertTrue(owner.terminate(5)['owned_job_empty'])
        self.assertTrue(owner.host_exited())
    def test_only_explicit_pipe_handles_inherited(self):
        from native_harness_api.windows_job import HANDLE, DWORD, BOOL
        owner=self.spawn('args','safe')
        # Verify our owned handles are non-inheritable; actual child gets only
        # the allowlisted pipe counterparts via STARTUPINFOEX handle list.
        get=owner._api.dll.GetHandleInformation
        get.restype=BOOL;get.argtypes=[HANDLE,ctypes.POINTER(DWORD)]
        flags=DWORD()
        for handle in (owner._job,owner._process):
            self.assertTrue(get(handle,ctypes.byref(flags)));self.assertEqual(flags.value & 1,0)
        owner.stdin.write(b'ok\n');owner.stdin.close();owner.stdin=None
        self.assertTrue(owner.wait_empty(5)['owned_job_empty'])
    def test_inheritable_sentinel_is_not_in_child_handle_list(self):
        from native_harness_api.windows_job import _Win32, _Security, HANDLE, BOOL
        api=_Win32()
        create=api.dll.CreateEventW
        create.restype=HANDLE;create.argtypes=[ctypes.POINTER(_Security),BOOL,BOOL,ctypes.c_wchar_p]
        security=_Security(ctypes.sizeof(_Security),None,1)
        sentinel=create(ctypes.byref(security),True,False,None)
        self.assertTrue(sentinel)
        try:
            owner=self.spawn('sentinel',str(sentinel))
            result=json.loads(self.wait_file('sentinel.json').read_text())
            self.assertFalse(result['set_event'])
            self.assertEqual(api.WaitForSingleObject(sentinel,0),258)
            self.assertTrue(owner.wait_empty(5)['owned_job_empty'])
        finally:api.CloseHandle(sentinel)
    def test_controller_hard_crash_closes_job_and_kills_tree(self):
        import subprocess
        from native_harness_api.windows_job import _Win32,HANDLE,DWORD,BOOL
        api=_Win32();open_process=api.dll.OpenProcess
        open_process.restype=HANDLE;open_process.argtypes=[DWORD,BOOL,DWORD]
        env=dict(os.environ)
        env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        controller=subprocess.Popen([sys.executable,str(PEER.resolve()),'controller',str(self.root)],
            env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        handles=[]
        try:
            self.wait_file('controller.ready')
            for name in ('parent','child','grandchild'):
                handle=open_process(0x00100000,False,int((self.root/(name+'.pid')).read_text()))
                self.assertTrue(handle);handles.append(handle)
            (self.root/'crash.go').write_text('now')
            self.assertEqual(controller.wait(timeout=5),71)
            for handle in handles:self.assertEqual(api.WaitForSingleObject(handle,5000),0)
        finally:
            if controller.poll() is None:controller.kill();controller.wait(timeout=5)
            for handle in handles:api.CloseHandle(handle)
    def test_controller_crash_after_create_before_verification_kills_suspended_host(self):
        import subprocess
        from native_harness_api.windows_job import _Win32,HANDLE,DWORD,BOOL
        api=_Win32();open_process=api.dll.OpenProcess
        open_process.restype=HANDLE;open_process.argtypes=[DWORD,BOOL,DWORD]
        env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        controller=subprocess.Popen([sys.executable,str(PEER.resolve()),'controller_preverify',str(self.root)],
            env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        handle=None
        try:
            self.wait_file('controller.ready')
            handle=open_process(0x00100000,False,int((self.root/'created.pid').read_text()))
            self.assertTrue(handle)
            self.assertFalse((self.root/'executed').exists())
            (self.root/'crash.go').write_text('now')
            self.assertEqual(controller.wait(timeout=5),71)
            self.assertEqual(api.WaitForSingleObject(handle,5000),0)
            self.assertFalse((self.root/'executed').exists())
        finally:
            if controller.poll() is None:controller.kill();controller.wait(timeout=5)
            if handle:api.CloseHandle(handle)

    def test_native_failures_retain_owned_handles_for_cleanup_retry(self):
        for failed_api in ('TerminateJobObject','QueryInformationJobObject','CloseHandle'):
            with self.subTest(failed_api=failed_api):
                # Marker avoids reusing readiness files from prior trees.
                owner=self.spawn('grandchild')
                job,process=owner._job,owner._process
                original=getattr(owner._api,failed_api)
                setattr(owner._api,failed_api,lambda *args:0)
                try:
                    with self.assertRaises(WindowsJobError):owner.close(timeout=1)
                    self.assertFalse(owner._closed)
                    self.assertEqual(owner._job,job)
                    self.assertEqual(owner._process,process)
                finally:
                    setattr(owner._api,failed_api,original)
                    self.assertTrue(owner.close()['owned_job_empty'])
    def test_pid_alone_cannot_reattach_after_handle_loss(self):
        owner=self.spawn('grandchild')
        orphan=OwnedWindowsJob();orphan.pid=owner.pid
        with self.assertRaises(WindowsJobError):orphan.active_processes()
        with self.assertRaises(WindowsJobError):orphan.terminate()
        self.assertFalse(orphan.close()['owned_job_empty'])
        self.assertTrue(owner.close()['owned_job_empty'])

    def test_repeated_spawn_close_does_not_leak_handles(self):
        from native_harness_api.windows_job import HANDLE, DWORD, BOOL
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        current=kernel.GetCurrentProcess;current.restype=HANDLE;current.argtypes=[]
        count=kernel.GetProcessHandleCount;count.restype=BOOL;count.argtypes=[HANDLE,ctypes.POINTER(DWORD)]
        before=DWORD();self.assertTrue(count(current(),ctypes.byref(before)))
        for _ in range(10):
            owner=self.spawn('marker');self.assertTrue(owner.wait_empty(5)['owned_job_empty']);owner.close()
        after=DWORD();self.assertTrue(count(current(),ctypes.byref(after)))
        self.assertLessEqual(after.value,before.value+2)


if __name__=='__main__':unittest.main()
