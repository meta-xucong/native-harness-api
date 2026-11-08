# Public Harness beta16: anonymous WorkBuddy catalog candidate

This is a bounded hosted-Windows diagnostic, not a login tool or production
channel. It uses the public `@tencent-ai/agent-harness-win32-x64` **0.2.0-beta.16**
distribution, rather than transplanting its libraries into a WorkBuddy desktop.
The public beta channel, stable channel, and desktop-bundled beta15 are distinct
builds. A higher-looking version does not establish compatibility or chronology.

## Why investigate this path

The official public beta16 package includes a documented explicit WorkBuddy
Product integration surface. Static inspection of its corresponding JavaScript
runtime does not contain the missing composer dereference found in the reviewed
desktop beta15 standalone factory. This removes that specific source objection;
it does **not** prove authentication, server acceptance, GUI credential handoff,
or equivalence between the JavaScript bundle and compiled Windows executable.

Public sources:

- https://registry.npmjs.org/@tencent-ai%2fagent-harness
- https://registry.npmjs.org/@tencent-ai%2fagent-harness-win32-x64
- The integrity-verified main package's `docs/product.md`, `docs/distribution.md`
  and `docs/protocol.md` specify explicit Product input, complete platform
  `bin/` retention, stdio initialization, and natural cleanup on stdin EOF.

## Exact diagnostic scope

1. Validate a disposable hosted Windows runner and attempt number 1. Exclusively
   create one attempt directory and intent record. There is no automatic retry.
2. Download fixed official artifacts, verify their pinned SHA-256 values, and
   inspect bounded archive inventories before extraction. Preserve complete SFE
   and Node distributions, including their licenses and native resources.
3. Obtain the already-pinned CN WorkBuddy 5.9.3 outer Product as **data** from its
   official installer. Do not run that desktop executable. Apply the same full
   supported overlay used in the earlier Product experiment: remove loader
   directives and record the truthful desktop Product version. Do not replace
   the SFE's packaged CodeBuddy files or copy accounts.
4. Create new empty HOME/config/APPDATA/LOCALAPPDATA/TEMP/workspace roots and an
   allowlisted environment. Supply the pinned Node through the official public
   launch environment; remove Electron run-as-node. Keep the default truthful
   Agent Harness host identity, not a fabricated desktop identity. Disable
   telemetry using its documented environment option.
5. Spawn the complete official SFE with `--stdio --product-config <new file>`
   inside an owned Windows Job. Send only `initialize`, `initialized`,
   `product/read`, `account/read`, and `auth/status` in that order.
6. Verify protocol version, server identity, exact public WorkBuddy auth catalog,
   ready managed CN WorkBuddy 5.9.3 Product, and anonymous account/status. Reject
   malformed, duplicate, unexpected, oversized or out-of-order frames. Any
   elicitation/permission request receives at most a fixed cancellation and
   ends the experiment, never authorization.
7. Close stdin, observe bounded natural process/Job exit, then perform bounded
   owned cleanup if needed. Protocol completion, natural exit and cleanup are
   separate facts. Forced cleanup is not a successful natural lifecycle.

No `auth/login`, `auth/logout`, account/Product provision, session creation,
prompt, model discovery/inference, network listener or existing-account access
is implemented. There is no arbitrary method or endpoint option.

## Pins and output

The source asset module fixes archive URLs and SHA-256 values:

- SFE archive: `e1e47c6ee6364251fcf7dffece12fed8287af6a6866dfa1192a40f9180585bda`
- SFE executable: `5a55b64f73eadc515120212f117cdfe4b25d4633ab4a57717aab2ba90b6e97b2`
- Official Node 22.16.0 Windows x64 ZIP:
  `21c2d9735c80b8f86dab19305aa6a9f6f59bbc808f68de3eef09d5832e3bfbbd`
- CN outer Product: `a73bea91defd7d5f38d5672c19111681ba52244ee8b9b64cc59dc3e3992eed61`

Node's official checksum list must also match the fixed ZIP pin. Acquisition
does not run npm, installation hooks or downloaded installers. New tar/ZIP paths
reject traversal, Windows aliases, links and devices; writes are exclusive.
Previously audited 7-Zip acquisition is reused only for the Product source.

Only a bounded self-authored JSON report is uploaded. It contains fixed flags,
stage/counter values, package/tree hashes and no server text, account identity,
URLs from protocol messages, stdout/stderr contents or local absolute paths.
`native_account_verified` and `real_model_ready` remain false even on success.

## Safety limits and use

This diagnostic **does execute official native software** and writes new files.
Initialization/managed reads can initialize services, locks, watchers or native
resources. No guarantee of zero network activity or zero other native effects
is made. Empty HOME/config roots and a Windows Job are not OS, credential,
browser-cookie, network or multi-tenant security boundaries. The hosted runner
has no user desktop account; running this on an existing user PC is not covered.

CN Product evidence does not establish WorkBuddy AI / international account
compatibility. Real authentication would require separate Product/version
validation, protected dedicated storage and browser/session review, account and
service authorization, and user-controlled official authentication. No tool
permission bypass, credential extraction, private bootstrap or remote model
proxy is an acceptable fallback.

Offline fixtures run with:

```text
PYTHONPATH=src python -m unittest discover -s tests -p 'test_public_harness_catalog*.py' -v
```

The dedicated workflow must pass the complete offline suite, including the real
Windows owned **self-authored** peer, before the single native experiment. Its
artifact is diagnostic evidence only, not a downloadable production release.
At source preparation time native execution is **NOT_RUN**; record the actual
run/result separately rather than interpreting fixture success as native proof.
