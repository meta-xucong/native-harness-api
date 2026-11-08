"""One hosted native managed-catalog experiment with a fresh empty account.

No login/logout/provision/session/model requests. Native reads can make locks,
watchers and directories. Fresh HOME and OwnedWindowsJob are not a sandbox.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from official_workbuddy_library import (Rejected, require, ancestors, identity,
    unique, supervise, verify_tree, environment, verify_asar_pair)
from official_workbuddy_smoke import acquire

STEPS = ('entry_verified', 'empty_roots_verified', 'product_config_verified', 'import_ok',
         'harness_created', 'client_created', 'connect_returned', 'connected',
         'product_read_returned', 'product_identity_verified', 'managed_catalog_verified',
         'account_read_returned', 'account_unauthenticated', 'auth_status_returned',
         'auth_status_unauthenticated')
PASS_BOOLS = STEPS + ('client_closed', 'harness_closed', 'harness_settled')
BOOLS = PASS_BOOLS + ('success', 'native_account_verified', 'real_model_ready')
NUMBERS = {'schema_version': 1, 'elicitation_seen': 1, 'failure_stage': 10, 'elapsed_ms': 120000}
KEYS = set(BOOLS) | set(NUMBERS)


def parse_dto(raw):
    try:
        require(type(raw) is bytes and 0 < len(raw) <= 4096)
        def invalid(_): raise Rejected()
        d = json.loads(raw.decode('utf8'), object_pairs_hook=unique, parse_constant=invalid)
        require(type(d) is dict and set(d) == KEYS)
        require(all(type(d[k]) is bool for k in BOOLS))
        require(all(type(d[k]) is int and 0 <= d[k] <= bound for k, bound in NUMBERS.items()))
        require(d['schema_version'] == 1 and not d['native_account_verified'] and not d['real_model_ready'])
        for before, after in zip(STEPS, STEPS[1:]): require(not d[after] or d[before])
        require(d['failure_stage'] != 0 or d['elicitation_seen'] == 0)
        complete = all(d[k] for k in PASS_BOOLS) and d['failure_stage'] == 0 and d['elicitation_seen'] == 0
        require(d['success'] == complete and (d['failure_stage'] != 0 or complete))
        return d
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Rejected() from None

def read_dto(directory, trusted):
    require(identity(directory) == trusted)
    with os.scandir(directory) as it: found = [next(it, None), next(it, None)]
    require(found[0] is not None and found[1] is None and found[0].name == 'managed-catalog-summary.json')
    path = directory / 'managed-catalog-summary.json'; ancestors(path); before = path.stat()
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
        require(args.run_root == Path(os.environ['RUNNER_TEMP']) / 'native-harness-managed-catalog-once')
        require(args.summary == Path(os.environ['GITHUB_WORKSPACE']) / 'managed-catalog-sanitized-summary.json')
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
        verify_asar_pair(executable.parent / 'resources/app.asar', package)
        report['preflight_stage'] = 5
        env = environment(args.run_root); workspace = args.run_root / 'workspace'; workspace.mkdir()
        output = args.run_root / 'output'; output.mkdir(); trusted = identity(output)
        driver = Path(__file__).with_name('workbuddy_managed_catalog_driver.mjs').resolve(strict=True)
        ancestors(driver); require(driver.stat().st_nlink == 1)
        command = [str(executable), str(driver), '--run-root', str(args.run_root)]
        report['preflight_stage'] = 6
        report = collect(supervise(command, workspace, env), output, trusted)
        report.update(preflight=True, preflight_stage=0, loader_revision=1, archive_pair_verified=True)
    except (Exception, KeyboardInterrupt): pass
    try:
        with args.summary.open('x', encoding='utf8') as f:
            json.dump(report, f, separators=(',', ':')); f.write('\n'); f.flush(); os.fsync(f.fileno())
    except OSError: return 1
    return 0 if report['success'] else 1


if __name__ == '__main__': raise SystemExit(main())
