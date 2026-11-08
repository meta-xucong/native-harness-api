# Root beta.15 virtual-ASAR library diagnostics

The earlier sections preserve historical experiment scopes and results. The
current proposed scope is **revision 5, external-account Product-only**, below.
Each changed executable experiment needs exact-source independent review before
its single push-triggered cloud run. No user-PC/account/model permission follows.

New separate controller, not a retry of the CLI or installed diagnostic. Do not
publish or execute official runtime until exact source/command independent review.
No local PC permission is implied. Old CLI smoke/profile workflows remain unchanged; only the dedicated library workflow is updated.

Pinned root @genie/agent-harness 0.2.0-beta.15 comes from the existing verified
WorkBuddy 5.9.3 installer/payload (not npm or a nested CLI copy). The complete
archive is checksum verified and safely extracted using the reviewed acquire
function. Runtime additionally pins WorkBuddy.exe SHA-256
bbf504003825e0e22dd8c401b396d79db78ec603558088565d1b2e81561704a0 and size204590360;
All 6166 regular extracted files additionally match canonical tree SHA-256
5c1f3ccee6ac24a630e9f18b3a49f559b3fbad76c1ec47a2a2448cb9b6f54ab2
(sorted UTF-8 path, NUL, decimal size, NUL, file SHA, LF). Links/reparse/extra files
are rejected. package.json/public.js hashes are checked. No vendor code patch/preload.

Root createHarness({configDir,strictMcpConfig:true}) and
createClient({account:{mode:'external'},product:{mode:'external'}}) make exactly
one connect(harness): initialize request plus initialized notification. Client and
harness close are awaited, including harness.closed. The controller invokes no auth/provision/read, session, query, model or prompt
APIs/RPCs, creates no extra listener and supplies no product identity override.
This restricts controller calls, not every implicit vendor lifecycle effect.
The pinned external-profile validation and managed-discovery/preparation guards
were traced in the private BETA15_EXTERNAL_HANDSHAKE_REVIEW.md source audit.
No unconditional managed read was found on inspected constructor/close paths.

Hosted Windows guards plus explicit token-free environment and fresh HOME,
USERPROFILE and config directories reduce exposure. Driver verifies homedir before
vendor import. This is NOT a filesystem/network sandbox: native KnownFolder APIs,
implicit network, settings/storage/MCP/telemetry lifecycles may have side effects.
No firewall, new OS user or AppContainer is configured. Outbound network is not
blocked; no zero-side-effect claim is permitted.

Python supervisor uses OwnedWindowsJob directly, NOT the old phase function.
Stdin is closed without writing. Both streams are drained/discarded with bounded
byte counters; raw output never reaches artifacts. Timeout/overflow fails, stops
the owned job and verifies host/job/handles/IO cleanup. Only then may the fixed
new output/library-summary.json be parsed. Exact numeric/boolean schema, bounded
size, duplicates/nonfinite/type checks and path identity/link checks apply.
Partial DTO or uncertain cleanup is failure. Successful natural exit0 plus pre-cleanup natural_job_empty and complete
DTO means only handshake/lifecycle success, not GUI login/model/product readiness.

Future reviewed command (not executed by this document):
`python code/tools/official_workbuddy_library.py --seven-zip <runner7zip> --run-root <RUNNER_TEMP>/native-harness-library-once --summary <GITHUB_WORKSPACE>/library-sanitized-summary.json`.
Fixed attempt marker and fresh directories enforce no overwrite or same-root retry.
The only eventual upload is the sanitized summary. Never upload extraction, raw
logs, home/config/workspace, account data or vendor error strings.

Offline Python fixtures and self-written Node fake-API tests precede any vendor
attempt. Real Windows lifecycle prerequisites must pass separately; Linux skips
are not Windows evidence. The new push-only workflow filters only new library files, and requires Node fake
fixtures plus full Windows tests before one attempt. Merely writing it is not
execution permission; This corrected virtual-ASAR revision has not been published or executed.


## Loader revision 2: virtual ASAR (one new bounded attempt)

The historical physical-entry trial (source 79fb80a) failed during import in about
146 ms, before harness/client construction. Its raw exception was intentionally
not retained; do not label it an established module-not-found error. Preserve its
source and artifact unchanged. Static evidence found bare dependencies packed in
app.asar, absent from the physical app.asar.unpacked sibling tree. Native GUI uses
a package-name import within the virtual archive context.

This revision keeps the pinned 6166-file tree and executable pins unchanged. The
controller additionally verifies the full app.asar SHA256, bounded pickle/JSON
header, and exact public.js/package.json unpacked flags, sizes and integrity hashes
against paired physical bytes. The Node driver verifies virtual entry bytes with
the official runtime's built-in filesystem before importing the fixed ASAR URL.
No NODE_PATH, copied dependencies, loader patches, vendor changes or reconstruction.

