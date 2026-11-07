# Review-only cloud startup profile diagnostic

This adds a separate diagnostic, not a product-ready adapter. Existing official
smoke tool and workflow are unchanged. Do not push or dispatch until independent
source and exact-command review approves this new diagnostic. No local retry is
authorized; the previous local help/initialize permission was already consumed.

## Official evidence and bounded scope

- https://www.workbuddy.cn/docs/cli/startup-profiling
- https://www.workbuddy.cn/docs/cli/release-notes/v2.147.0
- Pinned official WorkBuddy 5.9.3 package/CLI 2.165 is acquired and verified using
  the existing reviewed `official_workbuddy_smoke.acquire` function.
- `bin/startup-profile.cjs`: 14142 bytes, SHA-256
  `f1ffe40a2f3a0e4e3e41178e9d19dca12f8c503bdcf240701beeb2af51a7b610`.
- The official collector is read-only source evidence, not copied into this repo.

The new workflow first runs offline fixtures and existing actual Windows process
lifecycle tests. Only success allows one vendor launch on disposable hosted
Windows. The wrapper rejects non-Windows/non-hosted execution. This environment
check is an accidental-run guard, not a security sandbox. It uses the previously
reviewed isolated HOME/config environment and owned Windows job lifecycle. It
never inherits account configuration or Node options. The controller invokes no
help, custom preload or login and reads no account credentials. It sends only one
initialize frame via the existing reviewed `phase` function, never session/new,
model selection or prompt RPCs. This limits our requests, not every internal vendor
side effect. Subsequent static tracing found that this bundle can enter an ordinary
request path that creates and saves a session before checking for an empty prompt;
actual internal session creation was not observed or ruled out. No account/session
files were inspected to resolve that uncertainty.

## Exact future command

The workflow fixes new, exclusive run root to
`$env:RUNNER_TEMP/native-harness-startup-profile-once` and summary to
`$env:GITHUB_WORKSPACE/startup-profile-summary.json`. Existing runner 7-Zip only.

```powershell
python code/tools/official_workbuddy_profile.py --seven-zip "$sevenZip" --run-root "$runRoot" --summary "$summary"
```

After the verified official executable and official entry path, arguments are:
`--startup-prof --startup-prof-dir <fresh root>/profile --startup-prof-name acp-init-once --acp`.
No markdown flag. Prefix order is required by the official entry. Intent markers
and fresh roots prevent reuse; do not delete markers, retry, or use another tag
after uncertain results. The wrapper never automatically deletes diagnostic roots.

## Safe output and interpretation

The only accepted raw filename is `profile/acp-init-once.json`. The parser runs
only after confirmed owned-job/process/handle/I/O cleanup. It rejects extra
members, links/reparse ancestors, hardlinks, identity changes, oversized input,
duplicate JSON keys, unknown schema fields, nonfinite numbers, invalid types,
counts and inconsistent timelines. Original JSON times are unrounded floating
milliseconds; arithmetic allows 1e-6 ms floating-point tolerance.

Only fixed status/reason values, finite audited phase names, aggregate counts,
timings, byte count and raw-file hash enter the reconstructed summary. Unknown
phases become `OTHER` with a count. Labels, source labels, warning text, metadata,
PID, version strings, paths and raw stdout/stderr are never exported. Dynamic
labels may contain secrets, even though the collector itself omits argv/env.
Only `startup-profile-summary.json` is uploaded. Never upload the raw profile,
run root, native output, or generic log directory.

A pending span means instrumentation was not closed before capture; it does not
prove the phase caused failure. Profile capture is independent of successful ACP
initialization. Empty frames do not prove product/protocol unsupported. Login and
model usability remain untested and product state remains NOT_READY. The existing
45-second phase limit may kill before the collector's unref'd 60-second timeout;
missing output is therefore valid diagnostic absence, not a root-cause finding.

