"""Vendor-free, unconnected beta.15 session contract reducer.

No I/O, SDK imports or production routing. Inputs are already decoded protocol
objects; transport must reject duplicate JSON keys before calling this module.
This is observation evidence, not permission to execute or release an owned slot.
"""
from __future__ import annotations
import json
import inspect
import math
import re
import time

REQUEST_ID = 'codebuddy.ai/requestId'
ERROR_UPDATE = '_codebuddy.ai/error'
SUBAGENT = 'codebuddy.ai/subagent'
ROOT_ID = 'codebuddy.ai/rootRequestId'

class ContractError(ValueError):
    pass


def check(ok):
    if not ok: raise ContractError('BETA15_CONTRACT_REJECTED')


def text(value, maximum=512):
    check(type(value) is str and 0<len(value)<=maximum)
    check(not any(0xD800<=ord(c)<=0xDFFF for c in value))
    return value


def snapshot(value, maximum=262144):
    try:
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf8')
        check(len(raw)<=maximum)
        return json.loads(raw),len(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError): raise ContractError('BETA15_CONTRACT_REJECTED') from None


class ModelReadback:
    """One explicit selection intent, admitted catalog and returned readback only."""
    def __init__(self, config_options):
        self.requested=None; self.confirmed=False; self.invalidated=False; self._response_consumed=False
        self._apply(config_options)

    def _apply(self, options):
        options,_=snapshot(options)
        check(type(options) is list and len(options)<=128)
        models=[o for o in options if type(o) is dict and o.get('configId')=='model']
        check(len(models)==1)
        model=models[0]; check(model.get('type')=='select')
        values=model.get('options'); check(type(values) is list and 0<len(values)<=1024)
        grouped=all(type(v) is dict and 'groupId' in v for v in values)
        flat=all(type(v) is dict and 'value' in v and 'groupId' not in v for v in values)
        check(grouped or flat)
        choices=[]
        if grouped:
            groups=set()
            for group in values:
                gid=text(group['groupId']);check(gid not in groups);groups.add(gid);text(group.get('name'))
                inner=group.get('options');check(type(inner) is list and len(inner)<=1024)
                choices.extend(inner)
        else: choices=values
        check(0<len(choices)<=1024)
        admitted=set()
        for choice in choices:
            check(type(choice) is dict and 'groupId' not in choice)
            value=text(choice.get('value'));text(choice.get('name'));check(value not in admitted);admitted.add(value)
        current=text(model.get('currentValue'));check(current in admitted)
        self.admitted=frozenset(admitted);self.current=current

    def _invalidate(self):
        self.invalidated=True
        self.confirmed=False

    def select_intent(self, model_id):
        try:
            check(not self.invalidated)
            model_id=text(model_id)
            check(self.requested is None and model_id in self.admitted)
            self.requested=model_id;self.confirmed=False
            return {'configId':'model','type':'id','value':model_id}
        except ContractError:
            self._invalidate();raise

    def selection_response(self, response):
        # Clear positive evidence before even envelope/order validation. A duplicate
        # or malformed response must not leave an earlier true flag accessible.
        self.confirmed=False
        try:
            check(not self.invalidated and self.requested is not None and
                  not self._response_consumed and type(response) is dict)
            self._response_consumed=True
            self._apply(response.get('configOptions'))
            check(self.current==self.requested)
            self.confirmed=True
        except ContractError:
            self._invalidate();raise

    def config_update(self, update):
        was_confirmed=self.confirmed
        self.confirmed=False
        if self.invalidated:return
        try:
            check(type(update) is dict and update.get('sessionUpdate')=='config_option_update')
            self._apply(update.get('configOptions'))
            if was_confirmed and self.current!=self.requested:
                self._invalidate()
            else:
                # Before the one correlated response, updates cannot confirm. After
                # invalidation, later matching snapshots cannot revive old evidence.
                self.confirmed=was_confirmed and self.current==self.requested
        except ContractError:
            self._invalidate();raise


def deny_permission(_request):
    """No approval UI or standing authorization in this pure foundation."""
    return {'outcome':{'outcome':'cancelled'}}


