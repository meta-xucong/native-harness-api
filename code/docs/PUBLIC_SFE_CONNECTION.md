# Public beta16 SFE connection seam (closed session admission)

The pinned public official beta16 Windows single-file executable has separately
passed anonymous initialize, WorkBuddy Product/catalog read, account/status read,
and natural EOF/zero-exit/empty-Job checks. That evidence is recorded in
PUBLIC_HARNESS_CATALOG_DIAGNOSTIC.md. It does not establish login, model execution,
native approval integration, or permission for commercial redistribution.

## Connection assembly

`tools/public_sfe_runtime.mjs` consumes an already-owned official MessageStream
and injected verified official Client loader. It uses the Client's custom
`transport` option only. It does not accept `cmd`/`url`, spawn processes, load
credentials, create a Harness, log in, provision accounts, or invoke sessions.
The SDK owns the outer Harness initialize projection and RPC encoding. The wire
is not raw ACP even though the version number is 1.

The verifier and loader are trusted owner dependencies, not arbitrary user
configuration. Production ownership must include the public package/binary pins,
complete tree validation and explicit isolated configuration. See the separate
public catalog assets verifier for those pins. No vendor files are copied here.
The transport uses the SDK Effect MessageStream contract, not a byte stream.
The owner also supplies an idempotent asynchronous transport-close callback;
calling an Effect value as though it were an awaited Promise is not cleanup.

Only bounded anonymous readiness evidence is returned. Session dispatch remains
closed even if a caller asks or supplies fabricated account data. There is no
`authenticated=true` escape hatch or raw-client return. A future real-account
admission path requires separate implementation, authorization and audit. Tests
here use self-authored Client/transport objects, not a vendor process or account.

## Session semantics and remaining owner integration

Existing beta15 command shim and SessionCoordinator are reusable building blocks,
not proof of beta16 wire equivalence. Beta16 may emit legitimate usage updates
without per-request IDs, including cumulative root-only accounting updates.
`native_harness_api.beta16_events` provides a separately bounded event projection
for that difference. It never invents request IDs or accounting and does not turn
cancel notifications into terminal outcomes. Result-bearing updates remain under
the existing coordinator's strict session/run correlation.

Do not send the existing OwnedSessionEngine shim protocol directly to SFE. SFE
has no `shim/close` RPC: stdin EOF initiates shutdown. EOF alone is neither a
successful model result nor empty-Job evidence. The existing engine's positive
shim ACK cannot be fabricated from an EOF. A future owned-process connection
must bridge those lifecycles explicitly, bound stdout/stderr and pending requests,
retain handles through uncertain cleanup, and verify native zero exit plus the
whole owned Job before cleanup can be confirmed. No such production launcher is
exposed by this slice.

SDK generic request cancellation uses `cancellationSignal`. Sending cancellation
only notifies the peer; it does not settle the pending request. The session
coordinator still requires real terminal evidence and never retries an uncertain
turn. Usage/liveness remain unknown where not separately proven. No production
channel, HTTP route, paid service, release readiness or actual model capability
is enabled by these modules.

## Verification

Run `PYTHONPATH=src python -m unittest discover -s tests -v` from `code/`.
The public runtime test wrapper executes only its self-authored Node fixtures.
The projection tests use synthetic notification data. Neither test suite starts
WorkBuddy/SFE, accesses accounts, or issues an inference request.
