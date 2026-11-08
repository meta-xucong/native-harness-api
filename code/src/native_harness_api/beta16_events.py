"""Bounded beta16 notification projection; no I/O, SDK import or admission.

Pinned sources (data-only inspection): public @tencent-ai/agent-harness
0.2.0-beta.16 docs/acp-v2.md SHA256
97c612e2682ab1ed235ec3797f8bbab7ccd8f9f918a6204e34b8ca75d1fec9c2;
lib/session/server-events.js SHA256
 def14a9f6069bf3d46a80ce9c6463213c759d3f52659c9fe53135ba415c9d8df;
SDK 1.7.0 schema/v2/schema.unstable.json SHA256
75b2aa359dd26cd9d0468674be96482b14a2d181bc8e3ea8c91a8e598f63e3b5.
The public package pins SDK 1.7.0; the inspected schema is the same-version
physical schema in the pinned official desktop archive, not an executed validator.

UsageUpdate/Cost and the beta16 credits extension allow session-level usage
without requestId. Such updates must not be assigned to a turn or treated as
completion. This narrow projection drops only validated usage updates. Every
other event is copied unchanged for the existing coordinator/reducer, which MUST
still validate session/run correlation, model evidence and terminal semantics.
No requestId is fabricated. Unknown usage remains unknown. Other beta16 events
may still be unsupported: this is not a general beta16 compatibility claim.
"""
from __future__ import annotations
import json
import math
import re


class ProjectionError(ValueError):
    """Finite reason only; never includes notification contents."""


class Beta16EventProjector:
    """Single-stream, serialized, bounded projection of decoded notification params.

    The caller owns byte framing and duplicate-key rejection before decoding.
    Dropped/foreign events still consume the same cumulative budgets. Recreating
    this object does not authorize reconnect, replay, another prompt or slot release.
    """
    def __init__(self, *, max_events=512, max_bytes=4 * 1024 * 1024,
                 max_frame_bytes=262144):
        for value, limit in ((max_events, 4096), (max_bytes, 4 * 1024 * 1024),
                             (max_frame_bytes, 262144)):
            if type(value) is not int or not 0 < value <= limit:
                raise ValueError('BETA16_EVENT_BUDGET_INVALID')
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.max_frame_bytes = max_frame_bytes
        self.events_received = self.bytes_received = self.usage_dropped = 0
        self.fault = None

    def _fail(self, code):
        if self.fault is None:
            self.fault = code
        raise ProjectionError(self.fault)

    def _copy(self, value):
        nodes = 0
        def validate(item, depth):
            nonlocal nodes
            nodes += 1
            if nodes > 20000 or depth > 64:
                raise ValueError()
            kind = type(item)
            if item is None or kind in (str, bool, int):
                return
            if kind is float:
                if not math.isfinite(item):
                    raise ValueError()
                return
            if kind is dict:
                if len(item) > 20000 or any(type(key) is not str for key in item):
                    raise ValueError()
                for child in item.values():
                    validate(child, depth + 1)
                return
            if kind is list:
                if len(item) > 20000:
                    raise ValueError()
                for child in item:
                    validate(child, depth + 1)
                return
            raise ValueError()
        validate(value, 0)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
        if len(encoded) > self.max_frame_bytes:
            self._fail('BETA16_EVENT_LIMIT')
        return json.loads(encoded), len(encoded)

    @staticmethod
    def _meta(value):
        return '_meta' not in value or value['_meta'] is None or type(value['_meta']) is dict

    @classmethod
    def _usage(cls, envelope, update):
        # Narrow projection rejects unknown siblings rather than accidentally
        # dropping an embedded result/terminal. Arbitrary bounded metadata remains
        # opaque, as mandated by ACP; none of it becomes identity or accounting.
        if (set(envelope) - {'sessionId', 'update', '_meta'} or not cls._meta(envelope)
                or set(update) - {'sessionUpdate', 'used', 'size', 'cost', '_meta'}
                or not cls._meta(update)):
            return False
        if not all(type(update.get(key)) is int and 0 <= update[key] <= 2**64 - 1
                   for key in ('used', 'size')):
            return False
        cost = update.get('cost')
        if cost is None:
            return True
        if (type(cost) is not dict or set(cost) - {'amount', 'currency', '_meta'}
                or not cls._meta(cost) or type(cost.get('amount')) not in (int, float)):
            return False
        amount, currency = cost['amount'], cost.get('currency')
        try:
            finite = math.isfinite(amount)
        except OverflowError:
            return False
        if not finite or type(currency) is not str:
            return False
        if currency == 'credits':
            return amount >= 0
        return re.fullmatch(r'[A-Z]{3}', currency) is not None

    def project(self, envelope):
        """Return an immutable-by-copy event, or None only for valid usage."""
        if self.fault is not None:
            raise ProjectionError(self.fault)
        self.events_received += 1
        if self.events_received > self.max_events:
            self._fail('BETA16_EVENT_LIMIT')
        try:
            event, size = self._copy(envelope)
            self.bytes_received += size
            if self.bytes_received > self.max_bytes:
                self._fail('BETA16_EVENT_LIMIT')
            if (type(event) is not dict or type(event.get('sessionId')) is not str
                    or not 0 < len(event['sessionId']) <= 512
                    or type(event.get('update')) is not dict
                    or type(event['update'].get('sessionUpdate')) is not str):
                self._fail('BETA16_EVENT_INVALID')
            update = event['update']
            if update['sessionUpdate'] == 'usage_update':
                if not self._usage(event, update):
                    self._fail('BETA16_EVENT_INVALID')
                self.usage_dropped += 1
                return None
            return event
        except ProjectionError:
            raise
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            self._fail('BETA16_EVENT_INVALID')

    def summary(self):
        return {'events_received': self.events_received, 'bytes_received': self.bytes_received,
                'usage_dropped': self.usage_dropped, 'fault': self.fault,
                'usage': {'state': 'unknown'}, 'native_account_verified': False,
                'real_model_ready': False, 'retry_safe': False}
