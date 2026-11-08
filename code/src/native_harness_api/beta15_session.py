"""Vendor-free single-session command/evidence coordinator, not a transport.

Every returned command is only an intent. No SDK imports, account operations,
network calls, production admission or process-cleanup claims are made here.
Caller must serialize access and bind responses to this one connection.
"""
from __future__ import annotations
import json
import math
import time
from .beta15_reducer import (
    ContractError, ModelReadback, SessionReducer, check, snapshot, text,
    deny_permission,
)


def decode_frame(raw: bytes, maximum=262144):
    """Decode one bounded complete UTF-8 JSON object, rejecting duplicate keys."""
    check(type(maximum) is int and 0 < maximum <= 4 * 1024 * 1024)
    check(type(raw) is bytes and 0 < len(raw) <= maximum)
    def pairs(items):
        result = {}
        for key, value in items:
            check(key not in result)
            result[key] = value
        return result
    def constant(_value):
        raise ContractError('BETA15_CONTRACT_REJECTED')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                            parse_constant=constant)
        check(type(result) is dict)
        return snapshot(result, maximum)[0]
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ContractError('BETA15_CONTRACT_REJECTED') from None


class SessionCoordinator:
    """One new-session/model-selection/prompt attempt with correlated replies.

    This in-memory object cannot authorize execution or survive restart. A future
    transport must persist its execution intent before sending any command, enforce
    native readiness/authorization, and retain process ownership independently.
    """
    def __init__(self, *, deadline, clock=time.monotonic, on_event=None):
        check(type(deadline) in (int, float) and math.isfinite(deadline))
        check(callable(clock))
        check(on_event is None or callable(on_event))
        self._on_event = on_event
        self.deadline, self.clock = deadline, clock
        self.phase = 'new'
        self.session_id = None
        self.model = None
        self.reducer = None
        self.fault = None
        self._next_id = 1
        self._pending = {}
        self._last_now = None
        self._close_issued = False
        self.close_acknowledged = False
        self._cancel_issued = False
        self.prompt_intent_issued = False

    def _emit_event(self, event):
        # Caller supplies only a bounded nonblocking enqueue. Sample the actual
        # coordinator clock after it, not the reducer's cached dispatch sample.
        try:
            return self._on_event(event)
        finally:
            self.tick()

    def _fail(self, reason):
        if self.fault is None:
            self.fault = reason
        self.phase = 'indeterminate'

    def tick(self):
        try:
            now = self.clock()
        except Exception:
            self._fail('INVALID_CLOCK')
            if self.reducer is not None:
                self.reducer.disconnect()
            return False
        if (type(now) not in (int, float) or not math.isfinite(now) or
                self._last_now is not None and now < self._last_now):
            self._fail('INVALID_CLOCK')
        else:
            self._last_now = now
            if now >= self.deadline and self.phase not in ('finished', 'closed'):
                self._fail('TIMEOUT')
        if self.reducer is not None:
            self.reducer.tick(now)
        return not self.fault

    def _request(self, method, params):
        number = self._next_id
        self._next_id += 1
        self._pending[number] = method
        return {'id': number, 'method': method, 'params': snapshot(params)[0]}

    def new_session(self, cwd):
        try:
            check(self.tick() and self.phase == 'new')
            # Absolute-path/empty-workspace validation belongs to the eventual OS
            # owner. Here the protocol string is bounded and not normalized.
            cwd = text(cwd, 4096)
            self.phase = 'creating'
            return self._request('session/new', {'cwd': cwd, 'mcpServers': []})
        except ContractError:
            self._fail('INVALID_NEW_INTENT')
            raise

    def select_model(self, model_id):
        try:
            check(self.tick() and self.phase == 'created' and not self._close_issued)
            params = self.model.select_intent(model_id)
            self.phase = 'selecting'
            return self._request('session/set_config_option',
                                 {'sessionId': self.session_id, **params})
        except ContractError:
            self._fail('INVALID_SELECTION_INTENT')
            raise

    def prompt(self, value):
        try:
            check(self.tick() and self.phase == 'selected' and self.model.confirmed
                  and not self.prompt_intent_issued and not self._close_issued)
            value = text(value, 131072)
            check(value.strip() != '')
            # Bind the reducer before emitting the command: native events may
            # arrive before the insertion-acknowledgement RPC response.
            self.reducer = SessionReducer(self.session_id, deadline=self.deadline,
                                          # Use the validated sample for this
                                          # serialized entry; do not call an
                                          # arbitrary clock twice mid-dispatch.
                                          clock=lambda: self._last_now, on_event=self._emit_event if self._on_event is not None else None)
            self.prompt_intent_issued = True
            self.phase = 'running'
            return self._request('session/prompt', {'sessionId': self.session_id,
                    'prompt': [{'type': 'text', 'text': value}]})
        except ContractError:
            self._fail('INVALID_PROMPT_INTENT')
            raise

    def response(self, envelope):
        """One already-correlated connection's RPC result, no raw error export."""
        try:
            envelope = snapshot(envelope)[0]
            check(type(envelope) is dict and type(envelope.get('id')) is int)
            method = self._pending.pop(envelope['id'], None)
            check(method is not None)
            check(set(envelope) <= {'id', 'result', 'error', 'jsonrpc'})
            check('jsonrpc' not in envelope or envelope['jsonrpc'] == '2.0')
            check('result' in envelope and 'error' not in envelope)
            result = envelope['result']
            check(type(result) is dict)
            # Cleanup replies remain recordable after failure, but never clear it.
            if method == 'session/close':
                check(set(result) <= {'_meta'})
                check('_meta' not in result or result['_meta'] is None or type(result['_meta']) is dict)
                self.close_acknowledged = True
                if not self.fault and self.phase == 'finished':
                    self.phase = 'closed'
                return
            check(self.tick())
            if method == 'session/new':
                check(self.phase == 'creating')
                self.session_id = text(result.get('sessionId'))
                self.model = ModelReadback(result.get('configOptions'))
                self.phase = 'created'
            elif method == 'session/set_config_option':
                check(self.phase == 'selecting')
                self.model.selection_response(result)
                self.phase = 'selected'
            elif method == 'session/prompt':
                check(self.phase == 'running')
                self.reducer.acknowledge(result)
                self._observe()
        except ContractError:
            self._fail('INVALID_RESPONSE')

    def _observe(self):
        if self.reducer is None:
            return
        if self.reducer.fault:
            self._fail('TURN_INDETERMINATE')
        elif self.reducer.outcome != 'pending' and not self.fault:
            self.phase = 'closed' if self.close_acknowledged else 'finished'

    def update(self, envelope):
        if not self.tick():
            return
        try:
            envelope = snapshot(envelope)[0]
            check(type(envelope) is dict and type(envelope.get('sessionId')) is str)
            # Before a new-session response there is no accepted binding. Ignore
            # notifications rather than attaching them to a guessed session.
            if self.session_id is None or envelope['sessionId'] != self.session_id:
                return
            update = envelope.get('update')
            check(type(update) is dict)
            if update.get('sessionUpdate') == 'config_option_update':
                self.model.config_update(update)
                if self.model.invalidated:
                    self._fail('MODEL_EVIDENCE_LOST')
            elif self.reducer is not None:
                self.reducer.update(envelope)
                self._observe()
        except ContractError:
            self._fail('INVALID_UPDATE')

    def permission(self, _request):
        return deny_permission(_request)

    def cancel(self):
        check(self.reducer is not None and not self._cancel_issued)
        self._cancel_issued = True
        return {'method': 'session/cancel', 'params': self.reducer.cancel_notification()}

    def close_session(self):
        check(self.session_id is not None and not self._close_issued)
        self._close_issued = True
        return self._request('session/close', {'sessionId': self.session_id})

    def disconnect(self):
        if self.reducer is not None:
            self.reducer.disconnect()
        # Even a terminal turn does not prove the session/process cleanup occurred.
        self._fail('DISCONNECTED')

    def evidence(self):
        turn = None if self.reducer is None else self.reducer.evidence()
        outcome = 'indeterminate' if self.fault else 'pending' if turn is None else turn['outcome']
        return {'outcome': outcome, 'phase': self.phase, 'fault': self.fault,
                'turn': turn, 'model_confirmed': bool(self.model and self.model.confirmed and not self.fault),
                'prompt_intent_issued': self.prompt_intent_issued,
                'session_close_acknowledged': self.close_acknowledged,
                'liveness': 'unknown', 'retry_safe': False, 'usage': {'state': 'unknown'}}
