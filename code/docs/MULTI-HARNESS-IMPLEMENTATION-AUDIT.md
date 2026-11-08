# v0.3 source audit: ACCEPT for draft push and CI

## Decision
The frozen Native implementation, Bridge/Sub2 mode changes and integration fixtures are accepted for source publication to the existing draft development branches and CI. No unresolved source-review blocker was found. This does not approve production merge/deployment or claim authenticated vendor execution works.

## Resolved findings
- Codex thread/start now checks returned cwd at the protocol boundary.
- Completed agent items cannot receive late delta/started/completed mutations. Authoritative terminal snapshots can still correct text, and partial snapshots preserve absent items.
- Codex owned-session diagnostic outcome incorporates protocol faults; actual turn completion and owned process cleanup remain distinct.
- Codex login now reuses the existing WindowsTTY fresh prompt handling. Previously buffered yes cannot confirm a newly displayed account. A deterministic regression fixture passes.

## Minimality
Static two-adapter selection and canonical frozen recipes are used instead of a dynamic framework. Binding wire, durable evidence database, Node journal/global slot, ownership cleanup and event schema are reused. No new table, retry/fallback, registry service, tool approval service or watchdog was introduced. New checks enforce actual boundary identities/completion; they do not duplicate arbitrary internal defenses. HTTP worker drainage changes only the test fixture.

## Independent tests
- Codex tests: 50 run, 4 Windows-only skipped, 46 passed.
- Generic config/deployment/slot tests: 16 passed.
- Real Bridge/Facade socket integration: 2 passed; includes preterminal A snapshot, two adapter projections, removed-route replay, unknown shared-slot occupancy. Executors are self-authored fixtures, not vendor runtimes.
- First full Native pass: 914 run, 58 skipped, remaining passed.
- Full Native final pass: 928 run, 58 skipped, remaining passed in 33.080 s; /tmp/devdoc-native-final.log.
- Cross-mode Bridge/Go targeted tests are documented in CROSS-MODES-AUDIT.md. Three-repository chain source reviewed: actual HTTP handler/middleware transport, fixture auth and route storage; no PostgreSQL or real model proof.

## Release gates still outstanding
1. Windows CI, especially real Windows Job tests with self-authored child processes.
2. Build and verify both complete selection packages against their exact source commit and pinned runtime tree.
3. Actual official executable/device-code/account/model acceptance remains NOT_RUN in this audit and must not be described as passed. Running official software does not itself authorize commercial redistribution of its service.

Packaging source and workflow are accepted for the data-only build step; optional login/model commands are not run during packaging. README limitations match implemented scope: WorkBuddy and Codex adapters only, commentary as running status rather than verbatim commentary body, no Doubao/Qwen adapter claim.
