"""Owned Windows process tree, independent of ACP/Runner/model contracts.

CreateProcessW uses creation-time JOB_LIST assignment and remains suspended
until exact owned-job membership is verified. Requires Windows 10/Server 2016+.
Only three specified pipe handles are inherited. No shell, breakaway, process
reattachment, or PID-based termination. Evidence means only owned_job_empty;
remote work, pre-existing processes, and external side effects are not covered.

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
            if self._closed:
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
            info = _Accounting()
            self._check(self._api.QueryInformationJobObject(self._job, 1, C.byref(info), C.sizeof(info), None), 'JOB_QUERY_FAILED')
            count = int(info.ActiveProcesses)
            if self._assigned and count == 0:
                self._empty_observed = True
            return count

    def wait_empty(self, timeout=5):
        timeout = _timeout(timeout)
        deadline = time.monotonic() + timeout
        with self._state_lock:
            while self.active_processes() != 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {'owned_job_empty':False}
                time.sleep(min(.02, remaining))
            return {'owned_job_empty':True}

    def terminate(self, timeout=5):
        timeout = _timeout(timeout)
        with self._state_lock:
            if self._closed:
                return {'owned_job_empty':self._empty_observed}
            if not self._job:
                raise WindowsJobError('JOB_NOT_CREATED')
            self._check(self._api.TerminateJobObject(self._job, 1), 'JOB_TERMINATE_FAILED')
            return self.wait_empty(timeout)

    def close(self, timeout=5):
        timeout = _timeout(timeout)
        with self._state_lock:
            if self._closed:
                return {'owned_job_empty':self._empty_observed}
            deadline = time.monotonic() + timeout
            # Membership-check failure still leaves a suspended host needing disposal.
            if self._process and not self.host_exited():
                if self._assigned:
                    self._check(self._api.TerminateJobObject(self._job, 1), 'JOB_TERMINATE_FAILED')
                else:
                    self._check(self._api.TerminateProcess(self._process, 1), 'HOST_TERMINATE_FAILED')
                milliseconds = max(0, int((deadline-time.monotonic())*1000))
                if self._api.WaitForSingleObject(self._process, milliseconds) != 0:
                    raise WindowsJobError('HOST_STOP_UNCONFIRMED', owner=self)
            if self._assigned:
                # Host may exit while descendants continue. Always terminate job.
                result = self.terminate(max(0, deadline-time.monotonic()))
                if not result['owned_job_empty']:
                    raise WindowsJobError('JOB_STOP_UNCONFIRMED', owner=self)
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
            for name in ('_thread', '_process', '_job'):
                handle = getattr(self, name)
                self._close_handle(handle); setattr(self, name, None)
            self._closed = True
            return {'owned_job_empty':self._empty_observed}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
