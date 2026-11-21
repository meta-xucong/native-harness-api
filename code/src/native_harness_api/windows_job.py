"""Owned Windows process tree, independent of ACP/Runner/model contracts.

CreateProcessW uses creation-time JOB_LIST assignment and remains suspended
until exact owned-job membership is verified. Requires Windows 10/Server 2016+.
Only three specified pipe handles are inherited. No shell, breakaway, process
reattachment, or PID-based termination. Evidence means only owned_job_empty;
remote work, pre-existing processes, and external side effects are not covered.

A private completion port tracks process objects from before the host is created.
Notifications are not assumed reliable: lifetime TotalProcesses must match the
retained object census, and every object must be signaled. Missed/ambiguous
short-lived processes, tracking limits or collector failure leave cleanup unknown
and retain ownership; accounting zero or a quiet snapshot is never sufficient.

Microsoft contracts:
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject

Windows acceptance tests must run on real Windows. Importing on Linux does not
validate Win32 behavior. This is not a sandbox or a credential/security boundary.
"""
from __future__ import annotations

import ctypes as C
import math
import os
from pathlib import Path
import subprocess
import threading
import time

DWORD = C.c_uint32
BOOL = C.c_int32
HANDLE = C.c_void_p
SIZE_T = C.c_size_t
LPVOID = C.c_void_p


class _FileTime(C.Structure):
    _fields_ = [('low', DWORD), ('high', DWORD)]


class _CompletionAssociation(C.Structure):
    _fields_ = [('CompletionKey', LPVOID), ('CompletionPort', HANDLE)]


class _Security(C.Structure):
    _fields_ = [('nLength', DWORD), ('lpSecurityDescriptor', LPVOID), ('bInheritHandle', BOOL)]


class _Startup(C.Structure):
    _fields_ = [('cb', DWORD), ('lpReserved', C.c_wchar_p), ('lpDesktop', C.c_wchar_p),
                ('lpTitle', C.c_wchar_p), ('dwX', DWORD), ('dwY', DWORD),
                ('dwXSize', DWORD), ('dwYSize', DWORD), ('dwXCountChars', DWORD),
                ('dwYCountChars', DWORD), ('dwFillAttribute', DWORD), ('dwFlags', DWORD),
                ('wShowWindow', C.c_uint16), ('cbReserved2', C.c_uint16),
                ('lpReserved2', LPVOID), ('hStdInput', HANDLE), ('hStdOutput', HANDLE), ('hStdError', HANDLE)]


class _StartupEx(C.Structure):
    _fields_ = [('StartupInfo', _Startup), ('lpAttributeList', LPVOID)]


class _ProcessInfo(C.Structure):
    _fields_ = [('hProcess', HANDLE), ('hThread', HANDLE), ('dwProcessId', DWORD), ('dwThreadId', DWORD)]


class _BasicLimits(C.Structure):
    _fields_ = [('PerProcessUserTimeLimit', C.c_int64), ('PerJobUserTimeLimit', C.c_int64),
                ('LimitFlags', DWORD), ('MinimumWorkingSetSize', SIZE_T), ('MaximumWorkingSetSize', SIZE_T),
                ('ActiveProcessLimit', DWORD), ('Affinity', SIZE_T), ('PriorityClass', DWORD), ('SchedulingClass', DWORD)]


class _IOCounters(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimits(C.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IOCounters),
                ('ProcessMemoryLimit', SIZE_T), ('JobMemoryLimit', SIZE_T),
                ('PeakProcessMemoryUsed', SIZE_T), ('PeakJobMemoryUsed', SIZE_T)]


