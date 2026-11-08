# Review-only root beta.15 virtual-ASAR library handshake

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
