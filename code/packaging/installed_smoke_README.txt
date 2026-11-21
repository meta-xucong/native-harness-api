Installed WorkBuddy entry diagnostic kit (Windows x64)
NOT a finished model service, and NOT evidence of successful real model usage.
WorkBuddy itself is not included. No installer, account database, token, probe,
server or model client is bundled. Python 3.13.17 private runtime and its
LICENSE.txt are preserved byte-for-byte. No pip or network download is needed.

Only invoke after the diagnostic tool and this exact source revision are audited
and execution is explicitly authorized. Use an existing official installation
and an explicitly authorized EXISTING work-root. The NEW, nonexistent run-root
must be its direct child. Work-root and install-root must not contain each
other. Do not point either work-root or run-root at account or project data. A failed/used run-root is not permission to
retry with another root; obtain a new explicit execution authorization.

In PowerShell, from the extracted kit directory:
  & .\runtime\python.exe -I -B .\launcher.py --help
  & .\runtime\python.exe -I -B .\launcher.py --install-root 'C:\EXPLICIT\WorkBuddy' --work-root 'C:\AUTHORIZED-WORK' --run-root 'C:\AUTHORIZED-WORK\NEW-DIAGNOSTIC-DIR'

The first command prints offline usage. The second explicitly invokes the
installed tool's bounded help/initialize diagnostic. It is not a login or model
request. No automatic run, shell wrapper, elevated permission, execution-policy
bypass, account scanning or vendor download is provided by this kit.

The tool's audited permission and output limits govern execution. This kit does
not promise an OS sandbox, credential isolation, successful initialization,
model identity, complete cancellation, or a production-ready upstream channel.
Do not upload arbitrary generated files or native output. Follow only the
reviewed tool's bounded report contract.

RELEASE-MANIFEST.json records source revision and per-file SHA256.
SHA256SUMS covers every member except itself. These detect byte changes, not
publisher identity: verify the outer ZIP SHA256 against the trusted delivery.

Operator-only outer controller (not a production service): controller.ps1
Run only using an authorized PowerShell 7 invocation after source/kit review:
  & .\controller.ps1 -Kit 'C:\EXACT\KIT' -Install 'C:\EXPLICIT\WorkBuddy' -Workspace 'C:\AUTHORIZED-WORK' -ControlName 'NativeHarnessControl-AUTHORIZED-TAG' -RunName 'NativeHarnessRun-AUTHORIZED-TAG'
Do not execute this after already running the direct launcher for the same
request. There is one authorized attempt, not two alternative attempts.
The controller invokes only this kit's runtime/python.exe via ArgumentList,
retains its Process/SafeHandle, and waits at most 120 seconds. On uncertainty it
may kill only that retained Python controller, never look up/re-attach a PID or
kill a process tree. No valid native cleanup report means native cleanup UNKNOWN,
including after a successful outer-process kill. Each output stream has a 16 KiB
byte cap; raw streams/errors are never returned. A fresh control directory gets
a durable launch.intent and bounded allowlisted summary. No directories are
removed and no retry/new-root workaround is allowed. Do not alter ExecutionPolicy
or evaluate a script blocked by policy. The Windows CI controller tests use a
synthetic launcher, not WorkBuddy; their success is not native model acceptance.
