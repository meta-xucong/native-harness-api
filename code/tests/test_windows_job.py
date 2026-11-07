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
