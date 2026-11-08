"""Bounded, ordered native progress delivery, never terminal completion evidence.

The reducer calls enqueue without network I/O. One worker publishes in order;
only a durable Bridge acknowledgement accepts an event. No retries or inference
restarts occur. Delivery failure makes the attempt uncertain even after native
cleanup. Callers retain unfinished workers and never release that attempt slot.
"""
import json
from collections import deque
import math
import time
import threading

MAX_EVENTS = 512
MAX_BYTES = 16 * 1024 * 1024
MAX_EVENT_BYTES = 6 * 262144 + 4096


def checked_event(event):
    if type(event) is not dict:
        raise ValueError('LIVE_EVENT_INVALID')
    if event.get('type') == 'text_snapshot':
        if set(event) != {'type', 'text'} or type(event['text']) is not str or len(event['text'].encode('utf8')) > 262144:
            raise ValueError('LIVE_EVENT_INVALID')
    elif event.get('type') == 'native_status':
        if (not {'type', 'state'} <= set(event) <= {'type', 'state', 'stop_reason'} or
                event['state'] not in ('running', 'requires_action', 'idle', 'unknown') or
                ('stop_reason' in event and (event['state'] != 'idle' or event['stop_reason'] not in
                 ('end_turn', 'refusal', 'cancelled', '_codebuddy.ai/error')))):
            raise ValueError('LIVE_EVENT_INVALID')
    else:
        raise ValueError('LIVE_EVENT_INVALID')
    raw = json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > MAX_EVENT_BYTES:
        raise ValueError('LIVE_EVENT_LIMIT')
    return json.loads(raw), len(raw)


class LiveEventPump:
    """Bounded latest-snapshot coalescing; status events are ordering barriers.

    Sequence numbers and quota are assigned only when a worker takes an event for
    publication. An unsent consecutive snapshot may be replaced, never a status
    or an event already sent. Native state/final close flush preceding text.
    """
    def __init__(self, publish, *, horizon=120):
        if not callable(publish):
            raise ValueError('LIVE_PUBLISHER_REQUIRED')
        if type(horizon) not in (int, float) or not math.isfinite(horizon) or not 0 < horizon <= 3600:
            raise ValueError('LIVE_HORIZON_INVALID')
        self.publish = publish
        self.queue = deque()
        self.condition = threading.Condition()
        self.failed = threading.Event()
        self.closed = threading.Event()
        self.count = self.bytes = self.accepted = self.received = self.coalesced = 0
        # Reserve capacity for boundary/final snapshots and finite state updates.
        # This is pacing, not permission to exceed the hard publication quotas.
        self.byte_rate = (MAX_BYTES - 2 * MAX_EVENT_BYTES - 65536) / horizon
        self.event_rate = (MAX_EVENTS - 66) / horizon
        self.next_snapshot = time.monotonic() + .1
        self.thread = threading.Thread(target=self._work, daemon=True, name='native-live-events')
        self.thread.start()

    def enqueue(self, event):
        try:
            event, size = checked_event(event)
            with self.condition:
                if self.failed.is_set() or self.closed.is_set():
                    return False
                if self.received >= 4096 or self.count >= MAX_EVENTS or self.bytes >= MAX_BYTES:
                    raise ValueError()
                if self.queue and event['type'] == self.queue[-1][0]['type'] == 'text_snapshot':
                    self.queue[-1] = (event, size)
                    self.coalesced += 1
                else:
                    if len(self.queue) >= 64:
                        raise ValueError()
                    self.queue.append((event, size))
                self.received += 1
                self.condition.notify_all()
                return True
        except Exception:
            self.failed.set()
            with self.condition:
                self.condition.notify_all()
            return False

    def _work(self):
        try:
            while True:
                with self.condition:
                    while True:
                        if self.failed.is_set():
                            return
                        if not self.queue:
                            if self.closed.is_set():
                                return
                            self.condition.wait(.02)
                            continue
                        event, size = self.queue[0]
                        delay = self.next_snapshot - time.monotonic()
                        # A state barrier or final flush must not overtake text.
                        if (event['type'] == 'text_snapshot' and len(self.queue) == 1
                                and not self.closed.is_set() and delay > 0):
                            self.condition.wait(min(delay, .1))
                            continue
                        if self.count >= MAX_EVENTS or self.bytes + size > MAX_BYTES:
                            raise ValueError()
                        self.queue.popleft()
                        self.count += 1
                        self.bytes += size
                        seq = self.count
                        break
                if self.publish(seq, event) is not True:
                    raise ValueError()
                self.accepted = seq
                if event['type'] == 'text_snapshot':
                    self.next_snapshot = time.monotonic() + min(1., max(.1, size / self.byte_rate, 1 / self.event_rate))
        except Exception:
            self.failed.set()

    def finish(self, timeout=6):
        with self.condition:
            self.closed.set()
            self.condition.notify_all()
        self.thread.join(timeout)
        if self.thread.is_alive() or self.accepted != self.count or self.queue:
            self.failed.set()
        return not self.failed.is_set()
