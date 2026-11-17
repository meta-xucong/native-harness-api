"""IO-free public beta16 outer Harness wire; single authorized local attempt.

Source: pinned @tencent-ai/agent-harness 0.2.0-beta.16 lib/client/client.js
(9ad4535f109270b404ed393d3774e41eb61babee5b68caa817399235a45c9984),
lib/auth/schemas.d.ts, lib/product/schemas.d.ts, lib/protocol/acp-v2.js.
The owner must validate the vendor/transport, authorize local execution, persist
intent before sending, and own bounded pipes/deadlines/EOF/Job cleanup. This
module never logs in, reads credentials, retries, or proves process quiescence.
"""
import json
import hashlib
import math
import re
import time
from .beta15_reducer import ContractError, REQUEST_ID, check, snapshot
from .beta15_session import FrameDecodeError, SessionCoordinator, decode_frame
from .beta16_events import Beta16EventProjector, ProjectionError
from .usage_evidence import UsageEvidence


_FRAME_KINDS = ('invalid_frame', 'invalid_utf8', 'invalid_json', 'invalid_frame_structure', 'invalid_jsonrpc_version',
                'invalid_response', 'unknown_response', 'invalid_request',
                'invalid_notification', 'rpc_error', 'prompt_ack_missing_fields', 'interaction_refused',
                'evidence_changed', 'frame_limit')
_FRAME_METHODS = ('initialize', 'product/read', 'account/read', 'auth/status', 'session/new',
                  'session/set_config_option', 'session/prompt', 'session/close', 'session/update',
                  'elicitation/create', 'session/request_permission', 'account/updated',
                  'product/updated', '$/cancel_request')
_PROMPT_ACK_FIELDS = ('messageId', '_meta', '_meta.' + REQUEST_ID)
_SESSION_UPDATE_TYPES = ('agent_message', 'agent_message_chunk', 'agent_thought', 'agent_thought_chunk',
                         'available_commands_update', 'config_option_update', 'notice', 'plan_update',
                         'session_info_update', 'state_update', 'subagent_update', 'tool_call_update',
                         'tool_call_content_chunk', 'usage_update', 'user_message', 'user_message_chunk',
                         '_codebuddy.ai/error')
_UPDATE_STAGES = ('envelope', 'projection', 'coordinator', 'turn_reducer')


def safe_failed_frame(value):
    """Project an allowlisted diagnostic without frame text or wire identifiers."""
    if type(value) is not dict or value.get('kind') not in _FRAME_KINDS:
        return None
    result = {'kind': value['kind']}
    if value.get('method') in _FRAME_METHODS:
        result['method'] = value['method']
    if result['kind'] == 'rpc_error':
        code = value.get('rpc_error_code')
        if type(code) is int and abs(code) <= (1 << 53) - 1:
            result['rpc_error_code'] = code
    if result['kind'] == 'prompt_ack_missing_fields':
        fields = value.get('missing_fields')
        if type(fields) in (list, tuple):
            result['missing_fields'] = [field for field in _PROMPT_ACK_FIELDS if field in fields]
    if result['kind'] == 'invalid_notification' and result.get('method') == 'session/update':
        stage = value.get('update_stage')
        if stage in _UPDATE_STAGES:
            result['update_stage'] = stage
            kind = value.get('update_type')
            result['update_type'] = kind if kind in _SESSION_UPDATE_TYPES else 'other'
    return result


def _missing_prompt_ack_fields(result):
    if type(result) is not dict:
        return ()
    missing = ['messageId'] if 'messageId' not in result else []
    if '_meta' not in result:
        missing.append('_meta')
    elif type(result['_meta']) is dict and REQUEST_ID not in result['_meta']:
        missing.append('_meta.' + REQUEST_ID)
    return missing


def _session_update_type(params):
    update = params.get('update') if type(params) is dict else None
    return update.get('sessionUpdate') if type(update) is dict else None


def keys(v, required, optional=()):
    return type(v) is dict and set(required) <= v.keys() <= set(required) | set(optional)


def meta(v):
    return '_meta' not in v or v['_meta'] is None or type(v['_meta']) is dict


def string(v):
    return type(v) is str and 0 < len(v) <= 4096


