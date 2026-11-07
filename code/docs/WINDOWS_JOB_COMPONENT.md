# Owned Windows Job component: implementation, not runtime acceptance

Independent source: `src/native_harness_api/windows_job.py`.
No Runner, ACP launcher, Bridge binding, result contract, or production admission
was changed. This component cannot label a task `stopped_confirmed`.

## Win32 basis

- [CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw): explicit executable, mutable command line, suspended primary thread, Unicode environment.
- [UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute): STARTUPINFOEX handle list restricts inherited handles; its entries must be inheritable and creation uses bInheritHandles=TRUE.
- [Creation-time Job assignment](https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812): JOB_LIST closes the application-level create-then-assign gap. Microsoft UpdateProcThreadAttribute documents Windows 10 / Server 2016 minimum. There is no fallback to post-create assignment.
- [IsProcessInJob](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob): the exact owned Job HANDLE must be verified before ResumeThread; incompatible enclosing jobs or failed verification cause failure, never breakaway fallback.
- [Job limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information): KILL_ON_JOB_CLOSE configured; BREAKAWAY_OK and SILENT_BREAKAWAY_OK are not enabled.
- [QueryInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject): ActiveProcesses from basic accounting is the emptiness observation.
- [ResumeThread](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread): exactly one expected suspension is removed; unexpected return is treated as launch failure.

## API

`OwnedWindowsJob.spawn(argv, cwd, env)` validates an absolute executable `.exe`,
explicit existing directory and explicit environment. It returns binary
`stdin`, `stdout`, `stderr` streams. It never invokes a shell or discovers an
executable on PATH. The caller owns bounded protocol IO and software provenance
verification; neither is a claim made by this primitive.

`host_exited()` consults the retained process HANDLE, not a PID.
`exit_code()` returns its exit code, or None while running; call before close.
`active_processes()` queries the retained Job HANDLE.
`wait_empty(timeout)` observes without killing.
`terminate(timeout)` terminates the job and observes emptiness.
`close(timeout)` also cleans owned pipe/thread/process/job handles, but only
after the applicable termination confirmation. Cleanup errors retain ownership
for retry and raise `WindowsJobError`, rather than returning success. If spawn
cleanup fails, the exception's `owner` is the only supported cleanup reference.
A closed handle cannot be recovered using a PID or a named job.

Evidence is **only `owned_job_empty`**. Never infer that a provider request,
remote action, pre-existing sidecar, service-created process, scheduled task, or
native tool's external side effect has stopped. This is not a sandbox. No
breakaway flag does not prevent a process from asking an unrelated service to
perform work outside the job.

## Lifecycle boundaries

Requires Windows 10 / Server 2016 or newer. Both `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`
and `PROC_THREAD_ATTRIBUTE_JOB_LIST` are installed in the two-entry STARTUPINFOEX
attribute list. CreateProcessW uses CREATE_SUSPENDED and creates the process in
the owned job; an IsProcessInJob check using that exact job handle must succeed
before ResumeThread. Unsupported JOB_LIST fails before launching anything; no
non-atomic fallback exists. Failed membership checks dispose of the still
suspended host. Parent exit is not group emptiness.

This removes the earlier application's Create→Assign window. The official
reference/blog does not enumerate every kernel rollback case if the caller dies
inside CreateProcessW; this component does not invent such a guarantee. The
Windows tests cover controller hard-crash after creation has returned but before
membership verification/resume, and after descendants are running. Neither is
proof about every interruption point inside the OS API. Durable restart cannot
infer resource identity or recover ownership from a saved PID.

Concurrent unrelated code using broad inheritable-handle process creation is
outside this module's control. Its own spawn paths use a lock and explicit
handle lists, and the Job/process/thread handles themselves are non-inheritable.

## Tests and status

`PYTHONPATH=src python -m unittest discover -s tests -p test_windows_job.py -v`

On the current Linux development host: **2 portable tests PASS, 18 Windows tests
SKIPPED**. This is NOT Win32 runtime validation. No Windows host, vendor binary,
user account, paid inference, or production deployment was used.

Windows-only tests use local Python synthetic children/grandchildren and cover:

- group membership; host exits while descendants remain; timeout;
- explicit termination, close and kernel kill-on-last-job-handle;
- controller `os._exit` without Python cleanup, including immediately after suspended creation before verification/resume;
- unsupported JOB_LIST rejection without fallback; exact membership-before-resume ordering;
- membership/resume failure injection with a real suspended child and no execution;
- Unicode/space/quote/empty argv and inherited stdio;
- unrelated inheritable event sentinel not inherited;
- cleanup/terminate/query failures retain ownership and do not return proof;
- repeated handle cleanup; diagnostic PID mutation cannot redirect control;
- PID-only objects cannot reattach to a live process tree;
- prohibited breakaway attempt.

Run these tests on supported real Windows before considering integration. Mock
Win32 success or Linux import success must never be substituted for those results.
