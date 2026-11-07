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
never inherits account configuration or Node options. No help invocation, custom
preload, login, credentials, model selection, session creation or prompt occurs.
Only one initialize frame is sent by the existing reviewed `phase` function.

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
