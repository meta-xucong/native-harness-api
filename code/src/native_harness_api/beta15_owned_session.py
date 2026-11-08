"""Bounded single-session orchestration over an explicitly supplied owned transport.

No runtime discovery, native-account configuration, SDK loading, automatic retry,
production admission or fallback exists here. Only self-authored child fixtures are
executed in tests. Caller owns authorization, launch provenance and durable intent.
"""
from __future__ import annotations
import json
import math
import queue
import threading
import time
from .beta15_session import SessionCoordinator, decode_frame
from .beta15_reducer import ContractError

SHUTDOWN_ID = 2147483647


class OwnedSessionEngine:
    MAX_FRAME = 262144
    MAX_OUTPUT = 4 * 1024 * 1024
    MAX_STDERR = 65536

    def __init__(self, transport):
        self.transport = transport
        self._attempted = False
        self._closing = False
        self._io_fault = None
        self._threads = []
        self._frames = queue.Queue(maxsize=512)
        self._writes = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self.stdout_bytes = 0
        self.stderr_bytes = 0
        self.eof = False
        self.stderr_eof = False
        self.shutdown_ack = False
        self._shutdown_issued = False
        self._io_ready = False
        self.coordinator = None
        self.fault = None

    def _fail(self, reason):
        if self.fault is None:
            self.fault = reason

    def _io_fail(self, reason):
        with self._lock:
            if self._io_fault is None:
                self._io_fault = reason

    def _start_thread(self, callback, *args):
        # Retain even ambiguous starts; never claim cleanup while a registered
        # thread might still use an owned pipe.
        record = {'thread': threading.Thread(target=callback, args=args, daemon=True),
                  'started': False}
        self._threads.append(record)
        record['thread'].start()
        record['started'] = True

    def _read(self, stream, stderr=False):
        try:
            while True:
                data = stream.read(4096) if stderr else stream.readline(self.MAX_FRAME + 2)
                if not data:
                    if not stderr:
                        self._frames.put_nowait(None)
                    else:
                        self.stderr_eof = True
                    return
                if stderr:
                    self.stderr_bytes += len(data)
                    if self.stderr_bytes > self.MAX_STDERR:
                        self._io_fail('STDERR_LIMIT')
                        return
                    continue  # Never retain or emit potentially secret stderr.
                self.stdout_bytes += len(data)
                if self.stdout_bytes > self.MAX_OUTPUT:
                    self._io_fail('OUTPUT_LIMIT')
                    return
                if len(data) > self.MAX_FRAME + 1 or not data.endswith(b'\n'):
                    self._io_fail('FRAME_LIMIT')
                    return
                self._frames.put_nowait(data[:-1])
        except queue.Full:
            self._io_fail('OUTPUT_LIMIT')
        except Exception:
            if not self._closing:
                self._io_fail('READ_FAILED')

    def _write(self, stream):
        try:
            while True:
                item = self._writes.get()
                if item is None:
                    return
                data, expires, result = item
                if self._closing or time.monotonic() >= expires:
                    result.put(False)
                    continue
                try:
                    remaining = memoryview(data)
                    while remaining:
                        if self._closing or time.monotonic() >= expires:
                            raise TimeoutError()
                        count = stream.write(remaining)
                        if not count:
                            raise OSError()
                        remaining = remaining[count:]
                    stream.flush()
                    result.put(True)
                except Exception:
                    result.put(False)
                    if not self._closing:
                        self._io_fail('WRITE_FAILED')
        except Exception:
            if not self._closing:
                self._io_fail('WRITE_FAILED')

    def _send(self, command, deadline):
        if self._closing or self._io_fault or time.monotonic() >= deadline:
            return False
        try:
            encoded = json.dumps(command, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')
            if len(encoded) > self.MAX_FRAME:
                raise ValueError()
            complete = queue.Queue(maxsize=1)
            self._writes.put_nowait((encoded + b'\n', deadline, complete))
            return complete.get(timeout=max(0, deadline - time.monotonic())) is True
        except (ValueError, UnicodeError, queue.Empty, queue.Full):
            self._fail('SEND_UNCONFIRMED')
            return False

    def _dispatch(self, raw):
        if raw is None:
            self.eof = True
            return
        try:
            value = decode_frame(raw)
            if 'id' in value:
                if type(value['id']) is int and value['id'] == SHUTDOWN_ID:
                    if (not self._shutdown_issued or self.shutdown_ack or set(value) != {'id', 'result'} or
                            type(value['result']) is not dict or set(value['result']) != {'closed'} or
                            value['result']['closed'] is not True):
                        self._fail('INVALID_SHUTDOWN_RESPONSE')
                    else:
                        self.shutdown_ack = True
                else:
                    self.coordinator.response(value)
            elif set(value) == {'method', 'params'} and value['method'] == 'session/update':
                self.coordinator.update(value['params'])
            elif set(value) == {'method', 'params'} and value['method'] == 'shim/fault':
                self._fail('SHIM_FAULT')
            else:
                self._fail('INVALID_FRAME')
        except ContractError:
            self._fail('INVALID_FRAME')

    def _pump_until(self, predicate, deadline, *, honor_turn=True, cancellation=None):
        while not predicate():
            if self._io_fault:
                self._fail(self._io_fault)
                return False
            if honor_turn:
                if self.fault or not self.coordinator.tick():
                    return False
                if cancellation is not None and cancellation.is_set():
                    return False
            if self.eof or time.monotonic() >= deadline:
                return False
            try:
                self._dispatch(self._frames.get(timeout=min(.02, max(0, deadline-time.monotonic()))))
            except queue.Empty:
                pass
        return True

    def run(self, command, cwd, env, *, workspace, model_id, prompt,
            timeout=120, cleanup_timeout=10, cancellation=None):
        """Launch one caller-verified shim, return observations, never restart it.

        The injected transport must be an owned-process implementation. This API
        is not exposed by the service/CLI and does not create native launch args.
        """
        if self._attempted:
            raise ValueError('SINGLE_ATTEMPT_ONLY')
        self._attempted = True
        for value, maximum in ((timeout, 3600), (cleanup_timeout, 300)):
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError('FINITE_TIMEOUT_REQUIRED')
        deadline = time.monotonic() + timeout
        self.coordinator = SessionCoordinator(deadline=deadline)
        if cancellation is not None and not isinstance(cancellation, threading.Event):
            raise ValueError('THREADING_EVENT_REQUIRED')
        if cancellation is not None and cancellation.is_set():
            return {'outcome': 'cancelled', 'fault': None,
                    'session': self.coordinator.evidence(), 'launch_attempted': False,
                    'cleanup_not_required': True, 'shutdown_acknowledged': False,
                    'natural_owned_exit': False, 'natural_exit_zero': False,
                    'host_exit_code_before_cleanup': None,
                    'stdout_eof_observed': False, 'stderr_eof_observed': False,
                    'cleanup': {'host_exited': None, 'owned_job_empty': None,
                                'handles_closed': False, 'cleanup_complete': False,
                                'io_threads_stopped': True},
                    'stdout_bytes': 0, 'stderr_bytes': 0,
                    'liveness': 'not_started', 'retry_safe': False}
        cleanup = {'host_exited': None, 'owned_job_empty': None,
                   'handles_closed': False, 'cleanup_complete': False}
        natural = False
        natural_exit_code = None
        io_stopped = False
        try:
            self.transport.start(command, cwd, env)
            self._start_thread(self._read, self.transport.stdout)
            self._start_thread(self._read, self.transport.stderr, True)
            self._start_thread(self._write, self.transport.stdin)
            self._io_ready = True
            stages = [
                (lambda: self.coordinator.new_session(workspace), lambda: self.coordinator.phase == 'created'),
                (lambda: self.coordinator.select_model(model_id), lambda: self.coordinator.phase == 'selected'),
                (lambda: self.coordinator.prompt(prompt), lambda: self.coordinator.phase == 'finished'),
            ]
            for intent, ready in stages:
                if cancellation is not None and cancellation.is_set():
                    self._fail('CANCEL_REQUESTED')
                    break
                if not self.coordinator.tick():
                    break
                if not self._send(intent(), deadline) or not self._pump_until(ready, deadline, cancellation=cancellation):
                    self._fail('TURN_NOT_COMPLETED')
                    break
            if self.coordinator.fault:
                self._fail('TURN_INDETERMINATE')
        except Exception:
            self._fail('EXECUTION_UNCONFIRMED')
        finally:
            end = time.monotonic() + cleanup_timeout
            graceful_end = time.monotonic() + cleanup_timeout / 2
            # Request cancellation/close at most once. These are cleanup intents,
            # not completion/OS-stop evidence, and never cause another prompt.
            try:
                if not self._io_ready:
                    raise RuntimeError('IO_NOT_READY')
                c = self.coordinator
                if c.reducer is not None and c.reducer.outcome == 'pending':
                    self._send(c.cancel(), graceful_end)
                if c.session_id is not None:
                    close = c.close_session()
                    if self._send(close, graceful_end):
                        self._pump_until(lambda: c.close_acknowledged, graceful_end, honor_turn=False)
                self._shutdown_issued = True
                if self._send({'id': SHUTDOWN_ID, 'method': 'shim/close', 'params': {}}, graceful_end):
                    self._pump_until(lambda: self.shutdown_ack, graceful_end, honor_turn=False)
                # Observe natural owned-process quiescence before forced cleanup.
                while time.monotonic() < graceful_end:
                    try:
                        while True:
                            self._dispatch(self._frames.get_nowait())
                    except queue.Empty:
                        pass
                    observed = self.transport.observe()
                    if (observed.get('host_exited') is True and observed.get('owned_job_empty') is True
                            and self.eof and self.stderr_eof):
                        code = observed.get('host_exit_code')
                        natural_exit_code = code if type(code) is int else None
                        natural = True
                        break
                    if not self.shutdown_ack:
                        break
                    time.sleep(min(.02, max(0, graceful_end-time.monotonic())))
            except Exception:
                self._fail('CLOSE_UNCONFIRMED')
            self._closing = True
            try:
                self._writes.put_nowait(None)
            except queue.Full:
                pass
            try:
                cleanup = self.transport.close(timeout=max(0, end-time.monotonic()))
            except Exception:
                self._fail('CLEANUP_UNCONFIRMED')
            for record in self._threads:
                thread = record['thread']
                try:
                    thread.join(timeout=max(0, end-time.monotonic()))
                except RuntimeError:
                    pass
            io_stopped = all(r['started'] and not r['thread'].is_alive() for r in self._threads)
        try:
            while True:
                self._dispatch(self._frames.get_nowait())
        except queue.Empty:
            pass
        if self._io_fault:
            self._fail(self._io_fault)
        evidence = self.coordinator.evidence()
        good_cleanup = all(cleanup.get(key) is True for key in
                           ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete')) and io_stopped
        natural_zero = natural and type(natural_exit_code) is int and natural_exit_code == 0
        if not natural_zero or not good_cleanup or not self.shutdown_ack or not self.coordinator.close_acknowledged:
            self._fail('CLEANUP_UNCONFIRMED')
        return {'outcome': evidence['outcome'] if not self.fault else 'indeterminate',
                'fault': self.fault, 'session': evidence, 'launch_attempted': True,
                'cleanup_not_required': False,
                'shutdown_acknowledged': self.shutdown_ack, 'natural_owned_exit': natural,
                'natural_exit_zero': natural_zero,
                'host_exit_code_before_cleanup': natural_exit_code,
                'stdout_eof_observed': self.eof, 'stderr_eof_observed': self.stderr_eof,
                'cleanup': {**cleanup, 'io_threads_stopped': io_stopped},
                'stdout_bytes': self.stdout_bytes, 'stderr_bytes': self.stderr_bytes,
                'liveness': 'unknown', 'retry_safe': False}
