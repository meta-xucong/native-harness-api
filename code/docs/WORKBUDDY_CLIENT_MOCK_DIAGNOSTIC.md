# Public Client / synthetic peer diagnostic

This experiment runs the actual pinned beta15 **Client** against an entirely
self-authored in-memory JSON-RPC object-stream peer. It does not create a native
Harness, Server, auth provider, native account, browser login, Product resolver,
session or model call. A managed **client profile** is negotiated only with the
fake peer. All account data remains fabricated. Native account verification and
real-model readiness are literal false, including on a successful run.

## Four finite cases

1. Advertise the synthetic WorkBuddy-shaped method, acknowledge `auth/login`,
   then return an unauthenticated `account/read`. A subsequent explicitly
   fabricated account read and status response verify client decoding, not login.
2. Issue one synthetic form elicitation. The client handler cancels without using
   its payload; the peer returns a fixed cancellation error and unauthenticated
   readback. This does not exercise a real provider or prove credential rollback.
3. Send one malformed account snapshot and require actual client rejection.
4. Leave a fake login pending, cancel it after the peer observes it, require the
   correlated cancellation wire message, then let the synthetic peer respond with
   a cancellation error. Send a late duplicate ACK and require an unauthenticated
   read. The peer's error, not the abort signal alone, settles this request.

The pinned SDK request option is `cancellationSignal`, while connection setup
uses `signal`. A request cancellation sends `$/cancel_request`; it does not
automatically reject or remove the pending response. The `timeout_rejected` flag
here means a deadline triggered cancellation **and the synthetic peer confirmed
it with an error**. It is not a claim of SDK-enforced timeout or native rollback.
An independent outer deadline and owned shutdown still bound an unresponsive peer.

Each case has a fresh client/peer. Request identifiers, methods, message count and
order are bounded. Unknown operations fail; there are no session/model or real
URL paths. Client close and both stream endpoints are observed separately. The
public transport's idempotent Effect-based close is also requested explicitly,
including after a client-close failure. A 30-second operation budget and shared
10-second cleanup budget are bounded again by the existing owned-process parent.

## Loading and containment

The new workflow/parent/driver are separate from the successful Product-only
probe, whose source and trigger remain unchanged. The original official CN
WorkBuddy 5.9.3 distribution, full 6,166-file payload tree, executable, ASAR and
three selected package/Client/Transport members are pinned. Public entries are
loaded through official Electron 37.10.3's normal ASAR resolution. Physical
checks use its builtin `original-fs`; no monkeypatch, private import, dependency
reconstruction, endpoint replacement, npm install or protection bootstrap occurs.
Effect's public export, already a Client dependency, is separately verified and
used only to run the transport's cleanup Effect.

Only a fresh GitHub-hosted Windows process is admitted, using the existing
suspended launch/OwnedWindowsJob supervisor. HOME/USERPROFILE, APPDATA,
LOCALAPPDATA, TEMP/TMP, configuration directories and cwd are fresh; the child
checks these again. stdin is closed. stdout/stderr are bounded, counted and
otherwise discarded. The summary is exclusively created and accepted only after
owned cleanup; it contains fixed boolean/integer keys, no account identity,
credential, URL, raw message or exception text. The artifact contains this
reconstructed summary only. No retry is built in; rerun attempts are skipped.

A fresh home and owned Job are **not** a filesystem/network sandbox. No firewall
or other security setting is changed. Client+Transport's inspected local import
closure avoids native component/provider edges; transitive third-party module
initialization remains a caveat. Do not describe this as completely side-effect
free or claim its synthetic results establish native login/restoration.

## Source validation and run gate

```text
node tests/fakes/beta15_client_mock_test.mjs
node tests/fakes/workbuddy_client_mock_driver_test.mjs
PYTHONPATH=src python -m unittest discover -s tests -p test_official_workbuddy_client_mock.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

These commands use only authored fake APIs, not the vendor runtime. The real
probe is a separately authorized hosted test after independent source review.
Its workflow watches only this probe's new source and tests. Reused helper
changes require a new review; they do not automatically rerun this experiment.
No claim of actual execution or pass is made by this document before evidence is
collected for the exact published commit.
