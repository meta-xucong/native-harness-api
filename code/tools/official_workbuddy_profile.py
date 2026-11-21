"""Hosted disposable Windows CI only: one initialize with official startup profiling.

Never a local retry tool, account/login test or model channel. No vendor code is
copied into this repository. Raw profiler data and native streams are not exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys

from installed_workbuddy_smoke import phase, stage_intent
from official_workbuddy_smoke import acquire, isolated_env, VERSION, INSTALLER_SHA

COLLECTOR_SHA = 'f1ffe40a2f3a0e4e3e41178e9d19dca12f8c503bdcf240701beeb2af51a7b610'
COLLECTOR_SIZE = 14142
PROFILE_NAME = 'acp-init-once.json'
MAX_PROFILE = 4 * 1024 * 1024
MAX_ENTRIES = 10000
PHASES = frozenset(('collector-ready', 'bootstrap', 'first-model-request',
    'endpoint-ready', 'ui-ready', 'application-ready', 'process_start', 'shim_load',
    'module_load', 'di_container_init', 'post_construct', 'settings_load',
    'memory_load', 'plugin_load', 'marketplace_load', 'mcp_config_load',
    'mcp_server_connect', 'tool_register', 'auth_init', 'cli_parse', 'stdin_read',
    'auth_wait', 'runner_init', 'agent_interceptors', 'agent_llm_call', 'ui_ready', 'ready'))
STATUSES = frozenset(('complete', 'error', 'pending'))
TOP = frozenset(('schemaVersion', 'startedAt', 'clock', 'target', 'reason', 'exitCode',
    'durationMs', 'coveredMs', 'uncoveredMs', 'bundle', 'milestones', 'spans', 'events',
    'warnings', 'droppedEntries', 'applicationName', 'version', 'pid', 'platform',
    'arch', 'runtime', 'runtimeVersion', 'runtimeUptimeAtEntryMs', 'resources'))


class ProfileError(Exception):
    pass


def require(condition):
    if not condition:
        raise ProfileError()


def keys(value, required, optional=()):
    require(type(value) is dict and set(required) <= value.keys() <= set(required) | set(optional))


def integer(value, maximum=2**63-1, minimum=0):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def number(value, maximum=180000):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum)
    return value


def string(value, maximum=4096):
    require(type(value) is str and len(value) <= maximum and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def summarize_profile(raw):
    """Validate the pinned collector schema; reconstruct a finite, text-safe DTO."""
    try:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_PROFILE)
        def invalid(_):
            raise ProfileError()
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
        keys(data, TOP - {'exitCode'}, {'exitCode'})
        require(type(data['schemaVersion']) is int and data['schemaVersion'] == 1)
        require(data['target'] == 'application-ready' and data['bundle'] == 'lite-wb')
        require(data['clock'] == 'monotonic milliseconds from CLI entry')
        require(data['reason'] in ('target-reached', 'exit-before-target', 'timeout'))
        require(data['platform'] == 'win32' and data['arch'] == 'x64' and data['runtime'] == 'node')
        for name in ('startedAt', 'applicationName', 'version', 'runtimeVersion'):
            string(data[name], 512)
        integer(data['pid'], 2**32-1, 1)
        number(data['runtimeUptimeAtEntryMs'], 86400000)
        duration = number(data['durationMs'])
        covered = number(data['coveredMs'], duration)
        uncovered = number(data['uncoveredMs'], duration)
        require(math.isclose(covered + uncovered, duration, abs_tol=0.000001))
        exit_code = integer(data['exitCode'], 255) if 'exitCode' in data else None
        if data['reason'] == 'exit-before-target':
            require(exit_code is not None)
        dropped = integer(data['droppedEntries'], 10**9)
        keys(data['resources'], ('memoryBytes', 'cpuMicroseconds'))
        keys(data['resources']['memoryBytes'], ('rss', 'heapTotal', 'heapUsed', 'external'), ('arrayBuffers',))
        keys(data['resources']['cpuMicroseconds'], ('user', 'system'))
        for values in data['resources'].values():
            for value in values.values():
                integer(value)
        require(type(data['warnings']) is list and len(data['warnings']) <= 64)
        for warning in data['warnings']:
            string(warning)
        require(type(data['spans']) is list and type(data['events']) is list)
        require(len(data['spans']) + len(data['events']) <= MAX_ENTRIES)
        require(type(data['milestones']) is dict and len(data['milestones']) <= MAX_ENTRIES)
        milestones = {}
        unknown = 0
        for name, value in data['milestones'].items():
            string(name, 256); number(value, duration)
            if name in PHASES:
                milestones[name] = value
            else:
                unknown += 1
        if data['reason'] == 'target-reached':
            require('application-ready' in milestones)
        counts = {}
        seen = set()
        last = None
        for span in data['spans']:
            keys(span, ('id', 'phase', 'label', 'startMs', 'endMs', 'durationMs', 'status'), ('parentId', 'sourceLabel'))
            identity = integer(span['id'], MAX_ENTRIES, 1)
            require(identity not in seen)
            if 'parentId' in span:
                require(integer(span['parentId'], MAX_ENTRIES, 1) in seen)
            seen.add(identity)
            string(span['phase'], 256); string(span['label'])
            if 'sourceLabel' in span:
                require(type(span['sourceLabel']) is bool)
            require(span['status'] in STATUSES)
            start = number(span['startMs'], duration)
            end = number(span['endMs'], duration)
            elapsed = number(span['durationMs'], duration)
            require(start <= end and math.isclose(end-start, elapsed, abs_tol=0.000001))
            if span['status'] == 'pending':
                require(end == duration)
            name = span['phase'] if span['phase'] in PHASES else 'OTHER'
            unknown += name == 'OTHER'
            count = counts.setdefault(name, {'complete': 0, 'error': 0, 'pending': 0})
            count[span['status']] += 1
            last = {'phase': name, 'status': span['status'], 'start_ms': start, 'end_ms': end}
        event_counts = {}
        for event in data['events']:
            keys(event, ('phase', 'label', 'atMs'))
            string(event['phase'], 256); string(event['label']); number(event['atMs'], duration)
            name = event['phase'] if event['phase'] in PHASES else 'OTHER'
            unknown += name == 'OTHER'
            event_counts[name] = event_counts.get(name, 0) + 1
        return {'status': 'CAPTURED_SANITIZED', 'reason': data['reason'], 'exit_code': exit_code,
                'duration_ms': duration, 'covered_ms': covered, 'uncovered_ms': uncovered,
                'span_count': len(data['spans']), 'event_count': len(data['events']),
                'warning_count': len(data['warnings']), 'dropped_entries': dropped,
                'unknown_phase_occurrences': unknown, 'known_milestones': milestones,
                'span_status_counts': counts, 'event_counts': event_counts, 'last_recorded_span': last,
                'profile_bytes': len(raw), 'profile_sha256': hashlib.sha256(raw).hexdigest()}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError):
        raise ProfileError() from None


def ancestors(path):
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400)


def identity(path):
    ancestors(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode))
    return path.resolve(strict=True), info.st_dev, info.st_ino


def read_profile(directory, trusted_identity):
    """Only the one fresh fixed collector file, after confirmed cleanup."""
    require(identity(directory) == trusted_identity)
    path = directory / PROFILE_NAME
    with os.scandir(directory) as entries:
        found = list(next(entries, None) for _ in range(2))
    if found[0] is None:
        return {'status': 'MISSING'}
    require(found[1] is None and found[0].name == PROFILE_NAME)
    ancestors(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= MAX_PROFILE)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino))
        raw = stream.read(MAX_PROFILE + 1)
        after = os.fstat(stream.fileno())
    require((after.st_size, after.st_mtime_ns) == (info.st_size, info.st_mtime_ns) and len(raw) == info.st_size)
    require(identity(directory) == trusted_identity)
    return summarize_profile(raw)


def run_once(executable, entry, root):
    """Caller supplies a new hosted-run root; reserves one attempt before launch."""
    ancestors(root)
    profile_dir = root / 'profile'
    profile_dir.mkdir()  # Never reuse an old output directory.
    trusted = identity(profile_dir)
    collector = entry.parent / 'startup-profile.cjs'
    ancestors(collector)
    info = collector.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == COLLECTOR_SIZE)
    with collector.open('rb') as stream:
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == COLLECTOR_SHA)
    environment = isolated_env(root / 'isolated')
    workspace = root / 'workspace'; workspace.mkdir()
    command = [str(executable), str(entry), '--startup-prof', '--startup-prof-dir',
               str(profile_dir), '--startup-prof-name', 'acp-init-once', '--acp']
    stage_intent(root, 'initialize')
    observed = phase(command, workspace, environment, True)
    report = {'initialize': observed, 'profile': {'status': 'NOT_READ_CLEANUP_UNCONFIRMED'}}
    if observed['cleanup_complete'] is True:
        try:
            report['profile'] = read_profile(profile_dir, trusted)
        except (ProfileError, OSError):
            report['profile'] = {'status': 'REJECTED'}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seven-zip', required=True, type=Path)
    parser.add_argument('--run-root', required=True, type=Path)
    parser.add_argument('--summary', required=True, type=Path)
    args = parser.parse_args(argv)
    report = {'scope': 'CLOUD_NO_ACCOUNT_ONE_INITIALIZE_OFFICIAL_PROFILE',
              'version': VERSION, 'installer_sha256': INSTALLER_SHA, 'collector_sha256': COLLECTOR_SHA,
              'status': 'NOT_ESTABLISHED', 'login_tested': False, 'model_tested': False}
    try:
        require(os.name == 'nt' and os.environ.get('GITHUB_ACTIONS') == 'true' and
                os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')
        require(args.run_root.is_absolute() and args.summary.is_absolute() and args.seven_zip.is_file())
        ancestors(args.run_root.parent)
        args.run_root.mkdir()  # Exclusive reservation; never delete even on failure.
        stage_intent(args.run_root, 'diagnostic')
        executable, entry = acquire(args.run_root, args.seven_zip)
        report.update(run_once(executable, entry, args.run_root))
        if report['initialize']['status'] == 'OBSERVED':
            report['status'] = 'INITIALIZE_OBSERVED_NOT_PRODUCT_READY'
    except (Exception, KeyboardInterrupt):
        report['failure_code'] = 'PREFLIGHT_OR_OS_FAILURE'
    raw = json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n'
    try:
        # Only our reconstructed fixed-schema summary, never a native output path.
        with args.summary.open('x', encoding='utf-8') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    except OSError:
        print('{"status":"SUMMARY_WRITE_FAILED"}')
        return 1
    print(raw, end='')
    return 0 if report['status'] == 'INITIALIZE_OBSERVED_NOT_PRODUCT_READY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
