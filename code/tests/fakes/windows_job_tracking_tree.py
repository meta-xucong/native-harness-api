"""Self-authored lifecycle stress. No vendor, network, model or capture handshake."""
import os
from pathlib import Path
import subprocess
import sys
import time

mode, directory, *args = sys.argv[1:]
root = Path(directory)

def start(kind, *extra):
    return subprocess.Popen([sys.executable, __file__, kind, str(root), *map(str, extra)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if mode == 'leaf':
    # Exit immediately after ordinary work; no ready/ack delay for owner capture.
    (root / ('leaf-' + args[0])).write_text('done')
elif mode == 'burst':
    children = [start('leaf', i) for i in range(12)]
    if any(child.wait(timeout=20) != 0 for child in children):
        sys.exit(2)
    (root / 'burst.done').write_text('done')
elif mode == 'branch':
    children = [start('leaf', args[0] + '-' + str(i)) for i in range(3)]
    if any(child.wait(timeout=20) != 0 for child in children):
        sys.exit(2)
    (root / ('branch-' + args[0])).write_text('done')
elif mode == 'dynamic':
    branches = [start('branch', i) for i in range(4)]
    if any(branch.wait(timeout=25) != 0 for branch in branches):
        sys.exit(2)
    (root / 'dynamic.done').write_text('done')
elif mode in ('orphan', 'child', 'grandchild'):
    if mode != 'orphan':
        (root / (mode + '.pid')).write_text(str(os.getpid()))
    next_mode = 'child' if mode == 'orphan' else 'grandchild' if mode == 'child' else None
    if next_mode:
        start(next_mode)
        deadline = time.monotonic() + 20
        while not (root / (next_mode + '.ready')).exists():
            if time.monotonic() >= deadline:
                sys.exit(2)
            time.sleep(.01)
    (root / (mode + '.ready')).write_text('ready')
    # Only the long-running orphan case uses readiness. Short-lived tests above
    # have no artificial capture window and no signal back from the supervisor.
    if mode != 'orphan':
        time.sleep(60)
else:
    sys.exit(3)