class SessionReducer:
    """Single prompt attempt, ordered updates, correlation bound only by response."""
    def __init__(self, session_id, *, deadline, max_events=256, max_bytes=262144, clock=time.monotonic, on_event=None):
        self.session_id=text(session_id)
        check(type(deadline) in (float,int) and math.isfinite(deadline))
        check(type(max_events) is int and 0<max_events<=4096)
        check(type(max_bytes) is int and 0<max_bytes<=4*1024*1024)
        check(callable(clock));self.clock=clock;self._last_now=None
        self.deadline=deadline;self.max_events=max_events;self.max_bytes=max_bytes
        self.run_id=None;self.message_id=None;self.outcome='pending';self.fault=None
        self.terminal_reason=None;self.cancel_requested=False;self.insert_acknowledged=False
        self._buffer=[];self._events=0;self._bytes=0;self._messages={};self._terminal=False
        self._error_seen=False
        check(on_event is None or callable(on_event))
        self._on_event=on_event;self._emitted_bytes=0;self._emitted_events=0
        self._emitting=False

    def _emit(self, event):
        """Trusted sink must synchronously enqueue without blocking/reentrancy.

        None/True acknowledges delivery; backpressure, async callbacks and errors
        invalidate this attempt. No callback exception text or metadata escapes.
        The sink must independently bound its queue; this layer bounds emission.
        """
        if self._on_event is None or self.fault:return
        try:
            check(not self._emitting)
            event,size=snapshot(event,6*262144+4096)
            self._emitted_bytes+=size;self._emitted_events+=1
            if self._emitted_bytes>16*1024*1024 or self._emitted_events>self.max_events:
                self._fail('EVENT_SINK_OVERFLOW');return
            self._emitting=True
            result=self._on_event(event)
            if inspect.iscoroutine(result):result.close()
            check(result is None or result is True)
            self.tick(self.clock())
        except Exception:self._fail('EVENT_SINK_FAILED')
        finally:self._emitting=False


    def _fail(self, code):
        if self.fault is None:self.fault=code
        self.outcome='indeterminate'

    def tick(self, now):
        if type(now) not in (int,float) or not math.isfinite(now):self._fail('INVALID_CLOCK')
        elif self._last_now is not None and now<self._last_now:self._fail('INVALID_CLOCK')
        else:
            self._last_now=now
            if now>=self.deadline and not self._terminal:self._fail('TIMEOUT')

    def disconnect(self):
        if not self._terminal:self._fail('DISCONNECTED')

    def cancel_notification(self):
        self.cancel_requested=True
        # Returning notification params is not proof it was sent or accepted.
        return {'sessionId':self.session_id}

    def acknowledge(self, response):
        if self._emitting:self._fail('EVENT_SINK_FAILED');return
        self.tick(self.clock())
        if self.fault:return
        try:
            response,_=snapshot(response)
            check(type(response) is dict and not self.insert_acknowledged)
            mid=text(response.get('messageId'));meta=response.get('_meta')
            check(type(meta) is dict);run=text(meta.get(REQUEST_ID))
            self.message_id=mid;self.run_id=run;self.insert_acknowledged=True
            buffered=self._buffer;self._buffer=[]
            for update in buffered:self._reduce(update)
        except ContractError:self._fail('INVALID_ACK')

    def update(self, envelope):
        if self._emitting:self._fail('EVENT_SINK_FAILED');return
        self.tick(self.clock())
        if self.fault:return
        try:
            envelope,size=snapshot(envelope,self.max_bytes)
            self._bytes+=size;self._events+=1
            if self._bytes>self.max_bytes or self._events>self.max_events:
                self._fail('OVERFLOW');return
            check(type(envelope) is dict and type(envelope.get('sessionId')) is str)
            if envelope['sessionId']!=self.session_id:return
            update=envelope.get('update');check(type(update) is dict)
            if not self.insert_acknowledged:self._buffer.append(update)
            else:self._reduce(update)
        except ContractError:self._fail('INVALID_UPDATE')

    def _reduce(self, update):
        if self.fault:return
        kind=update.get('sessionUpdate');check(type(kind) is str)
        if kind in ('subagent_update','config_option_update','session_info_update'):return
        meta=update.get('_meta');check(type(meta) is dict)
        run=text(meta.get(REQUEST_ID))
        if run!=self.run_id:return
        # Public projection moves child events to their own envelope sessionId;
        # also reject any leaked private subagent marker or non-root run identity.
        if SUBAGENT in meta:return
        if ROOT_ID in meta and meta[ROOT_ID]!=self.run_id:return
        if kind=='agent_message_chunk':
            check(not self._terminal);mid=text(update.get('messageId'))
            content=update.get('content');check(type(content) is dict)
            if content.get('type')=='text':
                value=content.get('text');check(type(value) is str)
                self._messages[mid]=self._messages.get(mid,'')+value
                self._emit({'type':'text_snapshot','text':''.join(self._messages.values())})
            else:self._fail('UNSUPPORTED_CONTENT')
        elif kind=='agent_message':
            check(not self._terminal);mid=text(update.get('messageId'))
            if 'content' in update:
                contents=update['content']
                if contents is None:contents=[]
                check(type(contents) is list)
                parts=[]
                for content in contents:
                    check(type(content) is dict and content.get('type')=='text' and type(content.get('text')) is str)
                    parts.append(content['text'])
                self._messages[mid]=''.join(parts)
                self._emit({'type':'text_snapshot','text':''.join(self._messages.values())})
        elif kind==ERROR_UPDATE:
            check(not self._terminal)
            error=update.get('error')
            check(type(update.get('willRetry')) is bool and type(error) is dict)
            check(re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*',text(error.get('code'))) is not None)
            text(error.get('message'),4096)
            if not update['willRetry']:self._error_seen=True
        elif kind=='state_update':
            state=update.get('state');check(state in ('running','requires_action','idle','unknown'))
            if state!='idle':
                check(not self._terminal)
                self._emit({'type':'native_status','state':state});return
            reason=update.get('stopReason')
            check(type(reason) is str)
            if self._terminal:
                check(reason==self.terminal_reason);return
            self._terminal=True;self.terminal_reason=reason
            if reason=='cancelled':self.outcome='cancelled'
            elif reason==ERROR_UPDATE:
                error=meta.get('codebuddy.ai/error')
                check(self._error_seen or type(error) is dict and type(error.get('code')) is str and type(error.get('message')) is str)
                self.outcome='failed'
            elif reason in ('end_turn','refusal') and not self._error_seen and ''.join(self._messages.values()).strip():self.outcome='succeeded'
            else:self._fail('UNCONFIRMED_TERMINAL')
            if not self.fault:
                self._emit({'type':'native_status','state':state,'stop_reason':reason})
        # Tool/reasoning/usage/config events are never converted to remote tool calls
        # or invented usage. They remain outside this minimal text observation.

    def evidence(self):
        return {'outcome':self.outcome,'insert_acknowledged':self.insert_acknowledged,
                'terminal_observed':self._terminal,'terminal_reason':self.terminal_reason,
                'fault':self.fault,'cancel_requested':self.cancel_requested,
                'text':''.join(self._messages.values()),'usage':{'state':'unknown'},
                'liveness':'unknown','retry_safe':False}
