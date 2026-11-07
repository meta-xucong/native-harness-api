"""Independent Windows owned-process stdio transport; no ACP or task semantics."""
import math
from pathlib import Path
import threading

from .windows_job import OwnedWindowsJob, UnsupportedPlatform, WindowsJobError


class TransportError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class SupervisedACPTransport:
    """Single launch attempt. A failed owner remains held for explicit cleanup retry.

    This is not a Popen substitute. Readers/writers must bound their own protocol
    IO and stop using streams before close. No scheduler, authentication or PID
    reattachment is provided. No fact returned here means stopped_confirmed.
    """
    def __init__(self):
        self._owner = None
        self._started = False
        self._attempted = False
        self._sealed = False
        self._cleanup_complete = False
        self._closed_facts = None
        self._lock = threading.RLock()

    def start(self, command, cwd, env):
        with self._lock:
            if self._attempted or self._sealed:
                raise TransportError('TRANSPORT_START_ALREADY_CONSUMED')
            self._attempted = True  # Before validation/OS launch, never reset.
            try:
                self._owner = OwnedWindowsJob.spawn(list(command), Path(cwd), dict(env))
            except UnsupportedPlatform:
                raise TransportError('WINDOWS_REQUIRED') from None
            except WindowsJobError as exc:
                self._owner = exc.owner
                raise TransportError('TRANSPORT_START_FAILED') from None
            except Exception:
                raise TransportError('TRANSPORT_START_FAILED') from None
            self._started = True
            return {'pid': self.pid, 'host_started': True}

    @property
    def pid(self):
        return self._owner.pid if self._owner is not None else None

    def _stream(self, name):
        with self._lock:
            if not self._started or self._owner is None or self._sealed:
                raise TransportError('TRANSPORT_STREAM_UNAVAILABLE')
            stream = getattr(self._owner, name)
            if stream is None:
                raise TransportError('TRANSPORT_STREAM_UNAVAILABLE')
            return stream

    @property
    def stdin(self):
        return self._stream('stdin')

    @property
    def stdout(self):
        return self._stream('stdout')

    @property
    def stderr(self):
        return self._stream('stderr')

    def observe(self):
        with self._lock:
            if self._closed_facts is not None:
                return dict(self._closed_facts)
            facts = {'host_exited': None, 'owned_job_empty': None,
                     'handles_closed': self._cleanup_complete, 'cleanup_complete': self._cleanup_complete, 'code': 'UNOBSERVED'}
            if self._owner is None:
                return facts
            try:
                facts['host_exited'] = self._owner.host_exited()
                facts['owned_job_empty'] = self._owner.wait_empty(0)['owned_job_empty']
                facts['code'] = 'OBSERVED'
            except Exception:
                # Partial successful observation is not a cleanup/stop guarantee.
                facts['code'] = 'OBSERVATION_FAILED'
            return facts

    def close(self, timeout=5):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not math.isfinite(timeout) or not 0 <= timeout <= 300):
            raise ValueError('Finite timeout in 0..300 required')
        with self._lock:
            self._sealed = True
            if self._closed_facts is not None:
                return dict(self._closed_facts)
            if self._owner is None:
                # No returned owner is not evidence about a failed launch.
                return {'host_exited': None, 'owned_job_empty': None,
                        'handles_closed': False, 'cleanup_complete': False, 'code': 'OWNER_UNAVAILABLE'}
            try:
                result = self._owner.close(timeout=timeout)
            except Exception:
                return {'host_exited': None, 'owned_job_empty': None,
                        'handles_closed': False, 'cleanup_complete': False, 'code': 'CLEANUP_UNCONFIRMED'}
            self._cleanup_complete = True
            self._closed_facts = {'host_exited': True,
                'owned_job_empty': result['owned_job_empty'],
                'handles_closed': True, 'cleanup_complete': True, 'code': 'CLOSED'}
            return dict(self._closed_facts)
