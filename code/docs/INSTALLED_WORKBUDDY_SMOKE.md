# Authorized installed Windows help / initialize trial

Independent narrow diagnostic, not a model adapter, login test, or production channel.
Implementation: `tools/installed_workbuddy_smoke.py`. No existing product gate changes.
Requires a separately approved operator run and exact fixed inventory; preparing this
code is not authorization to execute it. Current tests use only offline fake owners.

## Execution plan

1. Operator uses a trusted, separately verified Python launcher and one fixed fresh
   authorization-specific `--run-root` plus explicit absolute `--install-root` and
   existing `--work-root`. The run root must be a direct child of that work root.
   Drive-absolute Windows paths only: reject UNC, device, ADS, relative, reserved
   names and reparse/symlink ancestors all the way to the volume root. Installation
   and work roots must be disjoint, case-insensitively (neither may contain the other).
   Operator must prevalidate these same conditions and prevent concurrent ancestor
   replacement; pathname checks are not a race-proof sandbox against hostile mutation.
2. Exclusive mkdir consumes the run. Flush/fsync `intent.json` before validation;
   existing directory means refuse, even after failed preflight or controller crash.
   Never choose another directory to retry the same authorization. Parent tool timeout
   must NOT restart the launcher. Stage-specific exclusive `help.intent` and
   `initialize.intent` are persisted before each launch.
3. Hash only the five exact official installation files against embedded approved
   inventory (EXE 5.8.3, shared archive 5.8.4, CLI 2.161). Reject reparse/symlink
   components. Do not enumerate installation, account, environment, running processes,
   existing pipes or logs. Hash validation is not an atomic installation snapshot;
   do not update/replace installation concurrently with this trial.
4. Launch official EXE + official absolute bin entry + `--help` directly through
   audited `OwnedWindowsJob`. No shell, preload, wrapper, second EXE or fallback.
   Help must exit naturally zero and contain both fixed `--help` and `--acp`
   markers internally in bounded stdout/stderr, with complete readers and cleanup.
   Nonempty login/error text alone fails; raw help text is never returned.
5. Only then launch the same EXE/entry + `--acp`. Send exactly one newline-delimited
   initialize request. No responses to server RPCs, authentication, session creation,
   model selection or prompt. Any extra frame, notification, malformed JSON,
   duplicate field, residual bytes, missing response or nonzero natural exit fails.
   The complete stdout lifecycle, including cleanup drain, is validated.
6. Each phase has a 45-second observation deadline. A successful initialize response
   is observed to that deadline (or clean natural EOF/zero exit), never followed by
   another probe. Output is bounded to 1 MiB per stream; stdin writer and both readers
   run registered daemon threads so blocked IO cannot stall the observation loop.
7. Each phase cleanup has a separate five-second shared deadline across terminate,
   thread joins and close. Only owned Job members are terminated. No GUI attach,
   PID kill, existing-process termination or recovered-PID control. If thread start
   is interrupted its registration is uncertain, cleanup remains unknown. Do not
   close buffered streams while IO threads may be active. Failed owners remain
   referenced for cleanup retry, never another spawn.

OS process creation, filesystem fsync, synchronous Win32 calls and thread startup
are not forcibly interruptible Python operations. Deadlines bound cooperative waits,
not arbitrary kernel stalls. Outer watchdog termination closes the controller's Job
handle and invokes kill-on-close, but absence of a report is not positive cleanup
proof. Retained owners do not survive interpreter death, and PID recovery is forbidden.

## Privacy / filesystem boundary

Child env is a fixed allowlist (only named SystemRoot is read from parent).
HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP/config dirs point inside the new root.
These are disruption reduction, NOT a sandbox. The official runtime can use native
system interfaces and existing account state as expressly authorized by the user.
The tool never reads/exports credentials or intentionally selects account flows.
All tool-created state stays within the fresh root; vendor OS-level side effects
cannot be proven confined by environment redirection alone.

No raw stdout/stderr, auth methods, names, paths, logs, exception text or model data
are returned. Output contains fixed statuses, byte counts, natural-exit booleans and
separate host/Job/handle/IO cleanup facts. `OBSERVED` only means this narrow protocol
observation passed; it does not prove a real account, actual selected/reported model,
external tool stop, or `stopped_confirmed`. No automatic directory deletion occurs,
including success. Unknown cleanup retains evidence. The tool never attaches to or terminates an existing GUI; vendor OS/account side
effects are not proven absent.

## Offline checks

`PYTHONPATH=src python -m unittest discover -s tests -p test_installed_workbuddy_smoke.py -v`

Tests inject fake owners for lifecycle/parse/order/error contracts, not native Win32
execution evidence. Real installed vendor execution remains NOT_RUN until independent
review and separately authorized operator execution. No account, inference or vendor
process was used to develop this component.

Failure summaries include a fixed `failure_code` enum (or null on observed success).
Only exact locally generated TrialError values in the allowlist can supply a code;
OS/vendor exception text and arbitrary `.code` fields are never serialized. Cleanup
uncertainty takes precedence while byte counts and natural-exit facts remain available.

The dedicated suite now includes three Windows-only tests running self-written Python
fixtures through the real OwnedWindowsJob + new capture/writer lifecycle: help markers,
one initialize response/natural zero exit, and rejection of an extra protocol frame.
These do not run WorkBuddy. Linux validation: 22 tests, 19 passed and 3 explicit Windows
skips. Windows fixture execution is NOT_RUN here; existing Job test results do not
stand in for these new combination tests.

### Physical-path alias check

After lexical checks and reparse rejection of existing ancestors, the tool resolves
existing installation root, work root and run-parent with `Path.resolve(strict=True)`.
It rejects equality/ancestor overlap of the canonical installation/work paths and
requires canonical run-parent to equal canonical work root. Python's Windows
`os.path.realpath` expands MS-DOS 8.3 names; `Path.resolve` uses platform realpath.
References: https://docs.python.org/3.12/library/os.path.html#os.path.realpath and
https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.resolve .
This closes lexical alias comparisons, not arbitrary filesystem/drive remapping
races: no retained directory-handle lock or proof against concurrent replacement is
claimed. Do not alter installation, ancestors, drive mappings or work root during
execution. No SUBST command or permission change is performed.

Updated dedicated Linux result: 24 tests, 20 passed, 4 Windows-only skips. Added an
injected canonical-alias test and a real Windows GetShortPathNameW overlap test;
the latter explicitly skips if the filesystem supplies no 8.3 alias. All four new
Windows combination/path tests remain NOT_RUN in this Linux development environment.
