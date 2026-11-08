"""Single-attempt owned public SFE stdio session, with real EOF lifecycle.

Caller supplies verified launch assets, authorized local account/profile and an
owned transport. No login, discovery, retry, shim ACK or credential handling.
Synthetic tests cannot establish vendor/account/model readiness.
"""
from __future__ import annotations
import math
import queue
import threading
import time
from .public_sfe_session import PublicSfeSession


class PublicSfeOwnedSession:
    MAX_FRAME = 262144
    MAX_OUTPUT = 4 * 1024 * 1024
    MAX_STDERR = 65536

    def __init__(self, transport):
        self.transport = transport
        self._attempted = self._closing = self._io_ready = False
        self._io_fault = self.fault = None
        self._threads = []
        self._frames = queue.Queue(maxsize=512)
        self._writes = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self.stdout_bytes = self.stderr_bytes = 0
        self.eof = self.stderr_eof = self.stdin_eof_sent = False
        self.protocol = None
        self._send_deadline = 0

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
                    stream.close()
                    self.stdin_eof_sent = True
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

    def _send(self, frames, deadline):
        try:
            for frame in frames:
                if (type(frame) is not bytes or not frame.endswith(b'\n') or
                        len(frame) > self.MAX_FRAME + 1 or b'\n' in frame[:-1]):
                    raise ValueError()
                if self._closing or self.stdin_eof_sent or self._io_fault or time.monotonic() >= deadline:
                    raise ValueError()
                complete = queue.Queue(maxsize=1)
                self._writes.put_nowait((frame, deadline, complete))
                if complete.get(timeout=max(0, deadline-time.monotonic())) is not True:
                    raise ValueError()
            return True
        except (ValueError, queue.Empty, queue.Full):
            self._fail('SEND_UNCONFIRMED')
            return False

    def _dispatch(self, raw):
        if raw is None:
            self.eof = True
            return
        try:
            frames = self.protocol.receive(raw)
            if frames:
                self._send(frames, self._send_deadline)
        except Exception:
            self._fail('INVALID_FRAME')

    def _pump_until(self, predicate, deadline, *, honor_turn=True, cancellation=None):
        while not predicate():
            if self._io_fault:
                self._fail(self._io_fault)
                return False
            if honor_turn and (self.fault or not self.protocol.tick() or
                    cancellation is not None and cancellation.is_set()):
                return False
            if self.eof or time.monotonic() >= deadline:
                return False
            try:
                self._dispatch(self._frames.get(timeout=min(.02, max(0, deadline-time.monotonic()))))
            except queue.Empty:
                pass
        return True

    def _stages(self, workspace, model_id, prompt):
        """Internal extension seam; default execution contract remains unchanged."""
        return [
            (self.protocol.start, lambda: self.protocol.ready),
            (lambda: self.protocol.new_session(workspace), lambda: self.protocol.coordinator.phase == 'created'),
            (lambda: self.protocol.select_model(model_id), lambda: self.protocol.coordinator.phase == 'selected'),
            (lambda: self.protocol.prompt(prompt), lambda: self.protocol.coordinator.phase == 'finished'),
        ]

    def run(self, command, cwd, env, *, workspace, model_id, prompt,
            expected_account_digest, timeout=120, cleanup_timeout=10, cancellation=None):
        if self._attempted:
            raise ValueError('SINGLE_ATTEMPT_ONLY')
        self._attempted = True
        for value, maximum in ((timeout, 3600), (cleanup_timeout, 300)):
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError('FINITE_TIMEOUT_REQUIRED')
        if cancellation is not None and not isinstance(cancellation, threading.Event):
            raise ValueError('THREADING_EVENT_REQUIRED')
        deadline = time.monotonic() + timeout
        self.protocol = PublicSfeSession(deadline=deadline, expected_account_digest=expected_account_digest)
        if cancellation is not None and cancellation.is_set():
            return {'outcome': 'cancelled', 'fault': None, 'session': self.protocol.evidence(),
                    'launch_attempted': False, 'cleanup_not_required': True,
                    'cleanup_confirmed': False, 'natural_owned_exit': False,
                    'natural_exit_zero': False, 'stdin_eof_sent': False,
                    'stdout_eof_observed': False, 'stderr_eof_observed': False,
                    'host_exit_code_before_cleanup': None, 'cleanup': {},
                    'liveness': 'not_started', 'retry_safe': False}
        cleanup = {}
        natural = False
        natural_code = None
        io_stopped = False
        self._send_deadline = deadline
        try:
            self.transport.start(command, cwd, env)
            self._start_thread(self._read, self.transport.stdout)
            self._start_thread(self._read, self.transport.stderr, True)
            self._start_thread(self._write, self.transport.stdin)
            self._io_ready = True
            stages = self._stages(workspace, model_id, prompt)
            for intent, ready in stages:
                if cancellation is not None and cancellation.is_set():
                    self._fail('CANCEL_REQUESTED')
                    break
                if not self.protocol.tick() or not self._send(intent(), deadline) or not self._pump_until(ready, deadline, cancellation=cancellation):
                    self._fail('TURN_NOT_COMPLETED')
                    break
        except Exception:
            self._fail('EXECUTION_UNCONFIRMED')
        finally:
            end = time.monotonic() + cleanup_timeout
            graceful_end = time.monotonic() + cleanup_timeout / 2
            self._send_deadline = graceful_end
            try:
                if self._io_ready:
                    coordinator = self.protocol.coordinator
                    if coordinator.reducer is not None and not coordinator.reducer.evidence()['terminal_observed']:
                        self._send(self.protocol.cancel(), graceful_end)
                    if coordinator.session_id is not None:
                        if self._send(self.protocol.close_session(), graceful_end):
                            self._pump_until(lambda: coordinator.close_acknowledged, graceful_end, honor_turn=False)
                    # Writer owns stdin close, avoiding a blocking close on this
                    # orchestration thread. FIFO ensures no command follows EOF.
                    self._writes.put_nowait(None)
                    while time.monotonic() < graceful_end:
                        try:
                            while True:
                                self._dispatch(self._frames.get_nowait())
                        except queue.Empty:
                            pass
                        observed = self.transport.observe()
                        if (observed.get('host_exited') is True and observed.get('owned_job_empty') is True
                                and self.eof and self.stderr_eof and self.stdin_eof_sent):
                            code = observed.get('host_exit_code')
                            natural_code = code if type(code) is int else None
                            natural = True
                            break
                        time.sleep(min(.01, max(0, graceful_end-time.monotonic())))
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
                try:
                    record['thread'].join(timeout=max(0, end-time.monotonic()))
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
        evidence = self.protocol.evidence()
        zero = natural and type(natural_code) is int and natural_code == 0
        cleanup_confirmed = zero and self.eof and self.stderr_eof and self.stdin_eof_sent and io_stopped and all(
            cleanup.get(key) is True for key in ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete'))
        if not cleanup_confirmed:
            self._fail('CLEANUP_UNCONFIRMED')
        if self.protocol.coordinator.session_id is not None and not self.protocol.coordinator.close_acknowledged:
            self._fail('SESSION_CLOSE_UNCONFIRMED')
        return {'outcome': evidence['outcome'] if not self.fault else 'indeterminate',
                'fault': self.fault, 'session': evidence, 'launch_attempted': True,
                'cleanup_not_required': False, 'cleanup_confirmed': cleanup_confirmed,
                'natural_owned_exit': natural, 'natural_exit_zero': zero,
                'host_exit_code_before_cleanup': natural_code, 'stdin_eof_sent': self.stdin_eof_sent,
                'stdout_eof_observed': self.eof, 'stderr_eof_observed': self.stderr_eof,
                'cleanup': {**cleanup, 'io_threads_stopped': io_stopped},
                'stdout_bytes': self.stdout_bytes, 'stderr_bytes': self.stderr_bytes,
                'liveness': 'unknown', 'retry_safe': False}