class _Accounting(C.Structure):
    _fields_ = [(name, C.c_int64) for name in ('TotalUserTime', 'TotalKernelTime',
                'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')] + [
                (name, DWORD) for name in ('TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]


class WindowsJobError(RuntimeError):
    def __init__(self, code, *, winerror=None, owner=None):
        self.code, self.winerror, self.owner = code, winerror, owner
        super().__init__(code)  # No command, environment, paths, or OS message leakage.


class UnsupportedPlatform(WindowsJobError):
    def __init__(self):
        super().__init__('WINDOWS_REQUIRED')


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 300:
        raise ValueError('Timeout must be between 0 and 300 seconds')
    return float(value)


def _arguments(argv, cwd, env):
    if (not isinstance(argv, list) or not argv or len(argv) > 128 or
            any(not isinstance(v, str) or '\x00' in v for v in argv) or not argv[0]):
        raise ValueError('Explicit argv required')
    executable = Path(argv[0])
    directory = Path(cwd)
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != '.exe':
        raise ValueError('An absolute executable .exe path is required; no shell or PATH lookup')
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('An absolute existing working directory is required')
    if (not isinstance(env, dict) or not env or len(env) > 512 or
            any(not isinstance(k, str) or not k or '=' in k or '\x00' in k or
                not isinstance(v, str) or '\x00' in v for k, v in env.items())):
        raise ValueError('Explicit environment required')
    if len({k.upper() for k in env}) != len(env):
        raise ValueError('Duplicate environment keys')
    command = subprocess.list2cmdline(argv)
    environment = '\x00'.join(k + '=' + env[k] for k in sorted(env, key=str.upper)) + '\x00\x00'
    if len(command.encode('utf-16-le')) // 2 >= 32767 or len(environment.encode('utf-16-le')) // 2 > 32767:
        raise ValueError('Command or environment exceeds limit')
    return str(executable), command, str(directory), environment


class _Win32:
    def __init__(self):
        if os.name != 'nt':
            raise UnsupportedPlatform()
        self.dll = C.WinDLL('kernel32', use_last_error=True)
        def bind(name, restype, *argtypes):
            fn = getattr(self.dll, name); fn.restype = restype; fn.argtypes = argtypes
            setattr(self, name, fn)
        bind('CreateJobObjectW', HANDLE, LPVOID, C.c_wchar_p)
        bind('CreateIoCompletionPort', HANDLE, HANDLE, HANDLE, SIZE_T, DWORD)
        bind('GetQueuedCompletionStatus', BOOL, HANDLE, C.POINTER(DWORD), C.POINTER(SIZE_T), C.POINTER(LPVOID), DWORD)
        bind('OpenProcess', HANDLE, DWORD, BOOL, DWORD)
        bind('GetProcessTimes', BOOL, HANDLE, C.POINTER(_FileTime), C.POINTER(_FileTime), C.POINTER(_FileTime), C.POINTER(_FileTime))
        bind('SetInformationJobObject', BOOL, HANDLE, C.c_int, LPVOID, DWORD)
        bind('QueryInformationJobObject', BOOL, HANDLE, C.c_int, LPVOID, DWORD, C.POINTER(DWORD))
        bind('IsProcessInJob', BOOL, HANDLE, HANDLE, C.POINTER(BOOL))
        bind('TerminateJobObject', BOOL, HANDLE, C.c_uint)
        bind('CreatePipe', BOOL, C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(_Security), DWORD)
        bind('SetHandleInformation', BOOL, HANDLE, DWORD, DWORD)
        bind('InitializeProcThreadAttributeList', BOOL, LPVOID, DWORD, DWORD, C.POINTER(SIZE_T))
        bind('UpdateProcThreadAttribute', BOOL, LPVOID, DWORD, SIZE_T, LPVOID, SIZE_T, LPVOID, LPVOID)
        bind('DeleteProcThreadAttributeList', None, LPVOID)
        bind('CreateProcessW', BOOL, C.c_wchar_p, C.c_wchar_p, LPVOID, LPVOID, BOOL,
             DWORD, LPVOID, C.c_wchar_p, LPVOID, C.POINTER(_ProcessInfo))
        bind('ResumeThread', DWORD, HANDLE)
        bind('TerminateProcess', BOOL, HANDLE, C.c_uint)
        bind('WaitForSingleObject', DWORD, HANDLE, DWORD)
        bind('GetExitCodeProcess', BOOL, HANDLE, C.POINTER(DWORD))
        bind('CloseHandle', BOOL, HANDLE)


_SPAWN_LOCK = threading.Lock()


class OwnedWindowsJob:
    """A single owned process tree. Call close explicitly; no PID reattachment.

    stdin/stdout/stderr are binary pipes. The caller must bound and drain output;
    this component never reads arbitrary user files or starts an output collector.
    close failure retains ownership handles for explicit cleanup retry.
    """
    def __init__(self):
        self._api = _Win32()
        self._job = self._process = self._thread = None
        self._raw = set()
        self.stdin = self.stdout = self.stderr = None
        self.pid = None  # Diagnostic only; never used for control or proof.
        self._closed = False
        self._empty_observed = False
        self._assigned = False
        self._resumed = False
        self._state_lock = threading.RLock()
        self._tracking_lock = threading.RLock()
        self._known = {}  # (PID, creation FILETIME) -> retained process HANDLE.
        self._port = self._tracker = None
        self._tracker_stop = threading.Event()
        self._tracking_fault = None
        self._last_total = 0
        self._unclassified = set()
        self._stop_proven = False

    MAX_TRACKED_PROCESSES = 4096
    MAX_TRACKING_MESSAGES = 65536

    def _tracking_failed(self, code):
        with self._tracking_lock:
            if self._tracking_fault is None:
                self._tracking_fault = code
        self._tracker_stop.set()

    def _identity(self, handle, pid):
        created, exited, kernel, user = (_FileTime() for _ in range(4))
        self._check(self._api.GetProcessTimes(handle, C.byref(created), C.byref(exited),
                    C.byref(kernel), C.byref(user)), 'PROCESS_IDENTITY_FAILED')
        return (pid, (int(created.high) << 32) | int(created.low))

    def _retain_process(self, handle, pid, *, host=False):
        # Caller transfers this handle; on any failure it remains retained.
        # Non-host unclassified handles are retained separately for failure cleanup.
        with self._tracking_lock:
            if not host:
                self._unclassified.add(handle)
            if not self._job:
                raise WindowsJobError('JOB_NOT_CREATED', owner=self)
            belongs = BOOL()
            self._check(self._api.IsProcessInJob(handle, self._job, C.byref(belongs)), 'TRACKED_MEMBERSHIP_QUERY_FAILED')
            if not belongs.value:
                raise WindowsJobError('TRACKED_MEMBERSHIP_UNVERIFIED', owner=self)
            identity = self._identity(handle, pid)
            old = self._known.get(identity)
            if old is not None:
                if host and old != handle:
                    self._close_handle(old)
                    self._known[identity] = handle
                elif not host and old != handle:
                    self._close_handle(handle)
                if not host:
                    self._unclassified.discard(handle)
                return
            if len(self._known) >= self.MAX_TRACKED_PROCESSES:
                raise WindowsJobError('PROCESS_TRACKING_LIMIT', owner=self)
            self._known[identity] = handle
            if not host:
                self._unclassified.discard(handle)

    def _capture_pid(self, pid):
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            raise WindowsJobError('TRACKED_PID_INVALID', owner=self)
        # The PID comes only from the private Job/port, never caller input. It is
        # not authority: exact membership and creation identity are checked.
        with self._tracking_lock:
            if len(self._known) + len(self._unclassified) >= self.MAX_TRACKED_PROCESSES:
                raise WindowsJobError('PROCESS_TRACKING_LIMIT', owner=self)
            for key, handle in self._known.items():
                if key[0] == pid:
                    status = self._api.WaitForSingleObject(handle, 0)
                    if status == 258:
                        return
                    if status != 0:
                        raise self._error('TRACKED_WAIT_FAILED')
        handle = self._api.OpenProcess(0x00100000 | 0x1000, False, pid)
        self._check(handle, 'TRACKED_OPEN_FAILED')
        self._retain_process(handle, pid)

    def _start_tracking(self):
        self._unclassified = set()
        self._port = self._api.CreateIoCompletionPort(HANDLE(-1), None, 0, 1)
        self._check(self._port, 'COMPLETION_PORT_CREATE_FAILED')
        association = _CompletionAssociation(1, self._port)
        self._check(self._api.SetInformationJobObject(self._job, 7, C.byref(association),
                    C.sizeof(association)), 'COMPLETION_PORT_ASSOCIATE_FAILED')
        # Associated while the Job is empty, before CreateProcess/ResumeThread.
        self._tracker = threading.Thread(target=self._track_processes, daemon=True,
                                         name='owned-job-lifetime-tracker')
        self._tracker.start()

    def _track_processes(self):
        messages = 0
        try:
            while not self._tracker_stop.is_set():
                message, key, value = DWORD(), SIZE_T(), LPVOID()
                ok = self._api.GetQueuedCompletionStatus(self._port, C.byref(message),
                        C.byref(key), C.byref(value), 20)
                if not ok:
                    if getattr(C, 'get_last_error', lambda: 0)() == 258:  # WAIT_TIMEOUT, not process proof.
                        continue
                    raise WindowsJobError('COMPLETION_PORT_READ_FAILED', owner=self)
                messages += 1
                if key.value != 1 or messages > self.MAX_TRACKING_MESSAGES:
                    raise WindowsJobError('COMPLETION_PORT_INVALID_OR_LIMIT', owner=self)
                if message.value == 6:  # JOB_OBJECT_MSG_NEW_PROCESS only.
                    self._capture_pid(int(value.value or 0))
                # EXIT/ACTIVE_PROCESS_ZERO messages are NOT process-stop proof.
        except Exception:
            self._tracking_failed('PROCESS_TRACKING_UNCONFIRMED')

    def _current_pids(self):
        capacity = 32
        for _ in range(16):
            class ProcessList(C.Structure):
                _fields_ = [('assigned', DWORD), ('listed', DWORD), ('pids', SIZE_T * capacity)]
            info = ProcessList()
            ok = self._api.QueryInformationJobObject(self._job, 3, C.byref(info), C.sizeof(info), None)
            assigned, listed = int(info.assigned), int(info.listed)
            if assigned > self.MAX_TRACKED_PROCESSES or listed > capacity:
                raise WindowsJobError('JOB_PROCESS_LIST_LIMIT', owner=self)
            if ok and assigned == listed:
                pids = [int(info.pids[i]) for i in range(listed)]
                if len(set(pids)) != len(pids) or any(not 0 < pid <= 0xffffffff for pid in pids):
                    raise WindowsJobError('JOB_PROCESS_LIST_INVALID', owner=self)
                return pids
            if not ok and getattr(C, 'get_last_error', lambda: 0)() != 234:  # ERROR_MORE_DATA
                raise self._error('JOB_PROCESS_LIST_FAILED')
            capacity = min(self.MAX_TRACKED_PROCESSES, max(capacity * 2, assigned))
        raise WindowsJobError('JOB_PROCESS_LIST_UNSTABLE', owner=self)

    def _capture_current(self):
        try:
            for pid in self._current_pids():
                self._capture_pid(pid)
            return True
        except Exception:
            # A later successful complete enumeration may recover a transient
            # query error, but cumulative census must still cover every birth.
            return False

    def _accounting(self):
        info = _Accounting()
        self._check(self._api.QueryInformationJobObject(self._job, 1, C.byref(info),
                    C.sizeof(info), None), 'JOB_QUERY_FAILED')
        total, active = int(info.TotalProcesses), int(info.ActiveProcesses)
        if total < self._last_total or total > self.MAX_TRACKED_PROCESSES or active > total:
            self._tracking_failed('JOB_ACCOUNTING_INVALID_OR_LIMIT')
            raise WindowsJobError('JOB_ACCOUNTING_INVALID_OR_LIMIT', owner=self)
        self._last_total = total
        return total, active

    def _whole_lifetime_signaled(self):
        with self._tracking_lock:
            if self._tracking_fault or self._unclassified or not self._known:
                return False
            # Snapshot stability alone is not coverage. TotalProcesses includes
            # every lifetime birth, including missed short-lived descendants.
            if self._current_pids():
                return False
            total, active = self._accounting()
            if active or total != len(self._known):
                return False
            for handle in self._known.values():
                status = self._api.WaitForSingleObject(handle, 0)
                if status == 258:
                    return False
                if status != 0:
                    raise self._error('TRACKED_WAIT_FAILED')
            # No birth/exit gap may hide behind the first accounting snapshot.
            final_total, final_active = self._accounting()
            return final_active == 0 and final_total == total and not self._current_pids()

    def _stop_tracking(self, deadline):
        self._tracker_stop.set()
        if self._tracker is not None:
            try:
                self._tracker.join(max(0, deadline-time.monotonic()))
            except RuntimeError:
                if self._tracker.ident is not None:
                    raise WindowsJobError('TRACKER_STOP_UNCONFIRMED', owner=self) from None
            if self._tracker.is_alive():
                raise WindowsJobError('TRACKER_STOP_UNCONFIRMED', owner=self)

    def _error(self, code):
        return WindowsJobError(code, winerror=C.get_last_error(), owner=self)

    def _check(self, ok, code):
        if not ok:
            raise self._error(code)

    def _close_handle(self, handle):
        if handle:
            self._check(self._api.CloseHandle(handle), 'HANDLE_CLOSE_FAILED')

    def _pipe(self, child_reads):
        read, write = HANDLE(), HANDLE()
        security = _Security(C.sizeof(_Security), None, 1)
        self._check(self._api.CreatePipe(C.byref(read), C.byref(write), C.byref(security), 0), 'PIPE_CREATE_FAILED')
        self._raw.update((read.value, write.value))
        parent, child = (write.value, read.value) if child_reads else (read.value, write.value)
        self._check(self._api.SetHandleInformation(parent, 1, 0), 'PIPE_INHERITANCE_FAILED')
        return parent, child

    @classmethod
    def spawn(cls, argv, cwd, env):
        if os.name != 'nt':
            raise UnsupportedPlatform()
        executable, command, directory, environment = _arguments(argv, cwd, env)
        owner = cls()
        with _SPAWN_LOCK:
            try:
                owner._spawn(executable, command, directory, environment)
                return owner
            except BaseException as original:
                try:
                    owner.close(timeout=5)
                except BaseException:
                    raise WindowsJobError('SPAWN_CLEANUP_UNCONFIRMED', owner=owner) from None
                raise original

    def _verify_membership(self):
        belongs = BOOL()
        self._check(self._api.IsProcessInJob(self._process, self._job, C.byref(belongs)), 'JOB_MEMBERSHIP_QUERY_FAILED')
        if not belongs.value:
            raise WindowsJobError('JOB_MEMBERSHIP_UNVERIFIED', owner=self)
        self._assigned = True

    def _install_job_attribute(self, storage, jobs):
        # PROC_THREAD_ATTRIBUTE_JOB_LIST = ProcThreadAttributeValue(13,FALSE,TRUE,FALSE).
        # No fallback to post-create assignment on old or unsupported Windows.
        self._check(self._api.UpdateProcThreadAttribute(storage, 0, 0x0002000D,
            C.cast(jobs, LPVOID), C.sizeof(jobs), None, None), 'JOB_LIST_UNSUPPORTED_OR_FAILED')

    def _resume(self):
        previous = self._api.ResumeThread(self._thread)
        if previous != 1:
            # Any unexpected suspend count is not permission to keep resuming.
            raise self._error('THREAD_RESUME_FAILED')
        self._resumed = True

    def _spawn(self, executable, command, directory, environment):
        self._job = self._api.CreateJobObjectW(None, None)  # Unnamed, non-inheritable.
        self._check(self._job, 'JOB_CREATE_FAILED')
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE only; never BREAKAWAY flags.
        self._check(self._api.SetInformationJobObject(self._job, 9, C.byref(limits), C.sizeof(limits)), 'JOB_LIMITS_FAILED')
        self._start_tracking()
        parent_in, child_in = self._pipe(True)
        parent_out, child_out = self._pipe(False)
        parent_err, child_err = self._pipe(False)
        inherited = (HANDLE * 3)(child_in, child_out, child_err)
        jobs = (HANDLE * 1)(self._job)  # Remains alive until attribute-list deletion.
        size = SIZE_T()
        self._api.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
        if not size.value or size.value > 1048576:
            raise self._error('ATTRIBUTE_SIZE_FAILED')
        storage = C.create_string_buffer(size.value)
        self._check(self._api.InitializeProcThreadAttributeList(storage, 2, 0, C.byref(size)), 'ATTRIBUTE_INIT_FAILED')
        try:
            self._check(self._api.UpdateProcThreadAttribute(storage, 0, 0x00020002,
                C.cast(inherited, LPVOID), C.sizeof(inherited), None, None), 'HANDLE_LIST_FAILED')
            self._install_job_attribute(storage, jobs)
            startup = _StartupEx()
            startup.StartupInfo.cb = C.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = child_in
            startup.StartupInfo.hStdOutput = child_out
            startup.StartupInfo.hStdError = child_err
            startup.lpAttributeList = C.cast(storage, LPVOID)
            info = _ProcessInfo()
            cmd = C.create_unicode_buffer(command)
            block = C.create_unicode_buffer(environment)
            flags = 0x4 | 0x400 | 0x80000  # CREATE_SUSPENDED | UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT
            self._check(self._api.CreateProcessW(executable, cmd, None, None, True,
                flags, C.cast(block, LPVOID), directory, C.byref(startup), C.byref(info)), 'PROCESS_CREATE_FAILED')
            self._process, self._thread, self.pid = info.hProcess, info.hThread, info.dwProcessId
        finally:
            self._api.DeleteProcThreadAttributeList(storage)
        self._verify_membership()  # Must succeed before ResumeThread; no PID lookup.
        self._retain_process(self._process, self.pid, host=True)
        for handle in (child_in, child_out, child_err):
            self._close_handle(handle); self._raw.remove(handle)
        # Transfer parent pipe ownership to CRT descriptors before allowing launch.
        import msvcrt
        for name, handle, mode, flags in (
                ('stdin', parent_in, 'wb', os.O_WRONLY),
                ('stdout', parent_out, 'rb', os.O_RDONLY),
                ('stderr', parent_err, 'rb', os.O_RDONLY)):
            fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
            self._raw.remove(handle)
            try:
                stream = os.fdopen(fd, mode, buffering=0)
            except BaseException:
                os.close(fd)
                raise
            setattr(self, name, stream)
        self._resume()
        self._close_handle(self._thread); self._thread = None

    def host_exited(self):
        with self._state_lock:
            if self._closed or self._stop_proven:
                return True
            if not self._process:
                return False
            status = self._api.WaitForSingleObject(self._process, 0)
            if status == 0:
                return True
            if status == 258:
                return False
            raise self._error('HOST_WAIT_FAILED')

    def exit_code(self):
        """Read the retained host object; call before close. None means running."""
        with self._state_lock:
            if self._closed or not self._process:
                raise WindowsJobError('HOST_HANDLE_UNAVAILABLE')
            if not self.host_exited():
                return None
            code = DWORD()
            self._check(self._api.GetExitCodeProcess(self._process, C.byref(code)), 'HOST_EXIT_CODE_FAILED')
            return int(code.value)

    def active_processes(self):
        with self._state_lock:
            if self._closed:
                if self._empty_observed:
                    return 0
                raise WindowsJobError('JOB_CLOSED_UNVERIFIED')
            if not self._job:
                raise WindowsJobError('JOB_NOT_CREATED')
            return self._accounting()[1]

    def wait_empty(self, timeout=5):
        timeout = _timeout(timeout)
        deadline = time.monotonic() + timeout
        with self._state_lock:
            if self._closed or self._stop_proven:
                return {'owned_job_empty': self._empty_observed}
            while True:
                captured = self._capture_current()
                if captured and self.host_exited() and self._whole_lifetime_signaled():
                    if self._assigned:
                        self._empty_observed = True
                    return {'owned_job_empty': True}
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._tracking_fault:
                    return {'owned_job_empty': False}
                time.sleep(min(.02, remaining))

    def terminate(self, timeout=5):
        timeout = _timeout(timeout)
        deadline = time.monotonic() + timeout
        with self._state_lock:
            if self._closed:
                return {'owned_job_empty':self._empty_observed}
            if not self._job:
                raise WindowsJobError('JOB_NOT_CREATED')
            self._capture_current()  # Capture errors never prevent containment kill.
            self._check(self._api.TerminateJobObject(self._job, 1), 'JOB_TERMINATE_FAILED')
            return self.wait_empty(max(0, deadline-time.monotonic()))

    def close(self, timeout=5):
        timeout = _timeout(timeout)
        with self._state_lock:
            if self._closed:
                return {'owned_job_empty':self._empty_observed}
            deadline = time.monotonic() + timeout
            if not self._stop_proven:
                if self._assigned:
                    self._capture_current()
                # Membership-check failure leaves a suspended host for disposal.
                if self._process and not self.host_exited():
                    if self._assigned:
                        self._check(self._api.TerminateJobObject(self._job, 1), 'JOB_TERMINATE_FAILED')
                    else:
                        self._check(self._api.TerminateProcess(self._process, 1), 'HOST_TERMINATE_FAILED')
                    milliseconds = max(0, int((deadline-time.monotonic())*1000))
                    if self._api.WaitForSingleObject(self._process, milliseconds) != 0:
                        raise WindowsJobError('HOST_STOP_UNCONFIRMED', owner=self)
                if self._assigned:
                    result = self.terminate(max(0, deadline-time.monotonic()))
                    if not result['owned_job_empty']:
                        raise WindowsJobError('JOB_LIFETIME_STOP_UNCONFIRMED', owner=self)
                self._stop_tracking(deadline)
                # Tracker failure cannot arrive unnoticed during join.
                if self._assigned and self._tracking_fault:
                    raise WindowsJobError('JOB_LIFETIME_STOP_UNCONFIRMED', owner=self)
                self._stop_proven = True
            # Only after stop confirmation release owned resources. No proof by PID.
            for name in ('stdin', 'stdout', 'stderr'):
                stream = getattr(self, name)
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        raise WindowsJobError('PIPE_CLOSE_FAILED', owner=self) from None
                    setattr(self, name, None)
            for handle in tuple(self._raw):
                self._close_handle(handle); self._raw.remove(handle)
            with self._tracking_lock:
                for identity, handle in tuple(self._known.items()):
                    if handle != self._process:
                        self._close_handle(handle)
                        del self._known[identity]
                for handle in tuple(self._unclassified):
                    self._close_handle(handle)
                    self._unclassified.remove(handle)
            if self._port:
                self._close_handle(self._port)
                self._port = None
            for name in ('_thread', '_process', '_job'):
                handle = getattr(self, name)
                self._close_handle(handle); setattr(self, name, None)
                if name == '_process':
                    self._known = {key: value for key, value in self._known.items() if value != handle}
            self._closed = True
            return {'owned_job_empty':self._empty_observed}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