def product(v, *, version='5.9.3'):
    required = ('state', 'mode', 'generation', 'revision', 'productName', 'applicationName',
                'productVersion', 'networkEnvironment', 'deploymentType', 'defaultFolderName',
                'freshness', 'links', 'productFeatures', 'featureToggles')
    return (keys(v, required, ('_meta',)) and meta(v) and v['state'] == 'ready'
            and v['mode'] == 'managed' and v['productName'] == v['applicationName'] == 'WorkBuddy'
            and v['productVersion'] == version and type(v['generation']) in (int, float)
            and string(v['revision']) and v['freshness'] in ('fresh', 'cached', 'degraded')
            and all(v[k] is None or type(v[k]) is str for k in ('networkEnvironment', 'deploymentType', 'defaultFolderName'))
            and keys(v['links'], (), ('documentation', 'home', 'privacy', 'support', 'terms'))
            and all(type(x) is str for x in v['links'].values())
            and all(type(v[k]) is dict and all(type(x) is bool for x in v[k].values())
                    for k in ('productFeatures', 'featureToggles')))


def account(v):
    if not (keys(v, ('state', 'mode', 'account'), ('_meta',)) and meta(v)
            and v['state'] == 'authenticated' and v['mode'] == 'managed'):
        return False
    a = v['account']
    if not (keys(a, ('id',), ('displayName', 'avatarUrl', 'authMethodId', 'organization', 'department', 'providerIdentity')) and string(a['id'])):
        return False
    for key in ('displayName', 'avatarUrl', 'authMethodId', 'department'):
        if a.get(key) is not None and type(a[key]) is not str:
            return False
    org = a.get('organization')
    if org is not None and not (keys(org, ('id',), ('displayName',)) and string(org['id'])
                              and (org.get('displayName') is None or type(org['displayName']) is str)):
        return False
    p = a.get('providerIdentity')
    if 'providerIdentity' in a and not (keys(p, ('accountType',), ('idp', 'oneidAccountId'))
                                      and all(type(x) is str for x in p.values())):
        return False
    return True


