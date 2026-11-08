"""One hosted credential-free public Client/custom-transport mock experiment.

No native Harness/Server/provider is constructed. Owned job is not a sandbox.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
from official_workbuddy_library import (Rejected, require, ancestors, identity,
    unique, supervise, verify_tree, environment, ASAR_SHA, ASAR_SIZE)
from official_workbuddy_smoke import acquire

PINS = {
    'package.json': ('9f7aaf7ba9cf0f8bb961e22a80c9e7ac24c2fcb4232b145e91eb39bcecb539a4', 18632),
    'lib/client/public.js': ('fe0b38691349e73e52f4a9ab2e26d085f65a03d17a412b25b70324153a2cae41', 106),
    'lib/transport/effect.js': ('abb6ecab760ea4a1bc5298b4b492b336539e7d775d9b89ccb6ad9e7f74eb566d', 2034),
}


def verify_pairs(archive, package):
    """Attest the three public members and original archive header; never execute."""
    ancestors(archive); before = archive.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == ASAR_SIZE)
    with archive.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino))
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == ASAR_SHA)
        stream.seek(0); prefix = stream.read(16); require(len(prefix) == 16)
        first, header_size, payload_size, json_size = struct.unpack('<4I', prefix)
        require(first == 4 and 8 <= header_size <= 8 * 1024 * 1024 and header_size % 4 == 0)
        require(payload_size == header_size - 4 and 0 < json_size <= payload_size - 4)
        require(header_size == 8 + ((json_size + 3) // 4) * 4 and 8 + header_size <= before.st_size)
        raw = stream.read(json_size); require(len(raw) == json_size)
        def invalid(_): raise Rejected()
        header = json.loads(raw.decode('utf8'), object_pairs_hook=unique, parse_constant=invalid)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns))
    for name, (expected, size) in PINS.items():
        record = header
        for part in ('node_modules', '@genie', 'agent-harness', *name.split('/')):
            require(type(record) is dict and type(record.get('files')) is dict)
            record = record['files'].get(part)
        require(type(record) is dict and record.get('unpacked') is True)
        require('link' not in record and 'offset' not in record and 'files' not in record)
        require(type(record.get('size')) is int and record['size'] == size)
        integrity = record.get('integrity')
        require(type(integrity) is dict and integrity.get('algorithm') == 'SHA256' and integrity.get('hash') == expected)
        path = package / name; ancestors(path); info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == size)
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino))
            require(hashlib.file_digest(stream, 'sha256').hexdigest() == expected)
            after = os.fstat(stream.fileno())
            require((info.st_size, info.st_mtime_ns) == (after.st_size, after.st_mtime_ns))
    manifest = json.loads((package / 'package.json').read_text(encoding='utf8'), object_pairs_hook=unique)
    require(manifest['exports']['./client']['default'] == './lib/client/public.js')
    require(manifest['exports']['./transport']['default'] == './lib/transport/effect.js')


PASS_BOOLS = (
    'entry_verified', 'import_ok', 'initialization_verified', 'ack_not_account_proof',
    'synthetic_account_read_verified', 'synthetic_status_verified', 'cancel_wire_seen',
    'cancel_rejected', 'malformed_account_rejected', 'abort_wire_seen',
    'timeout_rejected', 'late_ack_not_ready',
)
BOOLS = PASS_BOOLS + ('success', 'all_account_data_synthetic', 'native_account_verified', 'real_model_ready')
NUMBERS = {'schema_version': 1, 'cases_completed': 4, 'clients_closed': 4, 'peers_closed': 4,
           'wire_messages': 81, 'failure_stage': 7, 'elapsed_ms': 120000}
KEYS = set(BOOLS) | set(NUMBERS)


def parse_dto(raw):
    try:
        require(type(raw) is bytes and 0 < len(raw) <= 4096)
        def invalid(_): raise Rejected()
        d = json.loads(raw.decode('utf8'), object_pairs_hook=unique, parse_constant=invalid)
        require(type(d) is dict and set(d) == KEYS)
        require(all(type(d[k]) is bool for k in BOOLS))
        require(all(type(d[k]) is int and 0 <= d[k] <= bound for k, bound in NUMBERS.items()))
        require(d['schema_version'] == 1 and d['failure_stage'] in (0, 1, 2, 3, 4, 5, 7))
        require(d['all_account_data_synthetic'] and not d['native_account_verified'] and not d['real_model_ready'])
        require(not d['import_ok'] or d['entry_verified'])
        require(not d['initialization_verified'] or d['import_ok'])
        require(not any(d[k] for k in PASS_BOOLS[3:]) or d['initialization_verified'])
        require(not d['synthetic_account_read_verified'] or d['ack_not_account_proof'])
        require(not d['synthetic_status_verified'] or d['synthetic_account_read_verified'])
        require(not d['cancel_rejected'] or d['cancel_wire_seen'])
        require(not d['timeout_rejected'] or d['abort_wire_seen'])
        require(not d['late_ack_not_ready'] or d['timeout_rejected'])
        require(d['cases_completed'] <= d['clients_closed'] and d['cases_completed'] <= d['peers_closed'])
        require(d['clients_closed'] == 0 or d['import_ok'])
        require(d['peers_closed'] == 0 or d['import_ok'])
        require(d['cases_completed'] == 0 or d['initialization_verified'])
        require(not d['malformed_account_rejected'] or d['cases_completed'] >= 2)
        require(not d['abort_wire_seen'] or d['cases_completed'] >= 3)
        complete = all(d[k] for k in PASS_BOOLS) and d['cases_completed'] == 4 and d['clients_closed'] == 4 and d['peers_closed'] == 4 and d['failure_stage'] == 0
        require(d['success'] == complete and (d['failure_stage'] != 0 or complete))
        return d
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Rejected() from None


def read_dto(directory, trusted):
    require(identity(directory) == trusted)
    with os.scandir(directory) as it: found = [next(it, None), next(it, None)]
    require(found[0] is not None and found[1] is None and found[0].name == 'client-mock-summary.json')
    path = directory / 'client-mock-summary.json'; ancestors(path); before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 4096)
    with path.open('rb') as f:
        opened = os.fstat(f.fileno()); require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino))
        raw = f.read(4097); after = os.fstat(f.fileno())
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and len(raw) == before.st_size)
    require(identity(directory) == trusted)
    return parse_dto(raw)


def collect(controller, directory, trusted):
    result = {'controller': controller, 'dto_valid': False, 'success': False}
    if not controller['cleanup_complete']: return result
    try:
        result['dto'] = read_dto(directory, trusted); result['dto_valid'] = True
        result['success'] = controller['success'] and result['dto']['success']
    except (Rejected, OSError): pass
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--seven-zip', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args(argv); report = {'success': False, 'preflight': False, 'preflight_stage': 1}
    try:
        require(os.name == 'nt' and os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')
        require(args.run_root.is_absolute() and args.summary.is_absolute())
        require(args.run_root == Path(os.environ['RUNNER_TEMP']) / 'native-harness-client-mock-once')
        require(args.summary == Path(os.environ['GITHUB_WORKSPACE']) / 'client-mock-sanitized-summary.json')
        ancestors(args.run_root.parent); ancestors(args.summary.parent); require(not args.summary.exists())
        report['preflight_stage'] = 2
        args.run_root.mkdir()
        with (args.run_root / 'attempt.intent').open('xb') as f: f.write(b'one-attempt-no-retry\n'); f.flush(); os.fsync(f.fileno())
        report['preflight_stage'] = 3
        executable, _ = acquire(args.run_root, args.seven_zip)
        report['preflight_stage'] = 4
        verify_tree(executable.parent, '5c1f3ccee6ac24a630e9f18b3a49f559b3fbad76c1ec47a2a2448cb9b6f54ab2', 6166)
        ancestors(executable); require(executable.stat().st_nlink == 1 and executable.stat().st_size == 204590360)
        with executable.open('rb') as f: require(hashlib.file_digest(f, 'sha256').hexdigest() == 'bbf504003825e0e22dd8c401b396d79db78ec603558088565d1b2e81561704a0')
        package = executable.parent / 'resources/app.asar.unpacked/node_modules/@genie/agent-harness'
        verify_pairs(executable.parent / 'resources/app.asar', package)
        report['preflight_stage'] = 5
        env = environment(args.run_root); workspace = args.run_root / 'workspace'; workspace.mkdir()
        output = args.run_root / 'output'; output.mkdir(); trusted = identity(output)
        driver = Path(__file__).with_name('workbuddy_client_mock_driver.mjs').resolve(strict=True)
        ancestors(driver); require(driver.stat().st_nlink == 1)
        command = [str(executable), str(driver), '--run-root', str(args.run_root)]
        report['preflight_stage'] = 6
        report = collect(supervise(command, workspace, env), output, trusted)
        report.update(preflight=True, preflight_stage=0, loader_revision=1, archive_pairs_verified=True)
    except (Exception, KeyboardInterrupt): pass
    try:
        with args.summary.open('x', encoding='utf8') as f:
            json.dump(report, f, separators=(',', ':')); f.write('\n'); f.flush(); os.fsync(f.fileno())
    except OSError: return 1
    return 0 if report['success'] else 1


if __name__ == '__main__': raise SystemExit(main())
