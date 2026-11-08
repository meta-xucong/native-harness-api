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
import re
import time
from .beta15_reducer import ContractError, check, snapshot
from .beta15_session import SessionCoordinator, decode_frame
from .beta16_events import Beta16EventProjector, ProjectionError


def keys(v, required, optional=()):
    return type(v) is dict and set(required) <= v.keys() <= set(required) | set(optional)


def meta(v):
    return '_meta' not in v or v['_meta'] is None or type(v['_meta']) is dict


def string(v):
    return type(v) is str and 0 < len(v) <= 4096


def product(v):
    required = ('state', 'mode', 'generation', 'revision', 'productName', 'applicationName',
                'productVersion', 'networkEnvironment', 'deploymentType', 'defaultFolderName',
                'freshness', 'links', 'productFeatures', 'featureToggles')
    return (keys(v, required, ('_meta',)) and meta(v) and v['state'] == 'ready'
            and v['mode'] == 'managed' and v['productName'] == v['applicationName'] == 'WorkBuddy'
            and v['productVersion'] == '5.9.3' and type(v['generation']) in (int, float)
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
    def __init__(self, *, deadline, expected_account_digest, clock=time.monotonic, max_frames=4096,
                 max_bytes=4 * 1024 * 1024, on_event=None):
        check(type(max_frames) is int and 0 < max_frames <= 4096)
        check(type(max_bytes) is int and 0 < max_bytes <= 4 * 1024 * 1024)
        check(type(expected_account_digest) is str and re.fullmatch(r'[0-9a-f]{64}', expected_account_digest) is not None)
        self.expected_account_digest = expected_account_digest
        self.coordinator = SessionCoordinator(deadline=deadline, clock=clock, on_event=on_event,
                                              reducer_max_events=max_frames, reducer_max_bytes=max_bytes)
        self.projector = Beta16EventProjector(max_events=max_frames, max_bytes=max_bytes)
        self.max_frames, self.max_bytes = max_frames, max_bytes
        self.frames_received = self.bytes_received = self.frames_sent = 0
        self.failure_code = None
        self._started = self._ready = self._account = False
        self._next = 1
        self._pending = {}

    @property
    def ready(self):
        return self._ready and not self.failure_code and not self.coordinator.fault

    def fail(self, reason='transport'):
        if not self.failure_code:
            self.failure_code = reason if reason in ('timeout', 'eof', 'transport', 'protocol', 'interaction_refused', 'evidence_changed', 'limit') else 'transport'
        self._ready = False
        self.coordinator.disconnect()
        return ()

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

    def receive(self, frame):
        try:
            check(self._started)
            self.frames_received += 1
            check(type(frame) is bytes)
            self.bytes_received += len(frame)
            if self.frames_received > self.max_frames or self.bytes_received > self.max_bytes:
                return self.fail('limit')
            v = decode_frame(frame)
            check(v.get('jsonrpc') == '2.0')
            if 'method' in v:
                return self._incoming(v)
            check(keys(v, ('jsonrpc', 'id', 'result')) and type(v['id']) is int)
            entry = self._pending.pop(v['id'], None)
            check(entry is not None)
            method, inner = entry
            if inner is not None:
                # Close ACK may still be collected after a terminal protocol fault.
                check(method == 'session/close' or not self.failure_code)
                self.coordinator.response({'id': inner, 'result': v['result']})
                if self.coordinator.fault and not self.failure_code:
                    self.fail('protocol')
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
                return (self._emit(dict(jsonrpc='2.0', method='initialized', params={})), self._request('product/read', {}))
            if method == 'product/read':
                check(product(r))
                return (self._request('account/read', {}),)
            if method == 'account/read':
                check(account(r) and account_identity_digest(r['account']) == self.expected_account_digest)
                self._account = True
                return (self._request('auth/status', {}),)
            check(method == 'auth/status' and keys(r, ('authenticated',), ('message', '_meta')) and meta(r)
                  and r['authenticated'] is True and (r.get('message') is None or type(r['message']) is str))
            self._ready = True
            return ()
        except (ContractError, ProjectionError, ValueError, TypeError, OverflowError, RecursionError):
            return self.fail('protocol')

    def _incoming(self, v):
        if 'id' in v:
            check(keys(v, ('jsonrpc', 'id', 'method', 'params')) and type(v['id']) is int
                  and 0 <= v['id'] <= 2147483647 and type(v['params']) is dict)
            check(v['method'] in ('elicitation/create', 'session/request_permission'))
            result = {'action': 'cancel'} if v['method'] == 'elicitation/create' else {'outcome': {'outcome': 'cancelled'}}
            response = self._emit(dict(jsonrpc='2.0', id=v['id'], result=result))
            self.fail('interaction_refused')
            return (response,)
        check(keys(v, ('jsonrpc', 'method', 'params')))
        if v['method'] in ('account/updated', 'product/updated'):
            return self.fail('evidence_changed')
        check(not self.failure_code and self.ready and v['method'] == 'session/update' and self.tick())
        projected = self.projector.project(v['params'])
        if projected is not None:
            self.coordinator.update(projected)
            if self.coordinator.fault:
                self.fail('protocol')
        return ()

    def evidence(self):
        result = self.coordinator.evidence()
        result.update(protocol_ready=self.ready, account_verified=self.ready and self._account,
                      failure_code=self.failure_code, frames_received=self.frames_received,
                      bytes_received=self.bytes_received, frames_sent=self.frames_sent,
                      owned_cleanup_confirmed=False)
        return result
