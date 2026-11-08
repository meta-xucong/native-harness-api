# Windows owned-tree lifetime evidence

## Confirmed defect and regression

Two hosted Windows runs of source `f433209` exposed the same real gap:
`OwnedWindowsJob.close()` had reported empty Job / complete cleanup, while
independently retained and membership-verified child and grandchild process
objects were still unsignaled. Their later bounded waits completed. The host
had already exited; the implementation had waited for its object but only Job
accounting for descendants. This is not dismissed as a test flake.

Diagnostic runs: 42449701897 and 42449705106. Test witnesses have only synchronize
and limited-query rights, verify the exact owned Job, and never kill by PID. The
immediate false observation is never upgraded by subsequent fixture cleanup.
Older directory-sharing errors had hidden the first observations; explicit
fixture cleanup now preserves the original result and reports bounded cleanup
separately. A directory retry is test hygiene, not process-stop proof.

## Required closure contract

The corrected owner must associate its own completion port while its Job is
still inactive, before a child can run. It retains verified process-object
handles from creation notifications and current-member enumeration. PIDs select
objects only inside this still-owned Job; they are never sufficient for control,
termination, reattachment or stopped evidence. Object creation identity prevents
confusing recycled identifiers. Termination remains through the retained Job.

Notifications are only a discovery aid. Lifetime accounting must reconcile with
all distinct captured objects, and success requires their actual signaled state,
the retained host's signaled state, and an empty current Job. The same gate applies
to natural exit observation and forced cleanup. Missed/ambiguous objects, failed
queries, overflow, wait failures or missing accounting evidence remain unknown.
Repeated empty snapshots or elapsed time cannot manufacture coverage.

The collector and all handles are part of ownership. Startup failure, timeout,
partial close or collector failure must preserve enough ownership for cleanup-only
retry. No failed close authorizes starting the task again or releasing a scheduler
slot. Collector shutdown, process-handle disposal and Job/pipe closure are bounded
and individually checked; cleanup cannot silently forget a failed handle close.

## Primary API contracts

- [Microsoft Job completion-port association](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_associate_completion_port)
  describes best-effort notifications, PID recycling and association before
  processes become active. Absence of an exit notification proves nothing.
- [Microsoft Job process list](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_process_id_list)
  requires handling an incomplete variable-sized list; truncation is not an
  empty-tree observation.
- [Microsoft TerminateJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)
  acts on all associated processes but does not replace per-object completion
  observation.

## Acceptance

Portable tests can check bounded algorithms and failure retention, not actual
Windows process lifetime. Windows self-authored fixtures must separately cover
host-first exit, dynamic descendants, fast children, natural exit, kill cleanup,
collector startup/shutdown failures and retained-handle observations. Neither
these tests nor lifecycle repair demonstrates vendor login or model correctness.
Keep the immediate-child-object regression enabled; do not add sleeps or slow
fixture startup merely to make discovery succeed.

The implementation uses a private single-consumer completion port and bounded
current-member census. Its portable regressions cover missing births, incomplete
lists, wrong membership, count changes, tracker faults, failed waits and partial
handle closure. Local validation: 784 tests, 54 platform skips; Windows results
must be checked separately before treating this revision as a deployable repair.
