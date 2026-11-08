# Fresh empty-account native managed catalog

This separately authorized diagnostic extends the successful Product-only
experiment to **managed Account discovery**, not login. It uses the same genuine
full WorkBuddy Product projection and original pinned beta15 distribution.

Before importing native code it verifies six physically contained disposable
roots are empty: OS home, configuration, APPDATA, LOCALAPPDATA, temporary files
and working directory. Environment allowlisting and independent child checks
also prevent inherited custom-token/API-key/helper/spill settings from choosing
another authentication branch. No user PC, account store or credential is copied.
The original runtime performs its own empty-account discovery.

## Only allowed sequence

1. Verify exact entry/archive, empty roots and full official Product projection.
2. Create one native Harness and one managed-Account/managed-Product Client.
3. Register cancellation-only form/URL elicitation handling before connect. Any
   elicitation is sticky failure, not permission to continue.
4. Initialize, read Product and verify WorkBuddy / WorkBuddy / 5.9.3 identity.
5. Require exactly one advertised method with the pinned public fields:
   `type:agent`, `methodId:workbuddy-desktop`, `name:TencentCloud`,
   `description:null`. Provider identity is separately verified from the full
   pinned Product's `authentication.type:account-scoped-external-link`.
6. Read account, requiring `unauthenticated` with null mode/account. Read auth
   status, requiring `authenticated:false`.
7. Close Client and Harness, observe Harness settlement and owned process cleanup.

Request options use the pinned SDK's `cancellationSignal`; connection and Harness
startup use their own `signal` contracts. A request cancellation is not itself
response settlement, so the independent deadline and owned cleanup remain required.

The native Product Manager deliberately projects the public auth catalog to
description/methodId/name/type, dropping internal providerType/loginType. The
first hosted run (`e28faaf`, run 42437524509) verified managed initialization and
WorkBuddy Product identity, then correctly stopped at our mistaken providerType
assertion. Account/status were not called. That run closed all owned resources;
it did not prove the unreported catalog fields or native authentication. This
correction follows the pinned projection code, not a blanket relaxation.

There is no auth/login, auth/logout, account/Product provisioning, workspace,
session, tool or model request. Unexpected authenticated state is a failed test,
not a shortcut to usability. Native-account verification and real-model readiness
remain literal false. Catalog availability is not native authentication,
protected-account restoration, entitlement or completion evidence.

## Why an empty account is a useful test

Pinned-source tracing found a concrete empty branch: API-key and custom-token
stores are disabled when their indicators are absent; the ordinary file store
returns undefined before session decoding when its file is missing. Empty
settings may construct a protection codec, but have no protected fields to open.
The read-only account projection does not acquire the live login provider, and
unauthenticated initialization skips account commit/refresh. No private host
bootstrap, in-memory credential flag, fake host identity or protection downgrade
is needed or proposed. The diagnostic checks whether that traced branch actually
works in the hosted native runtime.

## Side effects and limits

Native settings/account reads can create disposable directories, lock files and
short-lived watchers/timers. Ordinary Harness startup/cleanup also initializes
other native services. These are not zero-side-effect operations. A fresh home
and OwnedWindowsJob do not sandbox filesystem/network access and do not prove
there was no implicit native network activity. No security/firewall setting is
changed. If protected or existing state appears, stop rather than decoding,
deleting, migrating or repairing it to make the test pass.

Physical and ASAR-virtual entry verification uses the already-reviewed helpers.
The full 6,166-file official payload, executable, ASAR, Product and entry remain
pinned. The parent uses suspended owned launch, closed stdin, discarded bounded
stdout/stderr and exact output validation after cleanup. Only a reconstructed
fixed boolean/integer summary is uploaded. Raw account data, native exceptions,
URLs and file contents are never artifacts. There is no automatic retry; workflow
rerun attempts are skipped. Existing Product and Client-mock paths are unchanged.

## Validation

```text
node tests/fakes/beta15_managed_catalog_test.mjs
node tests/fakes/workbuddy_managed_catalog_driver_test.mjs
PYTHONPATH=src python -m unittest discover -s tests -p test_official_workbuddy_managed_catalog.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

These are authored fixtures, not native results. The new workflow is published
only after independent review under the explicit empty-cloud-account scope.
A successful run would establish only genuine managed empty discovery/catalog
and unauthenticated readback. Real login and user-PC compatibility remain separate.
