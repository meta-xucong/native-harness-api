# Vendor-free beta.15 single-session coordinator

This is an unconnected implementation slice, not a ready WorkBuddy provider. It
uses the pinned beta.15 API facts underlying `BETA15_REDUCER.md`; no vendor code
was copied, imported or executed. It is not wired into Facade/Node or enabled in
any model route. Original-fs/ASAR diagnostic and native account work are separate.

## Implemented

`beta15_session.py` adds a pure single-attempt coordinator around the reducer.
A caller can ask it for new-session, selection, prompt, cancel and close **intents**.
It performs no I/O and has no authority to execute those intents. Session creation
response binds the session ID; one correlated selection response must read back the
advertised model before a prompt intent can exist. Prompt reducer registration
precedes that intent, allowing bounded events-before-ACK buffering. An insertion
ACK never finishes the turn. Config updates from other sessions are ignored;
loss of selected-model evidence invalidates the attempt. Duplicate, malformed,
uncorrelated and out-of-order responses invalidate rather than retry.

`decode_frame` accepts one complete bounded UTF-8 object, rejects duplicate keys at
every depth, non-finite numbers, non-object roots and invalid Unicode. It is not a
stream framer; a future transport must enforce total message size while accumulating
bytes, not only after buffering them. Decoder output still needs RPC/session
routing. Responses and updates must be dispatched serially on the same bound
connection; this component is not thread-safe and IDs restart per object.

Session cancel is a notification intent, not proof cancellation was accepted.
Session close response accepts only `{}` or a `_meta` object/null in this narrow
projection. Rejecting other fields is our conservative compatibility policy, not
a requirement of the vendor schema (which permits additional properties). It is
not a complete v2 validator. The close response is only recorded separately; it is neither terminal model
output nor OS cleanup proof. Close may still be requested and acknowledged after
failure, but no later selection or prompt can be admitted after close intent.
Disconnect/timeout/invalid clock remain sticky. Unknown usage and liveness remain
unknown; `retry_safe` is always false. The top-level outcome takes precedence over
any nested turn snapshot. No result is written to the immutable Bridge ledger.

The coordinator currently denies every tool permission because no approval UI or
authorization bridge exists in this slice. This is a test foundation, not a change
to the product requirement to preserve native approvals and tools. It neither
suppresses tools in the real SDK nor authorizes unattended inference.

## Required integration work before use

- Bind an actual shim transport to the audited loader and vendor-managed account
  path, after separate readiness and authorization checks. Do not infer native
  authentication or model entitlement from a catalog or synthetic fixtures.
- Enforce absolute, empty, owned workspace paths and stable connection identity.
  The coordinator only bounds the cwd string and does not inspect the filesystem.
- Persist the original task/grant execution intent before any native send. This
  object is not a durable journal, recovery mechanism, queue or idempotency key.
  Reconstructing it after a crash cannot authorize another prompt.
- Implement bounded OS ownership, close/cleanup and recovery evidence separately;
  RPC close alone cannot release a Bridge execution slot.
- Implement native permission presentation/approved replies, cancellation and
  output-size handling. Never convert native tools into downstream remote calls.
- Keep formal synthetic-only production admission gates closed until real tests
  of accounts, model selection, terminal output, isolation and recovery pass.

Tests in `test_beta15_session.py` use only self-authored decoded protocol objects.
They establish coordinator behavior, not real SDK or WorkBuddy functionality.
