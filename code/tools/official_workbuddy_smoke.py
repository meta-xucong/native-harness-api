"""No-account official Windows entry smoke. Never authenticates or sends prompts.

Only --help and one ACP initialize are allowed. This does not validate login,
models, tools, billing, WorkBuddy service terms, or a production model channel.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import subprocess
import sys
import struct
import stat
import zlib
import tempfile
import threading
import time
import urllib.request

VERSION = '5.9.3.42398134'
URL = 'https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.9.3.42398134-d4692421.exe'
INSTALLER_SIZE = 450174440
INSTALLER_SHA = 'b5edb52bd6c5b6c7296cf2fff697b019c86d11992a8a814c3010d611896852f9'
PAYLOAD_OFFSET = 276333
PAYLOAD_SIZE = 449257893
PAYLOAD_SHA = '19d3e4ad5108a080ef755c4475eecb84d97e46cfe77334d5d5e645fb1408e7e9'
ENTRY = 'resources/app.asar.unpacked/cli/bin/codebuddy'
ENTRY_SHA = '8df0f1c7bc6535e5df0b626450228241d874867023adb6c9c8f7b1a882590bf4'
MAX_OUTPUT = 1024 * 1024
KEEPALIVE_MS = 45000
KEEPALIVE_BYTES = b"'use strict';\nsetTimeout(() => {}, 45000);\n"
KEEPALIVE_SHA = '10d319ccff3336b745694709b4c65fd118c53139888869b5a9602045e45e2846'
INITIALIZE = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
    'protocolVersion': 1, 'clientCapabilities': {
        'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False},
    'clientInfo': {'name': 'native-harness-no-account-smoke', 'version': '1'}}}


class SmokeError(Exception):
    pass


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_member(name):
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and '..' not in path.parts and ':' not in name


def archive_inventory(raw):
    # 7z -slt header describes the input absolute filename; only members follow separator.
    if len(raw) > 8 * 1024 * 1024 or '\n----------\n' not in raw.replace('\r\n', '\n'):
        raise SmokeError('ARCHIVE_LIST_INVALID')
    body = raw.replace('\r\n', '\n').split('\n----------\n', 1)[1]
    total = 0
    names = set()
    for block in body.strip().split('\n\n'):
        fields = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
        name = fields.get('Path', '')
        if not safe_member(name) or str(PurePosixPath(name.replace('\\', '/'))).casefold() in names or any(k in fields for k in ('Symbolic Link', 'Hard Link')):
            raise SmokeError('ARCHIVE_MEMBER_UNSAFE')
        names.add(str(PurePosixPath(name.replace('\\', '/'))).casefold())
        try:
            size = int(fields.get('Size', '0'))
        except ValueError:
            raise SmokeError('ARCHIVE_SIZE_INVALID') from None
        if size < 0 or size > 1024**3:
            raise SmokeError('ARCHIVE_MEMBER_LIMIT')
        total += size
        if total > 6 * 1024**3 or len(names) > 20000:
            raise SmokeError('ARCHIVE_TOTAL_LIMIT')
    return len(names), total


def extract_payload(installer, output, *, offset=PAYLOAD_OFFSET, size=PAYLOAD_SIZE, expected_sha=PAYLOAD_SHA):
    """Extract exact verified embedded 7-Zip data, never interpret NSIS."""
    if offset < 0 or size < 32 or offset + size > installer.stat().st_size:
        raise SmokeError('PAYLOAD_BOUNDARY')
    with installer.open('rb') as src:
        src.seek(offset)
        header = src.read(32)
        if header[:8] != b'7z\xbc\xaf\x27\x1c\x00\x04':
            raise SmokeError('PAYLOAD_SIGNATURE_VERSION')
        start_crc = struct.unpack('<I', header[8:12])[0]
        next_offset, next_size, next_crc = struct.unpack('<QQI', header[12:32])
        if zlib.crc32(header[12:32]) != start_crc:
            raise SmokeError('PAYLOAD_START_CRC')
        if next_size > 1024 * 1024 or 32 + next_offset + next_size != size:
            raise SmokeError('PAYLOAD_HEADER_BOUNDARY')
        src.seek(offset + 32 + next_offset)
        next_header = src.read(next_size)
        if len(next_header) != next_size or zlib.crc32(next_header) != next_crc:
            raise SmokeError('PAYLOAD_NEXT_CRC')
        src.seek(offset)
        remaining = size
        hasher = hashlib.sha256()
        with output.open('xb') as dest:
            while remaining:
                chunk = src.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise SmokeError('PAYLOAD_TRUNCATED')
                hasher.update(chunk)
                dest.write(chunk)
                remaining -= len(chunk)
        if hasher.hexdigest() != expected_sha:
            raise SmokeError('PAYLOAD_SHA_MISMATCH')


def parse_frame(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError):
        raise SmokeError('NON_PROTOCOL_STDOUT') from None


def isolated_env(root):
    # No parent PATH, proxy, GitHub, account or vendor configuration variables.
    # Config redirection is NOT a sandbox: KnownFolder APIs may ignore env.
    # The supported boundary is a disposable runner with no user login/account.
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    env = {'SystemRoot': str(system), 'WINDIR': str(system), 'PATH': str(system / 'System32'),
           'ELECTRON_RUN_AS_NODE': '1', 'CODEBUDDY_FORCE_LITE_WB_BUNDLE': '1',
           'CODEBUDDY_DISABLE_IDE': '1'}
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP',
                'CODEBUDDY_CONFIG_DIR', 'WORKBUDDY_CONFIG_DIR'):
        path = root / key
        path.mkdir(parents=True)
        env[key] = str(path)
    return env


def validate_initialize(frame):
    if not isinstance(frame, dict) or 'method' in frame:
        raise SmokeError('UNSOLICITED_RPC_NO_RESPONSE_SENT')
    if frame.get('jsonrpc') != '2.0' or type(frame.get('id')) is not int or frame['id'] != 1:
        raise SmokeError('INVALID_INITIALIZE_RESPONSE')
    if 'error' in frame:
        raise SmokeError('INITIALIZE_REJECTED_NO_AUTH_ATTEMPT')
    result = frame.get('result')
    if not isinstance(result, dict) or type(result.get('protocolVersion')) is not int or result['protocolVersion'] != 1:
        raise SmokeError('UNSUPPORTED_PROTOCOL')
    caps = result.get('agentCapabilities')
    if not isinstance(caps, dict):
        raise SmokeError('INVALID_CAPABILITIES')
    # Do not emit raw names, IDs, auth details, paths or arbitrary server strings.
    return {'protocol_version': 1, 'load_session_advertised': caps.get('loadSession') is True,
            'auth_methods_advertised': bool(result.get('authMethods'))}


def capture_output(owner):
    events = queue.Queue(maxsize=260)
    overflow = threading.Event()
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    def reader(label, stream):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    events.put_nowait((label, None)); return
                if len(buffers[label]) + len(chunk) > MAX_OUTPUT:
                    overflow.set(); return
                buffers[label].extend(chunk)
                events.put_nowait((label, chunk))
        except (OSError, ValueError, queue.Full):
            overflow.set()
    threads = [threading.Thread(target=reader, args=(k, getattr(owner, k)), daemon=True) for k in buffers]
    for thread in threads:
        thread.start()
    return events, overflow, buffers, threads


def observe_eof_exit(owner, deadline, diagnostics):
    """Observation only; never kills/restarts or extends the existing deadline."""
    end = min(deadline, time.monotonic() + .5)
    diagnostics['eof_observation'] = 'no_natural_exit_observed'
    samples = []
    while time.monotonic() < end:
        try:
            code, count = owner.exit_code(), owner.active_processes()
            if type(count) is not int or not 0 <= count <= 65535:
                raise ValueError()
            if len(samples) < 10:
                samples.append(count)
            if code is not None:
                if type(code) is not int or not 0 <= code <= 0xffffffff:
                    raise ValueError()
                diagnostics['eof_observation'] = 'natural_exit_observed'
                diagnostics['natural_exit_code'] = code
                break
        except Exception:
            diagnostics['eof_observation'] = 'query_unavailable'
            break
        time.sleep(min(.05, max(0, end - time.monotonic())))
    diagnostics['eof_job_counts'] = samples


LOG_MARKERS = {
    'event_loop_drain': b'event_loop_drain',
    'dispatch_unmatched': b'[dispatchchain] no strategy matched',
    'auth_init_timeout': b'auth initialization timed out',
    'credential_bootstrap_failed': b'credentialbootstrap] startup failed',
    'module_missing': b'module_not_found',
    'uncaught_exception': b'uncaughtexception',
    'unhandled_rejection': b'unhandledrejection',
    'outer_job_unavailable': b'windows no-orphans cleanup unavailable',
}


def checked_stat(path, directory=False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise SmokeError('LOG_UNSAFE_ENTRY')
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise SmokeError('LOG_UNSAFE_ENTRY')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SmokeError('LOG_UNSAFE_ENTRY')
    return info



def opened_final_path(fd):
    """Resolve the opened object, not a second pathname lookup. Never read bytes."""
    if os.name == 'nt':
        import ctypes
        import msvcrt
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True).GetFinalPathNameByHandleW
        api.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        api.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(32768)
        size = api(msvcrt.get_osfhandle(fd), buf, len(buf), 0)
        if not size or size >= len(buf):
            raise SmokeError('LOG_TARGET_UNAVAILABLE')
        value = buf.value
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return os.path.normcase(os.path.normpath(value))
    if sys.platform.startswith('linux'):
        return os.path.normpath(os.readlink('/proc/self/fd/' + str(fd)))
    raise SmokeError('LOG_TARGET_UNAVAILABLE')


def verify_opened_target(fd, expected, trusted_root):
    actual = opened_final_path(fd)
    expected = os.path.normcase(os.path.abspath(expected))
    root = os.path.normcase(os.path.abspath(trusted_root))
    if actual != expected or os.path.commonpath((actual, root)) != root:
        raise SmokeError('LOG_TARGET_OUTSIDE_ALLOWLIST')


def classify_logs(private, cwd, dates, trusted_root=None):
    """Only exact fresh-run workspace logger files, never traverse account stores.

    Fixed official 5.9.3 source: PathUtils.getLoggerDir / LoggerStorageService
    buildLogFilename. This is bounded diagnostic reading, NOT a hostile-process
    sandbox. Call only after owned-job and reader cleanup has succeeded.
    """
    trusted_root = private.resolve(strict=True) if trusted_root is None else trusted_root
    report = {'status':'complete', 'files':[], 'total_bytes':0,
              'observed_categories':{key:0 for key in LOG_MARKERS}}
    if len(dates) > 2:
        return {**report, 'status':'date_limit'}
    roots = [private, private / 'CODEBUDDY_CONFIG_DIR', private / 'CODEBUDDY_CONFIG_DIR/logs']
    try:
        for path in roots:
            checked_stat(path, directory=True)
    except FileNotFoundError:
        return report
    except (OSError, SmokeError):
        return {**report, 'status':'unsafe_or_unreadable'}
    for date in sorted(dates):
        try:
            if datetime.date.fromisoformat(date).isoformat() != date:
                raise ValueError('noncanonical')
        except ValueError:
            return {**report, 'status':'invalid_date'}
        directory = roots[-1] / date
        try:
            checked_stat(directory, directory=True)
        except FileNotFoundError:
            continue
        except (OSError, SmokeError):
            return {**report, 'status':'unsafe_or_unreadable'}
        for workspace in (str(cwd), 'unknown-workspace'):
            digest = hashlib.md5(workspace.encode('utf-8'), usedforsecurity=False).hexdigest()
            name = Path(workspace).name + '__' + digest
            for suffix in ('.log', '.old.log'):
                path = directory / (name + suffix)
                try:
                    ancestors = roots + [directory]
                    before_dirs = [checked_stat(p, directory=True) for p in ancestors]
                    before = checked_stat(path)
                    if len(report['files']) >= 8 or before.st_size > 512 * 1024 or report['total_bytes'] + before.st_size > 2 * 1024 * 1024:
                        return {**report, 'status':'read_limit'}
                    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
                    with os.fdopen(fd, 'rb') as stream:
                        verify_opened_target(stream.fileno(), trusted_root / path.relative_to(private), trusted_root)
                        opened = os.fstat(stream.fileno())
                        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink) != (before.st_dev, before.st_ino, before.st_size, 1):
                            raise SmokeError('LOG_CHANGED')
                        current_file = checked_stat(path)
                        if (current_file.st_dev, current_file.st_ino) != (opened.st_dev, opened.st_ino):
                            raise SmokeError('LOG_CHANGED')
                        for ancestor, original in zip(ancestors, before_dirs):
                            current = checked_stat(ancestor, directory=True)
                            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                                raise SmokeError('LOG_CHANGED')
                        data = stream.read(before.st_size)
                        after = os.fstat(stream.fileno())
                        if len(data) != before.st_size:
                            return {**report, 'status':'truncated', 'partial_bytes':len(data)}
                        if (after.st_dev, after.st_ino, after.st_size, after.st_nlink, after.st_mtime_ns, after.st_ctime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, 1, opened.st_mtime_ns, opened.st_ctime_ns):
                            raise SmokeError('LOG_CHANGED')
                        verify_opened_target(stream.fileno(), trusted_root / path.relative_to(private), trusted_root)
                    report['files'].append({'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()})
                    report['total_bytes'] += len(data)
                    lowered = data.lower()
                    for key, marker in LOG_MARKERS.items():
                        report['observed_categories'][key] += lowered.count(marker)
                except FileNotFoundError:
                    continue
                except (OSError, SmokeError):
                    return {**report, 'status':'unsafe_or_unreadable'}
    return report


def observe(owner, stage, timeout=45, capture=None, diagnostics=None):
    events, overflow, buffers, threads = capture or capture_output(owner)
    if stage == 'initialize':
        # One bounded request, no client responses, authentication, session/new or prompt.
        owner.stdin.write((json.dumps(INITIALIZE, separators=(',', ':')) + '\n').encode())
        owner.stdin.flush()
    else:
        owner.stdin.close()
    deadline = time.monotonic() + timeout
    pending = bytearray()
    ended = set()
    summary = None
    while time.monotonic() < deadline:
        if overflow.is_set():
            raise SmokeError('NATIVE_OUTPUT_LIMIT_OR_IO')
        try:
            label, data = events.get(timeout=.05)
        except queue.Empty:
            # EOF may precede the host process signal. Re-evaluate completion
            # even when no further pipe events can arrive.
            label, data = None, b''
        if data is None:
            ended.add(label)
        elif label == 'stdout' and stage == 'initialize':
            pending.extend(data)
            while b'\n' in pending:
                line, _, pending = pending.partition(b'\n')
                if not line.strip():
                    continue
                frame = parse_frame(line)
                if summary is not None:
                    raise SmokeError('EXTRA_PROTOCOL_FRAME')
                summary = validate_initialize(frame)
            if summary is not None:
                # No more RPCs are sent. Caller immediately terminates owned job.
                break
        if stage == 'help' and len(ended) == 2 and owner.host_exited():
            if owner.exit_code() != 0:
                raise SmokeError('HELP_EXIT_NONZERO')
            text = bytes(buffers['stdout']) + bytes(buffers['stderr'])
            summary = {'help_flag_observed': b'--help' in text, 'acp_flag_observed': b'--acp' in text}
            if not summary['help_flag_observed']:
                raise SmokeError('HELP_NOT_OBSERVED')
            break
        if stage == 'initialize' and len(ended) == 2:
            observe_eof_exit(owner, deadline, diagnostics if diagnostics is not None else {})
            raise SmokeError('INITIALIZE_EOF')
    if summary is None:
        raise SmokeError('NATIVE_DEADLINE')
    return summary, buffers, threads, overflow


def validate_complete_stdout(data):
    # Recheck AFTER termination/EOF join, including chunks not observed by the loop.
    lines = data.split(b'\n')
    if lines[-1].strip():
        raise SmokeError('TRAILING_PROTOCOL_FRAGMENT')
    frames = [line for line in lines[:-1] if line.strip()]
    if len(frames) != 1:
        raise SmokeError('EXTRA_OR_MISSING_PROTOCOL_FRAME')
    return validate_initialize(parse_frame(frames[0]))


def verify_keepalive(path, private):
    """Verify only our fresh, fixed preload; never inspect vendor account data."""
    try:
        trusted = private.resolve(strict=True)
        info = path.lstat()
        if (not path.is_absolute() or path.parent != trusted or
                path.is_symlink() or not stat.S_ISREG(info.st_mode) or
                info.st_size != len(KEEPALIVE_BYTES) or
                path.resolve(strict=True) != path or sha(path) != KEEPALIVE_SHA):
            raise SmokeError('KEEPALIVE_INTEGRITY_FAILED')
    except OSError:
        raise SmokeError('KEEPALIVE_INTEGRITY_FAILED') from None
    return path


def create_keepalive(private):
    path = private.resolve(strict=True) / 'acp-keepalive.cjs'
    try:
        with path.open('xb') as stream:
            stream.write(KEEPALIVE_BYTES)
    except OSError:
        raise SmokeError('KEEPALIVE_CREATE_FAILED') from None
    return verify_keepalive(path, private)


def execute_stage(executable, entry, root, stage, job_class, diagnostics=None):
    if stage not in {'help', 'initialize'}:
        raise SmokeError('UNAUTHORIZED_STAGE')
    diagnostics = {} if diagnostics is None else diagnostics
    private = root / stage
    private.mkdir()
    cwd = private / 'workspace'
    cwd.mkdir()
    trusted_log_root = private.resolve(strict=True)  # Capture before vendor launch.
    owner = None
    summary = None
    capture = None
    failure = None
    started_date = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try:
        command = [str(executable), str(entry), '--help']
        diagnostics['entry_profile'] = 'direct_help'
        if stage == 'initialize':
            diagnostics.update(entry_profile='node_preload_initialize',
                               keepalive_ms=KEEPALIVE_MS, keepalive_sha256=KEEPALIVE_SHA)
            preload = create_keepalive(private)
            # Node runtime option, not a vendor flag. Entry remains Node's main.
            command = [str(executable), '--require', str(preload), str(entry), '--acp']
        owner = job_class.spawn(command, cwd, isolated_env(private))
        capture = capture_output(owner)
        summary, buffers, threads, overflow = observe(owner, stage, capture=capture, diagnostics=diagnostics)
    except Exception as exc:
        failure = exc
        # Atomic-spawn failures may retain an owned HANDLE for explicit cleanup.
        if owner is None:
            owner = getattr(exc, "owner", None)
        raise
    finally:
        if owner is not None:
            cleanup_error = None
            try:
                code = owner.exit_code()
                if type(code) is int:
                    diagnostics['host_exit_code_before_cleanup'] = code
            except Exception:
                cleanup_error = 'HOST_EXIT_QUERY_FAILED'
            try:
                if owner.terminate(timeout=10).get('owned_job_empty') is not True:
                    cleanup_error = 'OWNED_JOB_STOP_UNCONFIRMED'
            except Exception:
                cleanup_error = 'OWNED_JOB_STOP_UNCONFIRMED'
            if capture is not None:
                for thread in capture[3]:
                    thread.join(timeout=2)
                if any(thread.is_alive() for thread in capture[3]):
                    cleanup_error = 'READER_STOP_UNCONFIRMED'
            if capture is not None:
                captured = capture[2]
                for label in ('stdout', 'stderr'):
                    data = bytes(captured[label])
                    diagnostics[label + '_bytes'] = len(data)
                    diagnostics[label + '_sha256'] = hashlib.sha256(data).hexdigest()
                text = (bytes(captured['stdout']) + bytes(captured['stderr'])).lower()
                diagnostics['module_not_found_observed'] = b'module_not_found' in text
                diagnostics['authentication_required_observed'] = b'authentication required' in text or b'not authenticated' in text
                diagnostics['credential_protection_mentioned'] = b'credential protection' in text or b'credentialprotection' in text
                diagnostics['output_limit_or_io_flag'] = capture[1].is_set()
            # Close pipe handles only after the process is stopped and readers joined.
            try:
                result = owner.close(timeout=10)
                if result.get('owned_job_empty') is not True:
                    cleanup_error = 'OWNED_JOB_STOP_UNCONFIRMED'
            except Exception:
                cleanup_error = 'OWNED_JOB_CLOSE_UNCONFIRMED'
            if cleanup_error:
                diagnostics['cleanup_error'] = cleanup_error
                diagnostics['logs'] = {'status':'not_read_cleanup_unconfirmed'}
                if failure is None:
                    raise SmokeError(cleanup_error)
            elif stage == 'initialize' and failure is not None:
                finished_date = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
                try:
                    diagnostics['logs'] = classify_logs(private, cwd, {started_date, finished_date}, trusted_log_root)
                except Exception:
                    diagnostics['logs'] = {'status':'unavailable'}
    if overflow.is_set():
        raise SmokeError('NATIVE_OUTPUT_LIMIT_OR_IO')
    if diagnostics.get('host_exit_code_before_cleanup', 0) != 0:
        raise SmokeError('NATIVE_EXIT_NONZERO')
    if stage == 'initialize':
        summary = validate_complete_stdout(bytes(buffers['stdout']))
    return {**diagnostics, **summary, 'owned_job_empty': True,
            'stdout_bytes': len(buffers['stdout']), 'stderr_bytes': len(buffers['stderr'])}


def acquire(root, seven_zip):
    installer = root / 'official.exe'
    # Default HTTPS validation; no proxies or authentication handlers.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(URL, timeout=60) as response, installer.open('xb') as out:
        if response.url != URL:
            raise SmokeError('UNEXPECTED_DOWNLOAD_REDIRECT')
        total = 0
        deadline = time.monotonic() + 300
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > INSTALLER_SIZE or time.monotonic() > deadline:
                raise SmokeError('DOWNLOAD_LIMIT')
            out.write(chunk)
    if total != INSTALLER_SIZE or sha(installer) != INSTALLER_SHA:
        raise SmokeError('INSTALLER_HASH_MISMATCH')
    payload = root / 'payload.7z'
    extract_payload(installer, payload)
    listing = root / 'listing.txt'
    with listing.open('wb') as out:
        subprocess.run([str(seven_zip), 'l', '-slt', '-sccUTF-8', str(payload)], stdout=out,
                       stderr=subprocess.DEVNULL, check=True, timeout=60)
    if listing.stat().st_size > 8 * 1024 * 1024:
        raise SmokeError('ARCHIVE_LIST_LIMIT')
    archive_inventory(listing.read_text(encoding='utf-8'))
    target = root / 'official'
    target.mkdir()
    subprocess.run([str(seven_zip), 'x', '-y', '-bso0', '-bsp0', '-bse0',
                    '-o' + str(target), str(payload)], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=180)
    executable, entry = target / 'WorkBuddy.exe', target / ENTRY
    if not executable.is_file() or not entry.is_file() or sha(entry) != ENTRY_SHA:
        raise SmokeError('OFFICIAL_ENTRY_MISMATCH')
    for item in target.rglob('*'):
        if item.is_symlink() or item.is_junction():
            raise SmokeError('EXTRACTED_LINK')
    return executable, entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seven-zip', type=Path)
    parser.add_argument('--summary', type=Path)
    args = parser.parse_args()
    report = {'scope':'NO_ACCOUNT_NO_PROMPT_ENTRY_ONLY', 'version':VERSION,
              'installer_sha256':INSTALLER_SHA, 'payload_sha256':PAYLOAD_SHA, 'entry_sha256':ENTRY_SHA,
              'login_tested':False, 'model_tested':False, 'status':'FAIL'}
    try:
        if sys.platform != 'win32' or not args.seven_zip or not args.seven_zip.is_file():
            raise SmokeError('WINDOWS_EXISTING_7ZIP_REQUIRED')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
        from native_harness_api.windows_job import OwnedWindowsJob
        with tempfile.TemporaryDirectory(prefix='wb-no-account-') as temp:
            root = Path(temp)
            executable, entry = acquire(root, args.seven_zip)
            for stage in ('help', 'initialize'):
                report['current_stage'] = stage
                report[stage] = {}
                report[stage] = execute_stage(executable, entry, root, stage, OwnedWindowsJob, report[stage])
            report['status'] = 'PASS_SCOPED_NO_ACCOUNT_ENTRY'
    except Exception as exc:
        # Never emit arbitrary exception/path/native output or environment.
        report['error'] = str(exc) if isinstance(exc, SmokeError) else 'INTERNAL_OR_OS_FAILURE'
    raw = json.dumps(report, sort_keys=True) + '\n'
    if args.summary:
        args.summary.write_text(raw, encoding='utf-8')
    print(raw, end='')
    return 0 if report['status'] == 'PASS_SCOPED_NO_ACCOUNT_ENTRY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
