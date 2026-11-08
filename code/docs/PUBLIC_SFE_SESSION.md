# Public SFE local session / owned-process interface

This is an implemented local execution interface with synthetic regression tests,
not a claim that a real user account or model has passed acceptance. The user
performs login and actual WorkBuddy testing after deploying the completed local
package. No developer-side login is required to implement these interfaces.

## Protocol

`native_harness_api.public_sfe_session.PublicSfeSession` is an IO-free, single-use
outer Harness JSON-RPC state machine. Construct it with a finite monotonic
`deadline` and `expected_account_digest`. `start()` and `receive(frame)` return
tuples of NDJSON byte intents; the caller sends each at most once. Bootstrap
performs initialize/initialized, public Product read, managed account read, and
auth/status. It admits sessions only when the WorkBuddy Product matches, the
account is authenticated and its stable identity digest matches the local profile,
and status is authenticated. A profile digest is a binding, not proof of login.

`account_identity_digest(identity)` supplies the shared versioned digest contract
for the local login/profile integration. It hashes only stable identity fields,
rejects credentials/unknown fields, and excludes display names and avatars. The
protocol retains no account identity payload. Authenticated snapshots in tests
are synthetic, not vendor/account verification evidence.

After bootstrap, `new_session(workspace)`, `select_model(model_id)`, and
`prompt(text)` each produce one intent. Selection requires exact native catalog
membership and readback before prompt. The SessionCoordinator still correlates
request/root/session evidence, handles event-before-ACK, and keeps prompt ACK
separate from terminal completion. Beta16 usage-only notifications pass through
the bounded projection; usage remains unknown.

`cancel()` is a notification, never a terminal result. `close_session()` is an RPC,
but its ACK does not establish process termination. Unexpected RPC responses,
duplicate IDs/keys, malformed frames, conflicting terminal events, Product/account
changes or budget overruns become sticky uncertainty. Reverse permission and
elicitation are cancelled and fail the turn rather than silently auto-approved.
Tasks needing interactive native approval therefore remain unsupported by this
noninteractive session interface until that UI is integrated; they are not
silently retried or granted full access.

## Owned process

`PublicSfeOwnedSession(transport).run(command, cwd, env, *, workspace, model_id,
prompt, expected_account_digest, timeout=120, cleanup_timeout=10,
cancellation=None)` uses an explicitly injected owned transport. Production
Windows callers use `SupervisedACPTransport`, backed by an owned Windows Job.
The caller verifies complete pinned SFE assets and the isolated profile, acquires
the single execution slot, and persists durable intent **before** starting this
method. The engine does not authenticate, select arbitrary executables, recover
by PID, or retry after an ambiguous start.

The owner bounds stdout frames/total output, discards and bounds stderr, and
keeps cancellation and cleanup available independently of a pending prompt.
After session close it closes stdin to request the SFE's actual EOF shutdown.
There is no `shim/close` request or fabricated shutdown ACK. Normal success needs
terminal/model evidence, session-close ACK, natural zero exit, whole owned Job
emptiness, both output EOFs, stopped IO threads, and confirmed handle cleanup.
Forced cleanup is not natural successful completion. Late queued evidence is
drained before returning the result. Unknown cleanup remains indeterminate and
must not release a production slot solely from a timeout or lease expiry.

Results expose protocol/session evidence, bounded cleanup facts and explicit
`retry_safe: false`. Liveness is not upgraded to a scheduler-authoritative stop
fact. `cleanup_confirmed` is an owned-process observation, not authorization to
retry or execute another task. The provider/ledger owns admission and recovery.

## Testing and deployment boundaries

Unit tests and self-authored child processes cover anonymous/wrong-account
rejection, prompt ACK without completion, successful terminal correlation,
late conflicts, nonzero exit, cancellation and IO/cleanup failures. Windows Job
fixtures are explicitly skipped off Windows; actual CI results must be reported
separately. They execute no vendor binary or account/model request.

These interfaces alone do not update the old beta15 download kit. Public SFE
packaging must preserve the complete pinned official runtime tree and licenses,
select the correct entry, and integrate the user-run isolated profile/login flow.
Real login/model/tool compatibility remains NOT_RUN until local user acceptance.