Only the existing library workflow is changed. Fixed run root:
RUNNER_TEMP/native-harness-library-asar-once. Fixed public artifact/summary:
library-asar-sanitized-summary / library-asar-sanitized-summary.json.
The child-only file remains output/library-summary.json. Existing directories or
outputs consume the attempt; no retry, changed tag, fallback loader or local-PC run.

DTO schema 2 adds entry_verified and numeric error_class. Only allowlisted own data
error.code values map to classes 1..7; other failures map to 8, success to 0.
No message, stack, exception paths, tokens or vendor output are emitted. Classes:
1 ERR_MODULE_NOT_FOUND, 2 MODULE_NOT_FOUND, 3 ENOENT, 4 EACCES, 5 EPERM,
6 ERR_UNSUPPORTED_DIR_IMPORT, 7 ERR_UNKNOWN_FILE_EXTENSION, 8 OTHER.
Failure stage remains finite 0..7; entry_verified distinguishes pre-import entry
verification from import failure. Controller metadata records loader_revision=2
and archive_pair_verified=true only after successful preflight.

Exact six-file freeze requires independent review before commit/push/execution.
Only self-authored fixtures and offline tests may run before that review. An import
success or empty-account handshake is not proof of account reuse, model inference,
or production readiness. OwnedWindowsJob is lifecycle containment, not a sandbox.

## Loader revision 3: separate physical and virtual filesystem APIs

Revision 2 at `1e106e5470c9621f48b9c805135188aa824bae86` completed its one
hosted attempt. Parent preflight passed, but the native child exited nonzero with
no valid DTO (stdin/stdout 0 bytes, discarded stderr 158 bytes). The owned Job was
naturally empty and all cleanup checks passed. The result does not locate the
child failure or prove whether import/handshake occurred. No raw error was saved.

