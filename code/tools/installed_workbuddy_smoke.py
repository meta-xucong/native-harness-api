"""One authorized installed WorkBuddy help + initialize trial. No account inspection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import queue
import stat
import threading
import time

from native_harness_api.windows_job import OwnedWindowsJob

PINNED = {
    'WorkBuddy.exe': '5ea5d8ba6f6a435c27c239115647886a9646f76ff5a721e82f84f0ff16ac5e15',
    'resources/app.asar': '026a1d0805a2366d06390cef52e157a7277543cdd27dd308e6b1acb47ffffdc1',
    'resources/app.asar.unpacked/cli/bin/codebuddy': '8df0f1c7bc6535e5df0b626450228241d874867023adb6c9c8f7b1a882590bf4',
    'resources/app.asar.unpacked/cli/dist/codebuddy-lite-wb.mjs': '7d51bb147e224a2772ffca521958d0b05740af1195469564b60a88f05f8f93cf',
    'resources/app.asar.unpacked/cli/product.json': '09e61c49812371c0497f6c954e448cbbcb86c08461d6883cf344e7c0472838a2',
}
ENTRY = 'resources/app.asar.unpacked/cli/bin/codebuddy'
PLATFORM = os.name
LIMIT = 1024 * 1024
INITIALIZE = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
    'protocolVersion': 1, 'clientCapabilities': {
        'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False},
    'clientInfo': {'name': 'native-harness-installed-smoke', 'version': '1'}}}


class TrialError(Exception):
    pass


FAILURE_CODES = frozenset({'PATH_REJECTED', 'INSTALL_REJECTED', 'ROOT_REJECTED',
    'PROTOCOL_REJECTED', 'IO_FAILED', 'DEADLINE', 'NONZERO_EXIT', 'EMPTY_HELP',
    'HELP_NOT_OBSERVED', 'CLEANUP_UNCERTAIN', 'INTERNAL_FAILURE', 'UNSUPPORTED'})


def failure_code(exc):
    # Only our exact exception type and exact fixed string, never vendor/OS text/code.
    if type(exc) is TrialError and len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in FAILURE_CODES:
        return exc.args[0]
    return 'INTERNAL_FAILURE'


def windows_path(value):
    """Reject Windows aliases before filesystem calls; no expansion or resolution."""
    raw = str(value)
    path = PureWindowsPath(raw)
    if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':' or
            not path.drive[0].isascii() or not path.drive[0].isalpha() or
            raw.startswith(('\\\\', '//')) or '\x00' in raw):
        raise TrialError('PATH_REJECTED')
    for part in path.parts[1:]:
        if (':' in part or part in ('.', '..') or part.endswith((' ', '.')) or
                any(c in part for c in '<>"|?*') or any(ord(c) < 32 for c in part) or
                part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',
                    *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}):
            raise TrialError('PATH_REJECTED')
    return path


def checked_ancestors(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise TrialError('PATH_REJECTED')


def validate_paths(install, work, root):
    i, w, r = map(windows_path, (install, work, root))
    if r.parent != w or i == w or i in w.parents or w in i.parents:
        raise TrialError('PATH_REJECTED')
    checked_ancestors(install)
    checked_ancestors(work)
    if not install.is_dir() or not work.is_dir() or root.exists():
        raise TrialError('PATH_REJECTED')
    # Windows realpath uses final paths, including expansion of MS-DOS 8.3 names.
    # Retain lexical rejection above, but do not trust lexical disjointness alone.
    actual_install = windows_path(install.resolve(strict=True))
    actual_work = windows_path(work.resolve(strict=True))
    actual_parent = windows_path(root.parent.resolve(strict=True))
    if (actual_parent != actual_work or actual_install == actual_work or
            actual_install in actual_work.parents or actual_work in actual_install.parents):
        raise TrialError('PATH_REJECTED')


def validate_install(root):
    if not root.is_absolute():
        raise TrialError('INSTALL_REJECTED')
    for relative, digest in PINNED.items():
        path = root / relative
        checked_ancestors(path)
        if not path.is_file():
            raise TrialError('INSTALL_REJECTED')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise TrialError('INSTALL_REJECTED')
    # Hash checking is not protection against concurrent installation replacement.


def prepare(root):
    if not root.is_absolute():
        raise TrialError('ROOT_REJECTED')
    checked_ancestors(root.parent)
    # Caller supplies a fixed fresh authorization-specific root, never a new name on retry.
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    with (root / 'intent.json').open('xb') as stream:
        stream.write(b'{"authorization_consumed":true,"retry":false}\n')
        stream.flush()
        os.fsync(stream.fileno())
    system = os.environ.get('SystemRoot', r'C:\Windows')  # Only named OS variable, no env scan.
    env = {'SystemRoot': system, 'WINDIR': system,
           'PATH': str(Path(system) / 'System32'), 'ELECTRON_RUN_AS_NODE': '1',
           'CODEBUDDY_FORCE_LITE_WB_BUNDLE': '1', 'CODEBUDDY_DISABLE_IDE': '1'}
    for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP',
                 'CODEBUDDY_CONFIG_DIR', 'WORKBUDDY_CONFIG_DIR'):
        directory = root / name
        directory.mkdir()
        env[name] = str(directory)
    return env


def parse_response(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def invalid(_):
        raise ValueError()
    try:
        frame = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
        if (not isinstance(frame, dict) or set(frame) != {'jsonrpc', 'id', 'result'} or
                frame['jsonrpc'] != '2.0' or type(frame['id']) is not int or frame['id'] != 1):
            raise ValueError()
        result = frame['result']
        if (not isinstance(result, dict) or type(result.get('protocolVersion')) is not int or
                result['protocolVersion'] != 1 or not isinstance(result.get('agentCapabilities'), dict)):
            raise ValueError()
    except (ValueError, UnicodeError, TypeError):
        raise TrialError('PROTOCOL_REJECTED') from None


# Keep failed owners reachable for operator-directed cleanup, never for another spawn.
RETAINED = []


def phase(command, cwd, env, initialize, *, timeout=45, cleanup_timeout=5):
    result = {'status': 'FAILED', 'failure_code': 'INTERNAL_FAILURE', 'stdout_bytes': 0, 'stderr_bytes': 0,
              'natural_exit': False, 'natural_exit_zero': False,
              'host_exited': None, 'owned_job_empty': None, 'handles_closed': False,
              'io_threads_stopped': False, 'cleanup_complete': False}
    owner = None
    threads = []
    startup_uncertain = False
    events = queue.Queue(maxsize=520)
    fault = threading.Event()
    totals = {'stdout': 0, 'stderr': 0}
    stdout = bytearray()
    stderr = bytearray()
    writer_done = threading.Event()
    deadline = time.monotonic() + timeout

    def register(target, args=()):
        nonlocal startup_uncertain
        thread = threading.Thread(target=target, args=args, daemon=True)
        threads.append(thread)  # Register BEFORE start, including interrupted start.
        startup_uncertain = True
        thread.start()
        startup_uncertain = False

    def reader(label, stream):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    events.put_nowait((label, None))
                    return
                totals[label] += len(chunk)
                if totals[label] > LIMIT:
                    fault.set()
                    return
                if label == 'stdout':
                    stdout.extend(chunk)
                else:
                    stderr.extend(chunk)
                events.put_nowait((label, chunk))
        except BaseException:
            fault.set()

    def writer():
        try:
            if initialize:
                raw = (json.dumps(INITIALIZE, separators=(',', ':')) + '\n').encode()
                if owner.stdin.write(raw) != len(raw):
                    raise ValueError()
                owner.stdin.flush()
            else:
                owner.stdin.close()
            writer_done.set()
        except BaseException:
            fault.set()

    try:
        owner = OwnedWindowsJob.spawn(command, cwd, env)
        register(reader, ('stdout', owner.stdout))
        register(reader, ('stderr', owner.stderr))
        register(writer)
        pending = bytearray()
        frames = 0
        eof = set()
        while True:
            if fault.is_set():
                raise TrialError('IO_FAILED')
            if time.monotonic() >= deadline:
                if initialize and frames == 1 and not pending and not eof and writer_done.is_set():
                    break  # Successful protocol observation window; cleanup still mandatory.
                raise TrialError('DEADLINE')
            try:
                label, chunk = events.get(timeout=min(.02, max(0, deadline-time.monotonic())))
                if chunk is None:
                    eof.add(label)
                elif initialize and label == 'stdout':
                    pending.extend(chunk)
                    while b'\n' in pending:
                        raw, _, tail = pending.partition(b'\n')
                        pending = bytearray(tail)
                        parse_response(raw)
                        frames += 1
                        if frames != 1:
                            raise TrialError('PROTOCOL_REJECTED')
            except queue.Empty:
                pass
            if owner.host_exited():
                code = owner.exit_code()
                result['natural_exit'] = True
                result['natural_exit_zero'] = code == 0
                if code != 0:
                    raise TrialError('NONZERO_EXIT')
                if eof == {'stdout', 'stderr'} and events.empty():
                    if initialize and (frames != 1 or pending):
                        raise TrialError('PROTOCOL_REJECTED')
                    if not initialize and totals['stdout'] + totals['stderr'] == 0:
                        raise TrialError('EMPTY_HELP')
                    break
        # EOF without host exit, residual frames, and a blocked writer never count as PASS.
        if pending or not events.empty() or fault.is_set():
            raise TrialError('PROTOCOL_REJECTED')
        result['status'] = 'OBSERVED'
        result['failure_code'] = None
    except BaseException as exc:
        if owner is None:
            owner = getattr(exc, 'owner', None)
        result['status'] = 'FAILED'
        result['failure_code'] = failure_code(exc)
    finally:
        end = time.monotonic() + cleanup_timeout
        if owner is not None:
            try:
                if owner.host_exited():
                    result['natural_exit'] = True
                    result['natural_exit_zero'] = owner.exit_code() == 0
                    if not result['natural_exit_zero']:
                        result['status'] = 'FAILED'
                        result['failure_code'] = 'NONZERO_EXIT'
                stopped = owner.terminate(max(0, end-time.monotonic()))
                result['owned_job_empty'] = stopped.get('owned_job_empty') is True
                result['host_exited'] = owner.host_exited()
            except BaseException:
                pass
        for thread in threads:
            try:
                if thread.ident is not None:
                    thread.join(max(0, end-time.monotonic()))
            except BaseException:
                pass
        result['io_threads_stopped'] = not startup_uncertain and all(not t.is_alive() for t in threads)
        # Validate ALL captured stdout, including bytes drained during cleanup.
        if initialize and result['io_threads_stopped']:
            try:
                if not stdout.endswith(b'\n') or stdout.count(b'\n') != 1:
                    raise TrialError('PROTOCOL_REJECTED')
                parse_response(bytes(stdout[:-1]))
            except BaseException:
                result['status'] = 'FAILED'
                if result['failure_code'] is None:
                    result['failure_code'] = 'PROTOCOL_REJECTED'
        if not initialize and (b'--help' not in stdout + stderr or b'--acp' not in stdout + stderr):
            result['status'] = 'FAILED'
            if result['failure_code'] is None:
                result['failure_code'] = 'HELP_NOT_OBSERVED'
        if not writer_done.is_set():
            result['status'] = 'FAILED'
            if result['failure_code'] is None:
                result['failure_code'] = 'IO_FAILED'
        # Do not block closing a buffered stream while its IO thread is still active.
        if owner is not None and result['io_threads_stopped']:
            try:
                closed = owner.close(max(0, end-time.monotonic()))
                result['owned_job_empty'] = closed.get('owned_job_empty') is True
                result['handles_closed'] = True
            except BaseException:
                pass
        result['cleanup_complete'] = (result['host_exited'] is True and
            result['owned_job_empty'] is True and result['handles_closed'] and result['io_threads_stopped'])
        if owner is not None and not result['cleanup_complete']:
            RETAINED.append(owner)
        for label in totals:
            result[label + '_bytes'] = min(totals[label], LIMIT + 4096)
        if not result['cleanup_complete']:
            result['status'] = 'FAILED'
            result['failure_code'] = 'CLEANUP_UNCERTAIN'
        elif fault.is_set():
            result['status'] = 'FAILED'
            if result['failure_code'] is None:
                result['failure_code'] = 'IO_FAILED'
    return result


def stage_intent(root, stage):
    with (root / (stage + '.intent')).open('xb') as stream:
        stream.write(b'no-retry\n')
        stream.flush()
        os.fsync(stream.fileno())


def run(install, root, work):
    if PLATFORM != 'nt':
        return {'status': 'UNSUPPORTED', 'failure_code': 'UNSUPPORTED'}
    try:
        # Reserve the authorization before validation; failed preflight cannot be retried here.
        validate_paths(install, work, root)
        env = prepare(root)
        validate_install(install)
        command = [str(install / 'WorkBuddy.exe'), str(install / ENTRY)]
        stage_intent(root, 'help')
        help_result = phase(command + ['--help'], root, env, False)
        report = {'status': 'FAILED', 'help': help_result}
        if help_result['status'] != 'OBSERVED':
            return report
        stage_intent(root, 'initialize')
        initialize = phase(command + ['--acp'], root, env, True)
        report['initialize'] = initialize
        if initialize['status'] == 'OBSERVED':
            report['status'] = 'OBSERVED'
        return report
    except BaseException as exc:
        return {'status': 'PREFLIGHT_REJECTED', 'failure_code': failure_code(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-root', required=True, type=Path)
    parser.add_argument('--work-root', required=True, type=Path)
    parser.add_argument('--run-root', required=True, type=Path)
    args = parser.parse_args(argv)
    report = run(args.install_root, args.run_root, args.work_root)
    print(json.dumps(report, separators=(',', ':')))
    return 0 if report['status'] == 'OBSERVED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