def account_identity_digest(identity):
    """Versioned stable account binding, not authentication or a secret.

    Input is account identity (not account/read snapshot). Unknown fields,
    credentials and malformed/null providerIdentity are rejected. Display values
    are validated but excluded; absent optional stable fields normalize to null.
    """
    identity = snapshot(identity, 65536)[0]
    check(account({'state': 'authenticated', 'mode': 'managed', 'account': identity}))
    def bounded(v):
        if type(v) is str:
            return len(v) <= 4096
        if type(v) is dict:
            return all(bounded(x) for x in v.values())
        return v is None
    check(bounded(identity))
    provider = identity.get('providerIdentity')
    if provider is not None:
        check(all(k not in provider or string(provider[k]) for k in ('idp', 'oneidAccountId')))
    stable = dict(id=identity['id'], authMethodId=identity.get('authMethodId'),
                  organizationId=(identity.get('organization') or {}).get('id'),
                  providerIdentity=provider)
    try:
        raw = json.dumps(stable, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    except (ValueError, UnicodeError, TypeError):
        raise ContractError('PUBLIC_ACCOUNT_IDENTITY_REJECTED') from None
    return hashlib.sha256(b'native-harness-public-account-v1\0' + raw).hexdigest()


class PublicSfeSession:
    """Serialized state machine. All outputs are intents, not sent commands.

    receive accepts one bounded complete UTF-8 JSON frame (newline optional).
    Returned tuples contain encoded NDJSON frames. Account identity is discarded.
    Session methods are explicit so the owner can persist each intent first.
    """

    RETAIN_LATE_SESSION_FOR_CLOSE = False

    def __init__(self, *, deadline, expected_account_digest, clock=time.monotonic, max_frames=4096,
                 max_bytes=4 * 1024 * 1024, on_event=None):
        check(type(max_frames) is int and 0 < max_frames <= 4096)
        check(type(max_bytes) is int and 0 < max_bytes <= 4 * 1024 * 1024)
        check(type(expected_account_digest) is str and re.fullmatch(r'[0-9a-f]{64}', expected_account_digest) is not None)
        self.expected_account_digest = expected_account_digest
        self.coordinator = SessionCoordinator(deadline=deadline, clock=clock, on_event=on_event,
                                              reducer_max_events=max_frames, reducer_max_bytes=max_bytes)
        self.projector = Beta16EventProjector(max_events=max_frames, max_bytes=max_bytes)
        self.usage_evidence = UsageEvidence(max_events=max_frames)
        self.max_frames, self.max_bytes = max_frames, max_bytes
        self.frames_received = self.bytes_received = self.frames_sent = 0
        self.failure_code = None
        self._first_failed_frame = None
        self._started = self._ready = self._account = False
        self._next = 1
        self._pending = {}

    @property
    def ready(self):
        return self._ready and not self.failure_code and not self.coordinator.fault

    def fail(self, reason='transport'):
        if not self.failure_code:
            self.failure_code = reason if reason in ('timeout', 'eof', 'transport', 'protocol', 'interaction_refused',
                'evidence_changed', 'limit', 'rpc_error', 'prompt_ack_missing_fields') else 'transport'
        self._ready = False
        self.coordinator.disconnect()
        return ()

    def _fail_frame(self, kind, method=None, *, reason='protocol', **details):
        if self._first_failed_frame is None:
            self._first_failed_frame = safe_failed_frame(dict(kind=kind, method=method, **details))
        return self.fail(reason)

    def tick(self):
        if not self.coordinator.tick():
            self.fail('timeout')
        return not self.failure_code

    def _emit(self, v):
        self.frames_sent += 1
        check(self.frames_sent <= 32)
        raw = json.dumps(v, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode() + b'\n'
        check(len(raw) <= 262144)
        return raw

    def _request(self, method, params, inner=None):
        number = self._next
        self._next += 1
        self._pending[number] = (method, inner)
        return self._emit(dict(jsonrpc='2.0', id=number, method=method, params=params))

    def start(self):
        try:
            check(not self._started and self.tick())
            self._started = True
            return (self._request('initialize', dict(protocolVersion=1,
                clientInfo=dict(name='native-harness-local-session', version='1'),
                capabilities=dict(agent=dict(elicitation=dict(form={}, url={})),
                                  config=dict(warning={}), account=dict(mode='managed'), product=dict(mode='managed')))),)
        except (ContractError, ValueError):
            return self.fail('protocol')

    def _command(self, method, *args, cleanup=False):
        try:
            check(self._started and (cleanup or self.ready and self.tick()))
            intent = getattr(self.coordinator, method)(*args)
            if 'id' in intent:
                return (self._request(intent['method'], intent['params'], intent['id']),)
            return (self._emit({'jsonrpc': '2.0', **intent}),)
        except (ContractError, ValueError):
            return self.fail('protocol')

    def new_session(self, cwd): return self._command('new_session', cwd)
    def select_model(self, model_id): return self._command('select_model', model_id)
    def prompt(self, text): return self._command('prompt', text)
    def cancel(self): return self._command('cancel', cleanup=True)
    def close_session(self): return self._command('close_session', cleanup=True)

    def _after_initialize(self):
        return (self._request('product/read', {}),)

    def _check_product(self, value):
        check(product(value))

    def _check_account(self, value):
        check(account(value) and account_identity_digest(value['account']) == self.expected_account_digest)

    def receive(self, frame):
        kind, method = 'invalid_frame_structure', None
        v = None
        try:
            check(self._started)
            self.frames_received += 1
            check(type(frame) is bytes)
            self.bytes_received += len(frame)
            if self.frames_received > self.max_frames or self.bytes_received > self.max_bytes:
                return self._fail_frame('frame_limit', reason='limit')
            v = decode_frame(frame)
            if v.get('jsonrpc') != '2.0':
                return self._fail_frame('invalid_jsonrpc_version')
            if 'method' in v:
                kind = 'invalid_request' if 'id' in v else 'invalid_notification'
                method = v['method']
                return self._incoming(v)
            kind = 'invalid_response'
            check(type(v.get('id')) is int)
            entry = self._pending.get(v['id'])
            if entry is None:
                return self._fail_frame('unknown_response')
            method, inner = entry
            if 'error' in v:
                check(keys(v, ('jsonrpc', 'id', 'error')) and keys(v['error'], ('code', 'message'), ('data',)))
                error = v['error']
                check(type(error['code']) is int and type(error['message']) is str)
                self._pending.pop(v['id'])
                return self._fail_frame('rpc_error', method, reason='rpc_error', rpc_error_code=error['code'])
            check(keys(v, ('jsonrpc', 'id', 'result')))
            self._pending.pop(v['id'])
            if inner is not None:
                late_session = (self.RETAIN_LATE_SESSION_FOR_CLOSE and method == 'session/new'
                                and self.failure_code in (None, 'timeout'))
                check(method == 'session/close' or not self.failure_code or late_session)
                self.coordinator.response({'id': inner, 'result': v['result']},
                                          retain_late_session_for_close=late_session)
                if self.coordinator.fault and not self.failure_code:
                    if self.coordinator.fault == 'TIMEOUT':
                        return self.fail('timeout' if late_session else 'protocol')
                    if method == 'session/prompt':
                        missing = _missing_prompt_ack_fields(v['result'])
                        if missing:
                            return self._fail_frame('prompt_ack_missing_fields', method,
                                                    reason='prompt_ack_missing_fields', missing_fields=missing)
                        reducer = self.coordinator.reducer
                        if self.coordinator.fault == 'TURN_INDETERMINATE' and reducer.fault != 'INVALID_ACK':
                            return self._fail_frame('invalid_notification', 'session/update',
                                                    update_stage='turn_reducer',
                                                    update_type=reducer.failed_update_kind)
                    return self._fail_frame(kind, method)
                return ()
            check(not self.failure_code and self.tick())
            r = v['result']
            if method == 'initialize':
                check(keys(r, ('protocolVersion', 'serverInfo', 'capabilities', 'authMethods'), ('_meta',)) and meta(r))
                check(type(r['protocolVersion']) is int and r['protocolVersion'] == 1)
                info = r['serverInfo']
                check(keys(info, ('name', 'version'), ('title',)) and info['name'] == '@genie/agent-harness' and string(info['version']))
                check('title' not in info or type(info['title']) is str)
                check(type(r['capabilities']) is dict and r['authMethods'] == [{'description': None, 'methodId': 'workbuddy-desktop', 'name': 'TencentCloud', 'type': 'agent'}])
                return (self._emit(dict(jsonrpc='2.0', method='initialized', params={})), *self._after_initialize())
            if method == 'product/read':
                self._check_product(r)
                return (self._request('account/read', {}),)
            if method == 'account/read':
                self._check_account(r)
                self._account = True
                return (self._request('auth/status', {}),)
            check(method == 'auth/status' and keys(r, ('authenticated',), ('message', '_meta')) and meta(r)
                  and r['authenticated'] is True and (r.get('message') is None or type(r['message']) is str))
            self._ready = True
            return ()
        except FrameDecodeError as failure:
            return self._fail_frame(failure.kind)
        except (ContractError, ProjectionError, ValueError, TypeError, OverflowError, RecursionError):
            if kind == 'invalid_notification' and method == 'session/update':
                return self._fail_frame(kind, method, update_stage='envelope',
                                        update_type=_session_update_type(v.get('params')))
            return self._fail_frame(kind, method)

    def _incoming(self, v):
        if v['method'] == '$/cancel_request':
            check(keys(v, ('jsonrpc', 'method', 'params')) and keys(v['params'], ('requestId',), ('_meta',))
                  and meta(v['params']))
            request_id = v['params']['requestId']
            check(request_id is None or type(request_id) in (str, int)
                  or type(request_id) is float and math.isfinite(request_id))
            # This refers to a peer-originated request. Reverse requests here are
            # answered synchronously, so no peer request remains to cancel.
            return ()
        if 'id' in v:
            check(keys(v, ('jsonrpc', 'id', 'method', 'params')) and type(v['id']) is int
                  and 0 <= v['id'] <= 2147483647 and type(v['params']) is dict)
            check(v['method'] in ('elicitation/create', 'session/request_permission'))
            result = {'action': 'cancel'} if v['method'] == 'elicitation/create' else {'outcome': {'outcome': 'cancelled'}}
            response = self._emit(dict(jsonrpc='2.0', id=v['id'], result=result))
            self._fail_frame('interaction_refused', v['method'], reason='interaction_refused')
            return (response,)
        check(keys(v, ('jsonrpc', 'method', 'params')))
        if v['method'] in ('account/updated', 'product/updated'):
            return self._fail_frame('evidence_changed', v['method'], reason='evidence_changed')
        check(not self.failure_code and self.ready and v['method'] == 'session/update' and self.tick())
        params = v['params']
        update_type = _session_update_type(params)
        try:
            projected = self.projector.project(params)
        except ProjectionError:
            return self._fail_frame('invalid_notification', v['method'], update_stage='projection',
                                    update_type=update_type)
        self.usage_evidence.observe(params)
        if projected is not None:
            self.coordinator.update(projected)
            if self.coordinator.fault:
                stage = 'turn_reducer' if self.coordinator.fault == 'TURN_INDETERMINATE' else 'coordinator'
                return self._fail_frame('invalid_notification', v['method'], update_stage=stage,
                                        update_type=update_type)
        return ()

    def evidence(self):
        result = self.coordinator.evidence()
        reducer = self.coordinator.reducer
        result['metering_evidence'] = self.usage_evidence.summary(
            self.coordinator.session_id, None if reducer is None else reducer.run_id,
            accepted=reducer is not None and reducer.insert_acknowledged,
            terminal_observed=reducer is not None and reducer.evidence()['terminal_observed'])
        result.update(protocol_ready=self.ready, account_verified=self.ready and self._account,
                      failure_code=self.failure_code, frames_received=self.frames_received,
                      bytes_received=self.bytes_received, frames_sent=self.frames_sent,
                      owned_cleanup_confirmed=False)
        if self._first_failed_frame is not None:
            result['first_failed_frame'] = safe_failed_frame(self._first_failed_frame)
        return result