A source-level defect was then established independently of that unknown runtime
error. The pinned executable contains Electron/37.10.3. Its matching upstream
`asar-fs-wrapper.ts` and `Archive::Stat` treat the archive root as a virtual
directory. Revision 2 incorrectly used that patched filesystem to demand a
physical regular file and read the whole archive checksum. The versioned
[Electron documentation](https://github.com/electron/electron/blob/v37.10.3/docs/tutorial/asar-archives.md#treating-an-asar-archive-as-a-normal-file)
explicitly specifies built-in `original-fs` for whole-archive checksum reads.

Revision 3 requires that exact Electron runtime version and resolves only its
built-in `original-fs`. Physical path/type/link checks, physical entry/archive
hashes, and output reservation use its unpatched APIs. Only the virtual entry
read uses ASAR-aware `node:fs`; import still uses the same pinned virtual URL.
No global `process.noAsar` switch, loader patch, dependency copy, or vendor-byte
change is introduced. Physical checks now run inside the bounded pre-import
verification operation after safe output reservation, so caught verification
failures can produce the existing schema-2 DTO rather than bypassing it.

The fixed new root is `RUNNER_TEMP/native-harness-library-original-fs-once`;
the sole summary/artifact is `library-original-fs-sanitized-summary.json` /
`library-original-fs-sanitized-summary`. Parent metadata records loader_revision=3.
The earlier consumed run roots are not reused. The controller still calls only
one external initialize/initialized connection and close, without account,
session, model, or prompt APIs. This changed experiment requires its own exact
source/command review before publication and one new hosted attempt. At this
source checkpoint it is not yet executed. No local-PC run is authorized.

Self-authored fixtures distinguish physical file versus virtual directory,
exercise separate byte readers, reject mismatches/missing API objects, and prove
that failed physical verification blocks import and returns a finite failure.
They do not substitute for actual Electron or WorkBuddy execution.

## Loader revision 4: finite initialization evidence

The original-fs correction plus canonical fixture path was executed once at
`98b8704be6e8cd4cac7d870309cc7cb565533a1a`, hosted run `42430640869`.
Windows prerequisites passed (27 Node scenarios; 371 Python tests, 4 reported
skips, with all four real Windows library lifecycle tests actually passing).
The actual schema-2 DTO now confirms entry verification, import, harness and
client construction. It reports stage 4 / generic error class 8 at 4264 ms,
without confirmed connection. Stage 4 includes both connect and the following
protocol-version check; no authentication diagnosis is justified. Client and
harness close/settlement and all owned Job/host/handle/IO cleanup checks passed.
Only the 672-byte sanitized summary was exported; no native exception was saved.

Subsequent pinned-source review identifies a concrete compatibility requirement:
`lib/agent/v2.js` selectProfile rejects executor-backed MCP without both
`elicitation.form` and `elicitation.url` client capabilities. The default
application services supply that MCP integration. The old client advertises
neither. This inspected deterministic rejection path is not yet the captured
prior exception; do not relabel the prior generic error as a proven cause.

The corrected client advertises `elicitation:{form:{},url:{}}` and registers
`elicitation/create` before connecting. Its handler never reads request params,
opens a URL, renders a form, collects input, or accepts anything. It sets the
bounded numeric `elicitation_seen` marker to 1, aborts the existing harness
controller, and returns only `{action:'cancel'}`. A returned cancellation is not
proof it was delivered. Any observed elicitation makes the diagnostic fail,
even if connect returns or an event arrives during cleanup. Native permission
handling remains the SDK's default cancellation; no allow_always or new request
method is added. The external account/product profiles remain unchanged.

The loader/pins, initiated vendor calls, deadlines, isolation environment and
owned-process cleanup are unchanged. Schema 3 adds
`connect_returned` to distinguish a rejected connect from the subsequent
protocol assertion. False does not say which native initialize/initialized
handler was reached. It also recognizes the pinned SDK's own numeric RPC error
codes, which the old string-only classifier reduced to generic class 8:

- 9: -32700; 10: -32600; 11: -32601; 12: -32602; 13: -32603.
- 14: -32000; 15: -32800; 16: -32001; 17: -32002.

These are observations of codes, not diagnoses. In particular -32002 is used for
different meanings by the Harness and SDK. Existing classes 1..8 are unchanged.
`error_detail` is 0 for unmatched errors, or a finite exact-match bucket from
pinned source: 1 invalid Harness initialize response; 2 missing SDK client
context; 3 client connect timeout; 4 client closed; 5 harness closing;
6 Harness connection closed; 7 RPC transport closed; 8 RPC transport failed;
9 internal error; 10 invalid params; 11 missing executor-backed MCP elicitation
capabilities. Matching reads own data descriptors only,
never getters, prototype fields, cause/data/stack, or arbitrary string conversion.
Only the integer bucket leaves the process; no message or substring is exported.
This is not a claim that every possible initialization error is covered.

A separately reviewed single cloud experiment uses the fresh fixed root
`RUNNER_TEMP/native-harness-library-connect-evidence-once` and the sole fixed
summary/artifact `library-connect-evidence-sanitized-summary.json` /
`library-connect-evidence-sanitized-summary`. Metadata records loader_revision=4.
No consumed root is reused, no automatic retry, alternate entry, permissions,
account/session/model/prompt request, user PC access, or deployment is introduced.
As before, empty hosted HOME/config and owned Job are not a network/filesystem
sandbox. The exact source and command require independent review before this
new automatic push-triggered experiment. Handshake success alone would not
establish account reuse or real-use readiness.

### Revision 4 actual result: native initialization established

Reviewed source `2274a43544d4737fa57ba475877ddfe249ace196` completed exactly one
[hosted experiment](https://github.com/meta-xucong/native-harness-api/actions/runs/42431493208),
push event / attempt 1. Actual Windows prerequisites passed: 32 self-authored Node
scenarios and 372 Python tests with 4 reported skips; all four Windows library
lifecycle tests actually passed.

The downloaded artifact's sole schema-3 summary reports successful pinned entry
verification, import, harness/client construction, returned connection and
protocolVersion 1. `elicitation_seen=0`: this diagnostic received no elicitation.
Client/harness close and harness settlement all passed. The host exited naturally
with code 0, the owned Job was naturally empty, and host/Job/handle/IO cleanup
all passed. The routine elapsed 4400 ms, including setup/cleanup, not just connect.
Stdin/stdout were 0 bytes; 158 stderr bytes were discarded and are not interpreted.

Artifact 13597836437: ZIP 517 bytes, SHA256
`8603aac9462c2d629b569a05f88e7f83e7eef72652fbcfbfc26852dab1e349bb`.
Its only member is `library-connect-evidence-sanitized-summary.json`, 729 bytes,
SHA256 `ef2bc303d3b550b6da282814fda930d7ad8b3cf4d095b300543c11c0e3c34373`.
This establishes this fixed official library's empty-account in-memory handshake
and bounded owned cleanup. It does not establish GUI login inheritance, the user's
older installed version, native account/Product readiness, session/model/prompt
execution, provider model identity, or an enabled API channel. The successful
capability correction is consistent with the static mismatch; the historical
failed run's exact exception remains unknown. No retry, local-PC action, account
inspection, real model call, merge or deployment occurred. Product remains
NOT_READY_FOR_DIRECT_REAL_USE.

## Revision 5: genuine WorkBuddy Product, external account

This is a new bounded cloud experiment, not a managed-account catalog or login
test. Root coordination authorized this Product-only step after exact source
review. It retains the pinned official CN WorkBuddy 5.9.3 distribution, physical
`original-fs`/virtual-ASAR loading, external account mode, form/url cancellation
policy, deadlines and owned Windows Job cleanup. No vendor bytes are changed.

Before vendor import, the driver opens the fixed official
`resources/app.asar.unpacked/cli/product.json` through `original-fs`. It rejects
links/nonregular or oversized files, compares open-handle identity, bounds the
read to 1 MiB, and hashes the **same bytes** subsequently parsed as JSON. Expected
SHA256 is `a73bea91defd7d5f38d5672c19111681ba52244ee8b9b64cc59dc3e3992eed61`.
The complete extraction tree was already independently pinned by the parent.
The object must retain WorkBuddy name/application, `workbuddy-desktop` auth id,
`account-scoped-external-link` auth type and `workbuddy` auth platform. Only the
five loader-control fields `include`, `productConfigEnv`, `productConfigPathEnv`,
`replacements`, `mergeStrategy` are removed. Version is set to the pinned ASAR
application's actual `5.9.3`. All original endpoints and other Product data remain.
The overlay envelope is passed explicitly to `createHarness({productConfig})`.
This is public Product data, not a copied account, token, protection capability,
private host identity or authority snapshot.

Static call-site review establishes that explicit Product input uses an empty
resolver base. Omitting Product would use the root library's default **CodeBuddy**
configuration. An explicit name/auth-only object would instead lack required
routing data; neither is a substitute for the full official WorkBuddy projection.
The native GUI uses this same five-field stripping/version-fill projection shape,
but that precedent does not transfer its authenticated authority.

Client modes are now `{account:{mode:'external'},product:{mode:'managed'}}`.
In the pinned auth runtime, external account skips native managed account
discovery/preparation, regardless of Product mode. No authentication methods
are advertised in this profile. After the existing one connect/initialize pair,
the diagnostic requires absent or empty `initialization.authMethods`, then calls
exactly **one `client.product.read({})`**. This read returns a public anonymous
Product snapshot, not an account or model catalog. It must report state `ready`,
mode `managed`, product/application `WorkBuddy`, and version `5.9.3`.
No login/logout/authenticate, account read/restore/provision, Product provision,
workspace preparation, session creation/read/catalog, model/query/prompt, MCP
mutation, browser opening or accepted elicitation is initiated by the controller.
Empty authMethods means none advertised, not proof of managed login availability.

Schema 4 adds five strict booleans: `product_config_verified` (pinned input
verified, not a claim that the vendor parser has already run), `auth_methods_empty`,
`product_read_returned`, `product_ready`, and `product_identity_verified`.
All must hold, together with prior handshake/close evidence, for success.
New failure stage 8 covers Product read/validation; deadline remains stage 7.
The Python parent validates evidence ordering and finite error mappings. Only
these booleans and bounded numbers leave the child, never the Product response,
endpoints, URLs, account data, raw exceptions or native output.

The startup surface is **not side-effect-free**. Settings initialization still
reads native USER settings and can create adjacent locks/directories or migrate
credential fields if present. `configDir` alone does not cover every native path;
customization snapshots can affect paths. A fresh process and allowlisted env
exclude ambient tokens/helpers/product-spill/proxy/host-bootstrap variables.
Fresh HOME/USERPROFILE, both app-data roots, config, temp and workspace are
created; child checks Node homedir and tmpdir plus the exact environment/path
identities before vendor import. This reduces exposure but is not a filesystem
or network sandbox. Native/transitive imports, platform APIs, telemetry and other
implicit activity remain incompletely characterized. Outbound is not blocked.
No protected-store restoration or protection downgrade is allowed.

One reviewed run uses fresh fixed root
`RUNNER_TEMP/native-harness-library-product-once`, sole public summary
`library-product-sanitized-summary.json`, artifact
`library-product-sanitized-summary`, and metadata `loader_revision=5`.
The child-only DTO remains `output/library-summary.json`. Windows self-authored
fixtures/full test prerequisite must pass before vendor execution. No retries
reuse the root; raw logs/profiles/extraction are never artifacts. Ordinary adapter
source CI is separate. This document records the intended revision, not a run
result. Even success would establish only this pinned CN Product projection and
public readiness with no account, not GUI account reuse, installed 5.8.4 or INTL
compatibility, entitlement, model success, or an enabled real-use API channel.
