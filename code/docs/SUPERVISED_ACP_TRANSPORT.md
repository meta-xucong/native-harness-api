# Supervised ACP process transport (component draft)

Independent component, not an enabled WorkBuddy adapter. No scheduler, grant,
authentication, model selection, ACP messages or real channel admission occurs.

## Interface

```
t = SupervisedACPTransport()
t.start(command, cwd, env)  # {'pid': diagnostic_pid, 'host_started': True}
t.stdin, t.stdout, t.stderr  # binary streams, only after successful start
facts = t.observe()
facts = t.close(timeout=5)
```

Windows only, using OwnedWindowsJob creation-time JOB_LIST supervision. There is
no Popen fallback, poll/wait emulation, PID reattachment, factory configuration
or session recovery. Every start attempt consumes the object, including failed
validation/unsupported platform/partial launch. Close before start also seals it.
Failure raises sanitized TransportError; no command/env/vendor diagnostic is
included. A WindowsJobError owner remains attached for explicit close retries.

`observe` and `close` return separate facts: `host_exited`, `owned_job_empty`,
`handles_closed`, `cleanup_complete`, and a bounded `code`. None means unknown.
The latter two concern this component's owned handles/streams only, not ACP IO
threads or external work. Observation failure cannot claim cleanup. Close
failure returns unknown lifecycle facts and false cleanup, retains ownership,
and allows a later cleanup retry, never a second launch. Successful close is
idempotent. No result is `stopped_confirmed`; native completion, external tools,
remote work and provider-model provenance remain outside this component.

Timeout is finite 0..300 seconds, passed to the audited owner cleanup's shared
termination deadline. This is bounded process waiting, not a guarantee that an
OS call, lock acquisition or stream close can be forcibly interrupted. There is
no daemon cleanup thread returning before a late destructive action. Caller
must bound protocol IO, coordinate stream users, and separately track/join its
reader and writer threads. A successful close does not assert those threads
have exited. Failure before an owner was returned remains unobserved rather
than being fabricated as empty. Diagnostic pid is never used for control.

## Tests and unverified scope

`PYTHONPATH=src python -m unittest discover -s tests -p test_supervised_acp_transport.py -v`

Portable tests patch only the imported owner class for deterministic algorithm
checks; production has no fake-owner injection path or fallback. Windows tests
launch the existing local synthetic Python parent/child/grandchild fixture and
check cleanup, including a host that exits before its descendants. Linux skips
these tests explicitly. This new transport's real Windows tests have NOT_RUN
until a dedicated Windows job executes this revision. Prior OwnedWindowsJob
results do not certify this wrapper. No vendor code/account/prompt was used.

## Explicit ACP client seam

`ACPClient(..., supervised=True)` opts into this transport using the same
explicit argv/cwd/env. This is an internal component choice, not a new probe CLI
flag, service configuration, or real-channel admission mode. The default old
probe remains host-only. The package does not gain Windows supervision by
merely containing a changed ACP client.

The client serializes start/close, consumes failed start attempts, and seals IO
thread registration on close. The supervised close gives owner cleanup and IO
thread joins one shared five-second waiting budget. `io_threads_stopped` remains
separate; a surviving reader/writer prevents `cleanup_complete`, even if the
owned Job is empty. Cleanup may be retried but launch/prompt are never replayed.
These are process/pipe cleanup facts only. `descendants_stopped` remains false
because external brokers/services/remote work are outside owned Job authority.
Approval callbacks are not IO workers: a timed-out local callback may still be
waiting for input, but its late return cannot send an approval after the caller
has faulted. No forced cancellation or success claim is made for that callback.

`test_supervised_workbuddy_acp.py` adds portable lifecycle/race/error fixtures,
and Windows-only synthetic initialize/session/model/prompt/approval cases,
including a live owned descendant after turn completion. These test this
program's wiring, never vendor login, model selection or inference. There is no
new official WorkBuddy execution in this change. Its no-account initialize
failure and real-product blockers remain unchanged.

Thread registration itself is conservative: record the thread and an
unconfirmed-start marker before calling Thread.start. An interruption before
confirmation retains that record. If no thread identity has become observable,
close cannot infer that it never started and reports IO cleanup unknown; it
does not join an unstarted Thread or use is_alive=False as proof. A retained
started thread can be joined and reobserved on a later cleanup call.
