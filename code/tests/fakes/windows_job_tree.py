"""Synthetic Windows process-tree fixture. No vendor code, network or models."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

mode, root, *extra = sys.argv[1:]
root = Path(root)
(root/(mode+'.pid')).write_text(str(os.getpid()))
if mode == 'controller_preverify':
    from native_harness_api.windows_job import OwnedWindowsJob
    class CrashBeforeVerify(OwnedWindowsJob):
        def _verify_membership(self):
            (root/'created.pid').write_text(str(self.pid))
            (root/'controller.ready').write_text('ready')
            while not (root/'crash.go').exists():time.sleep(.01)
            os._exit(71)
    CrashBeforeVerify.spawn([sys.executable,__file__,'marker',str(root)],root,dict(os.environ))
elif mode == 'controller':
    from native_harness_api.windows_job import OwnedWindowsJob
    owner = OwnedWindowsJob.spawn([sys.executable, __file__, 'parent', str(root)],root,dict(os.environ))
    deadline=time.monotonic()+10
    while not (root/'parent.ready').exists():
        if time.monotonic()>deadline:sys.exit(2)
        time.sleep(.01)
    (root/'controller.ready').write_text('ready')
    while not (root/'crash.go').exists():time.sleep(.01)
    os._exit(71)  # No Python finally/destructor: kernel closes job HANDLE.
elif mode == 'sentinel':
    import ctypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.SetEvent.argtypes=[ctypes.c_void_p];api.SetEvent.restype=ctypes.c_int
    (root/'sentinel.json').write_text(json.dumps({'set_event':bool(api.SetEvent(int(extra[0])))}))
elif mode == 'args':
    (root/'argv.json').write_text(json.dumps(extra, ensure_ascii=False), encoding='utf-8')
    sys.stdout.buffer.write(sys.stdin.buffer.readline())
    sys.stdout.buffer.flush()
    sys.stderr.write('synthetic stderr\n');sys.stderr.flush()
elif mode == 'marker':
    (root/'executed').write_text('executed')
elif mode == 'breakaway':
    command = [sys.executable, __file__, 'marker', str(root)]
    environment = dict(os.environ)
    # Same executable/argv/cwd/env must run normally first, so an unrelated
    # launch failure cannot masquerade as successful containment enforcement.
    ordinary = subprocess.Popen(command, env=environment)
    ordinary_code = ordinary.wait(timeout=5)
    ordinary_marker = (root/'executed').exists()
    if ordinary_marker:
        (root/'executed').unlink()
    result = {'ordinary_exit_code':ordinary_code, 'ordinary_marker':ordinary_marker}
    try:
        child = subprocess.Popen(command, env=environment, creationflags=0x01000000)
        result.update(escaped=True, escape_exit_code=child.wait(timeout=5), winerror=None)
    except OSError as exc:
        result.update(escaped=False, winerror=exc.winerror)
    (root/'breakaway.json').write_text(json.dumps(result))
elif mode in ('parent', 'orphan_parent', 'child', 'grandchild'):
    next_mode = 'child' if mode in ('parent','orphan_parent') else 'grandchild' if mode == 'child' else None
    if next_mode:
        subprocess.Popen([sys.executable, __file__, next_mode, str(root)])
        deadline=time.monotonic()+10
        while not (root/(next_mode+'.ready')).exists():
            if time.monotonic()>deadline:sys.exit(2)
            time.sleep(.01)
    (root/(mode+'.ready')).write_text('ready')
    if mode != 'orphan_parent':
        while True:time.sleep(.1)
