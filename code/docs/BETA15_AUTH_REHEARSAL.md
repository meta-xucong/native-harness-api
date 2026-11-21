# Credential-free authentication boundary rehearsal

`tools/beta15_auth_rehearsal.py` is entirely self-authored Python. It has no
vendor import, native process, transport, HTTP/browser adapter, credential
store, file reader, helper or model path. It runs seven fixed in-memory event
sequences and emits bounded summaries. Do not connect it to a real peer.

```text
python tools/beta15_auth_rehearsal.py
PYTHONPATH=src python -m unittest discover -s tests -p test_beta15_auth_rehearsal.py -v
```

## What this tests

- Select only the uniquely advertised pinned WorkBuddy method identity
  `workbuddy-desktop` / `account-scoped-external-link`.
- A successful login request acknowledgment is not account readback.
- A separate *fabricated* account snapshot can complete the synthetic sequence,
  but can never make `native_account_verified` or `real_model_ready` true.
- Cancel without reading/opening/retaining URL or form content. A cancellation
  reply is **not** proof of rollback: the native provider can contact its service,
  launch a browser or persist a successful login before cancellation settles.
- Timeout, denial, unsupported protected restoration, duplicate/out-of-order
  events and late success cannot revive a stopped attempt or cause a retry.
- Secret-shaped/unexpected fixture fields and nonfixture identities are rejected;
  no account identity, raw error, URL or credential content appears in summaries.
- Cleanup produces exactly two synthetic close intents. This does not verify
  real client/server closure, native file cleanup or OS process ownership.
- Authentication results cannot schedule session/model work.

This intentionally narrow fixture is not a complete ACP schema validator, an
OAuth implementation, a native authentication provider, a production coordinator
or a generic parser for real account data. The fixed fictional identity prevents
using its success as real-user evidence. Intent labels are internal test markers,
not wire methods and not commands dispatched to the pinned runtime.

## Native evidence and current limits

The actual pinned beta15 Client exposes `client.auth.login` with a method id from
initialize's advertised catalog. The genuine WorkBuddy Product projects
`workbuddy-desktop`. Managed account initialization can restore native state
before login; avoiding a login call alone is not isolation.

The inspected pinned wire contract is:

| Method | Minimal request | Relevant response |
|---|---|---|
| `auth/login` | `{methodId:"workbuddy-desktop"}` when advertised | `{}` is valid, not account evidence by itself |
| `account/read` | `{}` | Unauthenticated: `{state:"unauthenticated",mode:null,account:null}` |
| `auth/status` | `{}` | `{authenticated:boolean}` |
| `auth/logout` | `{}` | `{}` can be valid; this rehearsal never calls native logout |

`account/updated` has the same snapshot shape as `account/read`, but an unsolicited
notification is not a completed read request. The minimal fabricated authenticated
fixture is `{state:"authenticated",mode:"managed",account:{id:"synthetic"}}`.
These account snapshots contain no credential field. The real schema permits
additional public account metadata; the rehearsal deliberately accepts only its
own fixed fictional id and subset. This table is source evidence, not permission
to issue these RPCs to a native account.

The native provider's request/openExternal/createRuntime injection seams are
internal and are not exported createHarness options. A caller must not fake a
desktop host identity, copy GUI credentials, reconstruct a protection bootstrap,
substitute another product's login or weaken storage protection to make it work.
Public createServer can host custom protocol modules, but its public entry has
eager reexports of native components. Any real Client/Server-only mock therefore
needs a separate import/call-graph and containment review. Changing an endpoint
to localhost alone does not contain unrelated native IO or network paths.

Officially documented choices include WorkBuddy's desktop browser login, a
registered OAuth/Open API application for local-assistant/cloud-task integration,
and a PC Share flow that only prefills input. These are distinct contracts, not
proof of a third-party protected-GUI-state handoff into beta15. The public
CodeBuddy Agent SDK's CLI login/cache API is also a different package/account
contract. No account, registration, signed link or official auth endpoint is
used by this rehearsal.

This slice changes no vendor-trigger workflow and permits only ordinary
vendor-free source CI. Native account/provider success, actual protected restore,
model entitlement, and user-PC compatibility remain unverified. The earlier
real Product-only success is separate and is not enlarged by synthetic tests.
