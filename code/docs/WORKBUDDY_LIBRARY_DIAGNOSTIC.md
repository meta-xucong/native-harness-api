# Review-only root beta.15 library handshake

New separate controller, not a retry of the CLI or installed diagnostic. Do not
publish or execute official runtime until exact source/command independent review.
No local PC permission is implied. Existing tools/workflows remain unchanged.

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
execution permission; no push/CI has occurred at this review checkpoint.
