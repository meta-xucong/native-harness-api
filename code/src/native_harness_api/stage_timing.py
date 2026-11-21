"""Opt-in, local timings for a newly accepted task."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import sys
from threading import Lock
import time


_task_id = ContextVar('timing_task_id', default=None)
_output_lock = Lock()


@contextmanager
def trace_task(task_id, *, enabled):
    token = _task_id.set(task_id if enabled else None)
    try:
        yield
    finally:
        _task_id.reset(token)


def _emit(task_id, stage, event, **fields):
    try:
        record = {'type': 'native_stage_timing', 'task_id': task_id, 'stage': stage,
                  'event': event, 'time_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                  **fields}
        line = json.dumps(record, separators=(',', ':')) + '\n'
        with _output_lock:
            sys.stderr.write(line)
            sys.stderr.flush()
    except (AttributeError, OSError, ValueError):
        # Diagnostics must not affect the execution or its recorded outcome.
        pass


@contextmanager
def timed_stage(stage, **fields):
    task_id = _task_id.get()
    if task_id is None:
        yield
        return

    _emit(task_id, stage, 'begin', **fields)
    started = time.monotonic_ns()
    raised = True
    try:
        yield
        raised = False
    finally:
        elapsed_ms = round((time.monotonic_ns() - started) / 1_000_000, 3)
        _emit(task_id, stage, 'end', elapsed_ms=elapsed_ms, raised=raised, **fields)
