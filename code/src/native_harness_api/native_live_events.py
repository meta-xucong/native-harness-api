"""Bounded, ordered native progress delivery, never terminal completion evidence.

The reducer calls enqueue without network I/O. One worker publishes in order;
only a durable Bridge acknowledgement accepts an event. No retries or inference
restarts occur. Delivery failure makes the attempt uncertain even after native
cleanup. Callers retain unfinished workers and never release that attempt slot.
"""
import json
import queue
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
    """Single producer and publisher worker; enqueue and finish waits are bounded."""
    def __init__(self, publish):
        if not callable(publish):
            raise ValueError('LIVE_PUBLISHER_REQUIRED')
        self.publish = publish
        self.queue = queue.Queue(maxsize=64)
        self.failed = threading.Event()
        self.closed = threading.Event()
        self.count = self.bytes = self.accepted = 0
        self.thread = threading.Thread(target=self._work, daemon=True, name='native-live-events')
        self.thread.start()

    def enqueue(self, event):
        if self.failed.is_set() or self.closed.is_set():
            return False
        try:
            event, size = checked_event(event)
            if self.count >= MAX_EVENTS or self.bytes + size > MAX_BYTES:
                raise ValueError()
            seq = self.count + 1
            self.queue.put_nowait((seq, event))
            self.count, self.bytes = seq, self.bytes + size
            return True
        except Exception:
            self.failed.set()
            return False

    def _work(self):
        try:
            while not self.failed.is_set():
                try:
                    seq, event = self.queue.get(timeout=.02)
                except queue.Empty:
                    if self.closed.is_set() and self.queue.empty():
                        return
                    continue
                if self.publish(seq, event) is not True:
                    raise ValueError()
                self.accepted = seq
        except Exception:
            self.failed.set()

    def finish(self, timeout=6):
        self.closed.set()
        self.thread.join(timeout)
        if self.thread.is_alive() or self.accepted != self.count:
            self.failed.set()
        return not self.failed.is_set()
