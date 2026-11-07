"""No-account official Windows entry smoke. Never authenticates or sends prompts.

Only --help and one ACP initialize are allowed. This does not validate login,
models, tools, billing, WorkBuddy service terms, or a production model channel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import subprocess
import sys
import struct
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


def observe(owner, stage, timeout=45, capture=None):
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
            continue
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


def execute_stage(executable, entry, root, stage, job_class, diagnostics=None):
    if stage not in {'help', 'initialize'}:
        raise SmokeError('UNAUTHORIZED_STAGE')
    diagnostics = {} if diagnostics is None else diagnostics
    private = root / stage
    private.mkdir()
    cwd = private / 'workspace'
    cwd.mkdir()
    owner = None
    summary = None
    capture = None
    try:
        owner = job_class.spawn([str(executable), str(entry), '--help' if stage == 'help' else '--acp'],
                                cwd, isolated_env(private))
        capture = capture_output(owner)
        summary, buffers, threads, overflow = observe(owner, stage, capture=capture)
    except Exception as exc:
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
            result = owner.close(timeout=10)
            if result.get('owned_job_empty') is not True:
                cleanup_error = 'OWNED_JOB_STOP_UNCONFIRMED'
            if cleanup_error:
                raise SmokeError(cleanup_error)
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
