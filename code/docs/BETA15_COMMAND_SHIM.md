# Vendor-free beta.15 connected-client command shim

`tools/beta15_command_shim.mjs` imports no vendor code and has no CLI/main, SDK
loader, process launch, network listener or account configuration. Its injectable
`createCommandShim` takes a client whose startup/authorization belongs to a
separate owner. Tests use only self-authored client objects. This module is not
wired into production channels and does not establish real model functionality.

The real owner must install the pinned required elicitation cancellation handler
before `client.connect`, as established by the separate handshake diagnostic.
The handlers here keep permissions cancelled and elicitations `{action:'cancel'}`
after construction; their arguments are not inspected. This is the current test
foundation, not a substitute for the required native approval UI.

## Contract

Inputs are decoded plain JSON objects; the caller must enforce UTF-8 framing and
reject duplicate keys before dispatch. `dispatch()` returns a Promise that tracks
only handling of that command, never the end of a model turn. The owner must not
serialize reading the next command behind this Promise: cancellation/close must
remain available while a prompt insertion response is pending.

Commands are `{id,method,params}` with positive safe-integer unique IDs below
2147483647. Supported requests: `session/new`, `session/set_config_option`,
`session/prompt`, `session/close`. Cancellation is notification
`{method:'session/cancel',params:{sessionId}}` with no ID. Only one new-session,
selection, prompt, cancel and close can be attempted. Exact cwd must match the
owner-supplied expectedCwd; the shim does not inspect or authorize the filesystem.
A model must appear in the catalog and be read back before a prompt is admitted.
A model change invalidates this attempt; it does not select or retry another.

The reserved shutdown RPC is
`{id:2147483647,method:'shim/close',params:{}}`. It fences new native commands
synchronously and invokes injected `closeRuntime()` once even with a pending
native call. The callback must resolve literal `true` only after client/harness
closing has settled. Then the shim returns `{id:2147483647,result:{closed:true}}`.
Callback failure gives a generic error. Hung callbacks remain the OS owner's
responsibility; this acknowledgment is not evidence of Job/process termination.

`emit(line)` receives complete JSONL synchronously. It may return undefined or
true, otherwise it is treated as a transport failure. The owner must provide
bounded stream buffering/backpressure; a failed emitter is never retried. Outputs:

- `{id,result}` or `{id,error:{code:'NATIVE_RPC_FAILED'}}` (invalid admission can
  instead be `INVALID_COMMAND`). No raw error message, stack or path is emitted.
- `{method:'session/update',params:{sessionId,update}}` only for the bound session.
  These are local protocol data, not sanitized public diagnostics. Do not upload
  or log them indiscriminately. Event-before-response order is preserved.
- `{method:'shim/fault',params:{code}}` with a fixed finite fault enum. Failure is
  sticky. If the writer itself failed, no additional output can be guaranteed.

Each snapshot is bounded to 256 KiB, nesting to 64, and values to 20,000 nodes.
Outputs have 512-frame/4-MiB total budgets with room reserved for one fault frame.
Accessors, toJSON callbacks, cycles, undefined, non-finite numbers, malformed
Unicode and non-JSON objects are rejected, not stringified as dynamic errors.
These narrow projections may reject vendor extensions; they are not the complete
v2 schema. Forwarding a prompt ACK never fabricates a terminal outcome, token
usage or cleanup guarantee. The separate Python coordinator correlates root-run
updates and the process owner enforces deadlines and durable execution intent.

No application deadline, durable journal, recovery, model accounting, real SDK
readiness or native account proof is implemented in this module. Recreating a
shim cannot authorize retrying an uncertain native operation.

## Injected external-only runtime assembly

`beta15_external_runtime.mjs` composes the already-tested startup contract without
importing the vendor bundle itself. `createExternalRuntime` requires `verifyEntry`
(explicit true), `loadApi`, `configDir`, `expectedCwd` and `emit` callbacks/values.
Only after verification may the injected API loader run. The assembly uses
`createHarness({configDir,strictMcpConfig:true,signal})`, creates an external
account/product client with form+URL elicitation capabilities, installs cancel-only
permission/elicitation handlers before connect, and requires protocolVersion 1.
Any elicitation during initialization rejects setup without inspecting its input.
It cannot switch to a managed account or supply provider credentials. The pinned
client rejects duplicate request handlers: preconnect handler unsubscribe functions
are verified and invoked synchronously before the shim installs its handlers. This
behavior was read from client.js SHA256
`9ad4535f109270b404ed393d3774e41eb61babee5b68caa817399235a45c9984`;
fixtures reject duplicate registration rather than assuming replacement.

Initialization and client/harness close waits are bounded. Late-created harnesses
are retained for cleanup; resource close is issued at most once even when a prior
wait timed out. Shutdown asks client.close, harness.close and harness.closed to
settle, then the command shim drains pending native RPC promises before emitting
its positive shutdown acknowledgment. A hung native RPC thus withholds the ACK;
the Python OS owner still supplies the ultimate process deadline. Any notification,
permission or elicitation callback arriving after the shutdown acknowledgment emits
a sticky `POST_SHUTDOWN_ACTIVITY` fault, rather than hiding potentially conflicting
evidence. Permission/elicitation still returns cancellation without reading input. Out-of-band
runtime.close fences the shim as stopped and reuses the same cleanup Promise.
No close result proves process termination or supplies production readiness.

All real path ownership, frozen bundle verification, empty config/workspace setup,
native launch authorization and OS isolation remain responsibilities of the
injected owner. A dishonest fake verifier or injected API is not a trust boundary.
The assembly does not expose a vendor executable/main or a complete production
runner. Its tests supply only self-authored fake APIs and perform no account or
model calls.