Tests are synthetic only; the final accompanying implementation report records
actual Linux counts and Windows-only skips. No vendor execution is permitted by
merely creating or testing this source.


## Actual reviewed cloud result and subsequent static findings

The exact four-file diagnostic was reviewed and published at
`f2999bbf8429f8957ec0e939fd10bd3bb86d6538`. Its sole automatic push run was
[42422608177](https://github.com/meta-xucong/native-harness-api/actions/runs/42422608177),
job 128554059686, attempt 1. No manual dispatch or vendor retry followed. Windows
prerequisites passed (322 tests; 4 reported skips, including the controller class
without its explicit runtime). The separate normal push/PR matrices both passed;
each ran the retained-controller suite separately, 9/9 passing. Ubuntu: 331 tests,
292 pass / 39 skips. These synthetic checks do not establish WorkBuddy usability.

The sole vendor initialize still failed: zero stdout bytes, 92 stderr bytes,
process exited with code 0 without controller termination. Host exit, owned Job
empty, handles closed, I/O threads stopped and cleanup were all confirmed. Native
stderr text was not exported, so its meaning is unknown. `natural_exit=true` does
not distinguish explicit vendor process.exit(0) from event-loop exhaustion.

The official profile was captured and sanitized: exit-before-target at
3176.8689 ms, 20 spans, endpoint-ready at 1679.911 ms, one pending module_load span,
no application-ready milestone. Eleven dynamic phases were mapped to OTHER.
Artifact 13594247508 has one summary member only; outer 995 bytes / SHA256
`38959474d7c993f1d1208990f914e9b5caa63fb53cad815e72e3290dea9e2cf8`;
summary 1766 bytes / SHA256
`71dd5a16d1282b0c19e0ff85f47c3eddc828a3b8488e8d8c1d7b681d6c17bad5`.
The raw profile was not uploaded. A closed auth_init span is not login success;
endpoint-ready is not ACP-ready; a pending span is not a proven fault location.

Static review of pinned CLI 2.165 main bundle, SHA256
`c57a02ee3f2ef813004262cd003774a6e71bd2a6f5005481359301b407977ea5`,
found a specific routing discrepancy candidate:

- Plain --acp bypasses whole-stdin input reading, but the visible default strategy
  still chooses the noninteractive executor. endpoint-ready is emitted there after
  internal endpoint/trace setup. Dedicated ACP stdio selection is in the --serve
  strategy, not that default branch.
- The ordinary /internal/agent chain can create and save a session before its empty
  prompt guard. Empty prompt skips this controller's direct agentService.runDefault
  call, but preflight/capability/session hooks can still run. Therefore no global
  claim of zero internal execution or network activity follows from our one frame.
- The executor explicitly finalizes with exit 0 after its single request. This is
  compatible with the observed exit, but the exact final call path and response
  contents were not captured. Root cause remains NOT_ESTABLISHED. These source
  findings concern 2.165, not proof of identical behavior in installed 2.161.

The current [CLI reference](https://www.workbuddy.cn/docs/cli/cli-reference)
acknowledges combining --serve and --acp, but does not promise HTTP is disabled.
[HTTP API documentation](https://www.workbuddy.cn/docs/cli/http-api) describes
listener startup, generated/persisted gateway credentials, and process/file APIs.
The official [2.166 release notes](https://www.codebuddy.cn/docs/cli/release-notes/v2.166.0)
report a fix to unauthorized internal-interface command execution when --serve
ports are exposed. Our 2.165 package and installed 2.161 predate that release; this
is a version-risk signal, not evidence that either test environment was exploited
or that every bundle includes the same defect. Current safety guarantees must not
be backdated to them.

No --serve experiment, local retry, credential/session inspection, upgrade or
model test is authorized by these findings. A supported mode contract and the
listener/auth/session side effects need separate static review and a newly scoped
isolated experiment before any further vendor run. The project remains
NOT_READY_FOR_DIRECT_REAL_USE, not an unsupported-product verdict.
