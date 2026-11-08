"""One owned, anonymous public Harness SFE catalog diagnostic.

No authentication, provision, session or model request is implemented. Fresh
hosted-runner directories and process ownership are not an outbound sandbox.
Raw native streams are parsed in memory or discarded, never exported.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import queue
import threading
import time

from native_harness_api.windows_job import OwnedWindowsJob
from public_harness_catalog_protocol import CatalogProtocol

STREAM_LIMIT = 1024 * 1024
FRAME_LIMIT = 65536
STDIN_LIMIT = 4096
RETAINED = []


def _require(value):
    if not value:
        raise ValueError('REJECTED')


def supervise(command, cwd, env, *, timeout=50, cleanup_timeout=5,
              job_class=OwnedWindowsJob):
    """Pipe ownership and deadlines are independent of protocol completion."""
    for value, maximum in ((timeout, 120), (cleanup_timeout, 10)):
        _require(type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum)
    protocol = CatalogProtocol()
    result = dict(schema_version=1, success=False, failure_stage=1,
                  stdin_bytes=0, stdout_bytes=0, stderr_bytes=0,
                  stdin_closed=False, natural_exit_zero=False,
                  natural_job_empty=False, host_exited=False,
                  owned_job_empty=False, handles_closed=False,
                  io_threads_stopped=False, cleanup_complete=False,
                  native_account_verified=False, real_model_ready=False)
    incoming = queue.Queue(maxsize=64)
    outgoing = queue.Queue(maxsize=8)
    fault, stop = threading.Event(), threading.Event()
    totals = {'stdout': 0, 'stderr': 0, 'stdin': 0}
    writer_state = {'closed': False}
    owner = None
    threads = []
    uncertain = False
    closing = False
    late_frame = False

    def enqueue(kind, value=None):
        try:
            incoming.put_nowait((kind, value))
        except queue.Full:
            fault.set()

    def reader(label, stream):
        pending = bytearray()
        frames = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    if pending:
                        fault.set()
                    enqueue(label + '_eof')
                    return
                totals[label] += len(chunk)
                if totals[label] > STREAM_LIMIT:
                    fault.set()
                    return
                if label == 'stderr':
                    continue
                pending.extend(chunk)
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n')
                    pending = bytearray(rest)
                    frames += 1
                    if not line or len(line) > FRAME_LIMIT or frames > 32:
                        fault.set()
                        return
                    enqueue('frame', bytes(line))
                if len(pending) > FRAME_LIMIT:
                    fault.set()
                    return
        except BaseException:
            fault.set()

    def writer(stream):
        try:
            while not stop.is_set():
                try:
                    frame = outgoing.get(timeout=.05)
                except queue.Empty:
                    continue
                if frame is None:
                    break
                _require(type(frame) is bytes and 0 < len(frame) <= 1024)
                _require(totals['stdin'] + len(frame) <= STDIN_LIMIT)
                view = memoryview(frame)
                while view:
                    written = stream.write(view)
                    _require(type(written) is int and 0 < written <= len(view))
                    totals['stdin'] += written
                    view = view[written:]
                stream.flush()
        except BaseException:
            fault.set()
        finally:
            try:
                stream.close()
                writer_state['closed'] = True
            except BaseException:
                fault.set()

    def send(frames):
        _require(type(frames) is tuple)
        for frame in frames:
            _require(type(frame) is bytes and 0 < len(frame) <= 1024)
            outgoing.put_nowait(frame)

    def close_input():
        nonlocal closing
        if not closing:
            outgoing.put_nowait(None)
            closing = True

    try:
        deadline = time.monotonic() + timeout
        owner = job_class.spawn(command, cwd, env)
        for target, args in ((writer, (owner.stdin,)),
                             (reader, ('stdout', owner.stdout)),
                             (reader, ('stderr', owner.stderr))):
            thread = threading.Thread(target=target, args=args, daemon=True)
            threads.append(thread)
            uncertain = True
            thread.start()
            uncertain = False
        send(protocol.start())
        result['failure_stage'] = 2
        eof = set()
        while True:
            if fault.is_set():
                protocol.fail('transport')
                result['failure_stage'] = 2
                break
            if time.monotonic() >= deadline:
                protocol.fail('timeout')
                result['failure_stage'] = 4
                break
            try:
                kind, value = incoming.get(timeout=.01)
            except queue.Empty:
                kind, value = None, None
            if kind == 'frame':
                if closing or protocol.done:
                    protocol.fail('transport')
                    late_frame = True
                    result['failure_stage'] = 6
                    break
                send(protocol.receive(value))
                if protocol.done:
                    close_input()
                    if not protocol.succeeded:
                        result['failure_stage'] = 3
                        # Give the fixed denial (if any) a chance to drain before
                        # bounded owned-process termination. It grants nothing.
            elif kind in ('stdout_eof', 'stderr_eof'):
                eof.add(kind)
                if kind == 'stdout_eof' and not protocol.done:
                    protocol.fail('eof')
                    close_input()
                    result['failure_stage'] = 3
            if owner.host_exited():
                result['natural_exit_zero'] = owner.exit_code() == 0
                if len(eof) == 2 and all(not t.is_alive() for t in threads):
                    result['natural_job_empty'] = owner.active_processes() == 0
                    if (protocol.succeeded and not late_frame and
                            result['natural_exit_zero'] and result['natural_job_empty']):
                        result['failure_stage'] = 0
                    elif result['failure_stage'] == 2:
                        result['failure_stage'] = 3
                    break
            if protocol.done and not protocol.succeeded and writer_state['closed']:
                break
    except BaseException as error:
        protocol.fail('transport')
        if owner is None:
            owner = getattr(error, 'owner', None)
    finally:
        stop.set()
        deadline = time.monotonic() + cleanup_timeout
        if owner is not None:
            try:
                result['owned_job_empty'] = owner.terminate(max(0, deadline-time.monotonic())).get('owned_job_empty') is True
                result['host_exited'] = owner.host_exited() is True
            except BaseException:
                pass
        for thread in threads:
            try:
                if thread.ident is not None:
                    thread.join(max(0, deadline-time.monotonic()))
            except BaseException:
                pass
        result['io_threads_stopped'] = not uncertain and all(not t.is_alive() for t in threads)
        if owner is not None and result['io_threads_stopped']:
            try:
                closed = owner.close(max(0, deadline-time.monotonic()))
                result['owned_job_empty'] = closed.get('owned_job_empty') is True
                result['handles_closed'] = True
            except BaseException:
                pass
        result['cleanup_complete'] = all(result[k] is True for k in (
            'host_exited', 'owned_job_empty', 'handles_closed', 'io_threads_stopped'))
        if owner is not None and not result['cleanup_complete']:
            RETAINED.append(owner)
        if not result['cleanup_complete']:
            result['failure_stage'] = 5
        elif fault.is_set():
            result['failure_stage'] = 2
        result['stdin_closed'] = writer_state['closed']
        for label in totals:
            result[label + '_bytes'] = min(totals[label], STREAM_LIMIT+4096)
        result['protocol'] = protocol.summary()
        result['success'] = (result['failure_stage'] == 0 and protocol.succeeded
                             and result['stdin_closed'] and result['cleanup_complete'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--seven-zip', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args(argv)
    result = {'success': False, 'preflight': False, 'preflight_stage': 1}
    try:
        _require(os.name == 'nt' and os.environ.get('GITHUB_ACTIONS') == 'true'
                 and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
                 and os.environ.get('GITHUB_RUN_ATTEMPT') == '1')
        _require(args.run_root.is_absolute() and args.summary.is_absolute())
        _require(args.run_root == Path(os.environ['RUNNER_TEMP']) / 'public-harness-catalog-once')
        _require(args.summary == Path(os.environ['GITHUB_WORKSPACE']) / 'public-harness-catalog-summary.json')
        from public_harness_catalog_assets import acquire_assets
        from official_workbuddy_library import ancestors, environment
        ancestors(args.run_root.parent)
        ancestors(args.summary.parent)
        _require(not args.summary.exists())
        result['preflight_stage'] = 2
        args.run_root.mkdir()
        with (args.run_root / 'attempt.intent').open('xb') as stream:
            stream.write(b'one-attempt-no-login-no-model-no-retry\n')
            stream.flush()
            os.fsync(stream.fileno())
        result['preflight_stage'] = 3
        assets = acquire_assets(args.run_root, args.seven_zip)
        result['preflight_stage'] = 4
        env = environment(args.run_root)
        del env['ELECTRON_RUN_AS_NODE']
        env['AGENT_HARNESS_HOST_NODE_EXECUTABLE'] = str(assets['host_node'])
        env['DISABLE_TELEMETRY'] = '1'
        workspace = args.run_root / 'workspace'
        workspace.mkdir()
        command = [str(assets['executable']), '--stdio', '--product-config', str(assets['product_config'])]
        result['preflight_stage'] = 5
        result = supervise(command, workspace, env)
        result.update(preflight=True, preflight_stage=0, diagnostic_revision=1,
                      asset_pins=assets['pins'])
    except (Exception, KeyboardInterrupt):
        pass
    try:
        encoded = json.dumps(result, separators=(',', ':')) + '\n'
        _require(len(encoded.encode('utf8')) <= 16384)
        with args.summary.open('x', encoding='utf8') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError):
        return 1
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
