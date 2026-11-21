# Beta.15 owned-session integration slice

This code joins the reviewed Python session coordinator to the connected-client JS
shim and external-only runtime assembly using bounded JSONL over owned binary
pipes. It is not wired into Node/Facade, has no vendor launcher or production
configuration, and does not enable a real model channel. It does not infer that an
external-mode handshake supplies WorkBuddy's native account/Product authority.

## Components and owner contract

- `beta15_owned_session.OwnedSessionEngine(transport)` orchestrates exactly one
  launch and one new-session/selection/prompt attempt through the coordinator.
- The caller must supply `SupervisedACPTransport` for actual Windows ownership.
  There is no Linux/Popen production fallback. Test-only `SyntheticHostTransport`
  runs only the self-authored Node child and cannot establish descendant ownership.
- The caller must verify launch command, official bytes and runtime provenance,
  path ownership, empty workspace/config and explicit environment before entry.
  The engine does not discover a vendor executable or read account/config data.
- The caller must durably record the task/grant intent BEFORE invoking `run` and
  must already have authority for every possible operation in that attempt.
  This in-memory object is not a durable journal or new queue. Recreating it or
  seeing a timeout, EOF or dead process does not authorize another prompt.
- An already-set `threading.Event` cancellation returns before launch, with
  `launch_attempted=false` and cleanup not required. Later cancellation causes
  bounded cancellation/close handling; it never starts a replacement turn.

The injected JS assembly follows the successfully tested external handshake
contract, including mandatory form/URL capabilities and handlers before connect.
A real loader still needs the exact audited virtual ASAR entry and physical
`original-fs`/paired-tree verification. The assembly deliberately accepts injected
verifier/API functions for tests, not arbitrary trust claims for service admission.
See [command shim](BETA15_COMMAND_SHIM.md).

## I/O, result and lifecycle boundaries

Each UTF-8 response frame is capped at 256 KiB; stdout is limited to 4 MiB and
512 queued frames, stderr to 64 KiB. Raw stderr is counted and discarded. A single
writer handles bounded intents and checks their deadline before each write. Any
unconfirmed send remains uncertain; it is not retried. RPC responses and updates
are dispatched serially to the coordinator, including events preceding prompt ACK.
No raw exception, stack or stderr is returned in evidence.

Cleanup has a separate budget, at most 300 seconds, matching the owned transport.
Half is reserved for graceful cancel/session-close/runtime-close and natural exit;
the remainder is retained for Job cleanup and joining I/O threads. Close intents
are single-attempt. The reserved runtime shutdown acknowledgment alone never
establishes successful lifecycle. Success additionally requires:

1. Correlated terminal/model evidence from the coordinator.
2. Session-close and exact-boolean runtime-close acknowledgments.
3. Host exit and empty owned Job BEFORE forced cleanup, with observed exit code 0.
4. Both stdout and stderr EOF before disposing pipes; queued late frames are
   processed, so a trailing conflict cannot be hidden by closing read handles.
5. Verified owned handle cleanup and every registered I/O thread stopped.

`SupervisedACPTransport.observe` now exposes `host_exit_code` only after an actual
exit observation, and only for an integer returned by the retained owner. Unknown
or nonzero exit code cannot satisfy the clean-exit gate. Existing close facts and
ownership behavior are unchanged. A nonzero process exit can coexist with a model
terminal, but the engine returns indeterminate rather than calling lifecycle clean.

Failures do not erase partial evidence. If cleanup is unconfirmed, the transport
still retains its owner for explicit cleanup-only handling; never restart work.
Even a clean Job lifecycle is not proof that external tools/services have stopped,
so returned `liveness` remains `unknown` (except cancellation before launch) and
`retry_safe` remains false. No Bridge result, usage accounting or slot release is
written by this component. Permissions remain cancel-only until a real native
approval bridge is implemented; this is not the production tools policy.

## Verification scope

`test_beta15_owned_session.py` runs only self-authored Node fixtures through the
real Python coordinator, new runtime assembly and command shim. It checks success,
wrong-model blocking, ACK-only timeout, malformed/oversized frames, stderr limits,
late terminal conflicts, missing/nonzero exit status, no EOF, hung shutdown and
pre-launch cancellation. It also invokes the pure JS fixtures automatically.

Windows-specific tests use actual `SupervisedACPTransport` with that same synthetic
child, including an orphan descendant requiring forced Job cleanup. They are
explicitly skipped on other platforms and need Windows CI results before claiming
Windows integration acceptance. None of these tests imports WorkBuddy or exercises
an account, entitled model or real prompt. Product status remains NOT_READY.
