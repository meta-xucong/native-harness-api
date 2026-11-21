"""One bounded, read-only observation before any forced-close operation.

A deadline ends waiting, not the ability to observe a process already stopped.
This probe consumes only remaining total cleanup budget, never sleeps, kills,
closes, clears a fault or derives natural exit from later cleanup results.
"""
import queue
import time


def pre_force_exit_probe(engine, deadline, *, require_stdin=False, clock=time.monotonic):
    report = {'phase': 'pre_force', 'attempted': False, 'observation_failed': False,
              'budget_expired': False, 'host_exited': None, 'owned_job_empty': None,
              'exit_code_known': False, 'stdout_eof': False, 'stderr_eof': False,
              'stdin_eof': None, 'frames_drained': 0}
    result = {'natural': False, 'exit_code': None, 'diagnostic': report}
    if engine._closing or not engine._io_ready:
        return result
    try:
        def available():
            if clock() >= deadline:
                report['budget_expired'] = True
                return False
            return not engine._closing
        def drain():
            # Capture a finite batch, not an unbounded drain racing a producer.
            for _ in range(min(512, engine._frames.qsize())):
                if not available():
                    return
                try:
                    raw = engine._frames.get_nowait()
                except queue.Empty:
                    return
                engine._dispatch(raw)
                report['frames_drained'] += 1
        if not available():
            return result
        report['attempted'] = True
        drain()
        if not available():
            return result
        observed = engine.transport.observe()
        if (observed.get('code') not in (None, 'OBSERVED')
                or any(observed.get(key) is not None and observed.get(key) is not False
                       for key in ('handles_closed', 'cleanup_complete'))):
            report['observation_failed'] = True
            return result
        drain()  # EOF may have reached the bounded queue during the OS query.
        if not available():
            return result
        report['host_exited'] = observed.get('host_exited') if type(observed.get('host_exited')) is bool else None
        report['owned_job_empty'] = observed.get('owned_job_empty') if type(observed.get('owned_job_empty')) is bool else None
        code = observed.get('host_exit_code')
        report['exit_code_known'] = type(code) is int
        report['stdout_eof'] = engine.eof is True
        report['stderr_eof'] = engine.stderr_eof is True
        report['stdin_eof'] = (engine.stdin_eof_sent is True) if require_stdin else None
        result['natural'] = (report['host_exited'] is True and report['owned_job_empty'] is True
                             and report['stdout_eof'] and report['stderr_eof']
                             and (not require_stdin or report['stdin_eof']))
        if result['natural']:
            result['exit_code'] = code if type(code) is int else None
        return result
    except Exception:
        report['observation_failed'] = True
        return result
