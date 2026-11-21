"""Bounded, IO-free anonymous catalog probe for the public Harness protocol.

Auth, sessions, provisioning and inference are never sent. This protocol verifier
is not authentication or process-cleanup evidence. Source contract: public
beta16 lib/client/client.js, lib/{auth,product}/schemas.d.ts and application/services.js.
Frames are complete JSON texts; the supervisor owns framing, deadlines and EOF.
"""
import json
import math

MAX_FRAME_BYTES = 65536
MAX_FRAMES = 8
CATALOG = [{"description": None, "methodId": "workbuddy-desktop", "name": "TencentCloud", "type": "agent"}]


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("constant")


def _bounded(value, depth=0):
    if depth > 16:
        return False
    if type(value) is dict:
        return len(value) <= 256 and all(len(k) <= 256 and _bounded(v, depth + 1) for k, v in value.items())
    if type(value) is list:
        return len(value) <= 256 and all(_bounded(v, depth + 1) for v in value)
    if type(value) is str:
        return len(value) <= 8192
    return (value is None or type(value) is bool or (type(value) is int and abs(value) <= 2 ** 53)
            or (type(value) is float and math.isfinite(value)))


def _keys(value, required, optional=()):
    return type(value) is dict and set(required) <= value.keys() and value.keys() <= set(required) | set(optional)


def _meta(value):
    return '_meta' not in value or value['_meta'] is None or type(value['_meta']) is dict


def _product(value):
    required = ('state', 'mode', 'generation', 'revision', 'productName', 'applicationName', 'productVersion',
                'networkEnvironment', 'deploymentType', 'defaultFolderName', 'freshness', 'links', 'productFeatures', 'featureToggles')
    if not _keys(value, required, ('_meta',)) or not _meta(value):
        return False
    if not (value['state'] == 'ready' and value['mode'] == 'managed' and value['productName'] == 'WorkBuddy'
            and value['applicationName'] == 'WorkBuddy' and value['productVersion'] == '5.9.3'):
        return False
    if type(value['generation']) not in (int, float) or not math.isfinite(value['generation']):
        return False
    if type(value['revision']) is not str or not value['revision'] or value['freshness'] not in ('fresh', 'cached', 'degraded'):
        return False
    if not all(value[k] is None or type(value[k]) is str for k in ('networkEnvironment', 'deploymentType', 'defaultFolderName')):
        return False
    if not _keys(value['links'], (), ('documentation', 'home', 'privacy', 'support', 'terms')) or not all(type(v) is str for v in value['links'].values()):
        return False
    return all(type(value[k]) is dict and all(type(v) is bool for v in value[k].values()) for k in ('productFeatures', 'featureToggles'))


class CatalogProtocol:
    """Single-use, fail-closed verifier. No server payload is retained."""
    def __init__(self):
        self._s = dict(schema_version=1, started=False, initialize_verified=False, product_verified=False,
                       catalog_verified=False, account_unauthenticated=False, auth_status_unauthenticated=False,
                       protocol_complete=False, failed=False, failure_code='none', frames_received=0, frames_sent=0,
                       elicitation_rejected=False, permission_rejected=False, native_account_verified=False, real_model_ready=False)
        self._expected = 0

    @property
    def done(self):
        return self._s['failed'] or self._s['protocol_complete']

    @property
    def succeeded(self):
        return self._s['protocol_complete'] and not self._s['failed']

    def summary(self):
        return dict(self._s)

    def fail(self, reason='transport'):
        # Do not retain arbitrary exception text or caller values.
        code = reason if type(reason) is str and reason in ('timeout', 'eof', 'transport') else 'transport'
        self._stop(code)
        return ()

    def _stop(self, code):
        if not self._s['failed']:
            self._s['failed'] = True
            self._s['failure_code'] = code
        self._s['protocol_complete'] = False

    def _emit(self, value):
        self._s['frames_sent'] += 1
        return json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\n'

    def _request(self, number, method, params):
        self._expected = number
        return self._emit(dict(jsonrpc='2.0', id=number, method=method, params=params))

    def start(self):
        if self._s['started'] or self.done:
            self._stop('invalid_state')
            return ()
        self._s['started'] = True
        params = dict(protocolVersion=1, clientInfo=dict(name='native-harness-public-catalog', version='1'),
                      capabilities=dict(agent=dict(elicitation=dict(form={}, url={})), config=dict(warning={}),
                                        account=dict(mode='managed'), product=dict(mode='managed')))
        return (self._request(1, 'initialize', params),)

    def receive(self, frame):
        if self.done or not self._s['started']:
            self._stop('invalid_state')
            return ()
        self._s['frames_received'] += 1
        if self._s['frames_received'] > MAX_FRAMES:
            self._stop('frame_limit')
            return ()
        try:
            if type(frame) is not bytes or not 0 < len(frame) <= MAX_FRAME_BYTES:
                raise ValueError()
            value = json.loads(frame.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
            if not _bounded(value) or type(value) is not dict or value.get('jsonrpc') != '2.0':
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError, OverflowError):
            self._stop('malformed_frame')
            return ()
        if 'method' in value:
            return self._reverse(value)
        if not _keys(value, ('jsonrpc', 'id', 'result')) or type(value['id']) is not int or value['id'] != self._expected:
            self._stop('unexpected_response')
            return ()
        result = value['result']
        if self._expected == 1:
            if not self._initialize(result):
                self._stop('initialize_rejected')
                return ()
            self._s['initialize_verified'] = True
            return (self._emit(dict(jsonrpc='2.0', method='initialized', params={})), self._request(2, 'product/read', {}))
        if self._expected == 2:
            if not _product(result):
                self._stop('product_rejected')
                return ()
            self._s['product_verified'] = self._s['catalog_verified'] = True
            return (self._request(3, 'account/read', {}),)
        if self._expected == 3:
            if not (_keys(result, ('state', 'mode', 'account'), ('_meta',)) and _meta(result)
                    and result['state'] == 'unauthenticated' and result['mode'] is None and result['account'] is None):
                self._stop('account_rejected')
                return ()
            self._s['account_unauthenticated'] = True
            return (self._request(4, 'auth/status', {}),)
        if not (_keys(result, ('authenticated',), ('message', '_meta')) and _meta(result)
                and result['authenticated'] is False and (result.get('message') is None or type(result['message']) is str)):
            self._stop('auth_status_rejected')
            return ()
        self._s['auth_status_unauthenticated'] = self._s['protocol_complete'] = True
        return ()

    @staticmethod
    def _initialize(result):
        if not _keys(result, ('protocolVersion', 'serverInfo', 'capabilities', 'authMethods'), ('_meta',)) or not _meta(result):
            return False
        info = result['serverInfo']
        return (type(result['protocolVersion']) is int and result['protocolVersion'] == 1
                and _keys(info, ('name', 'version'), ('title',)) and info['name'] == '@genie/agent-harness'
                and type(info['version']) is str and 0 < len(info['version']) <= 128
                and ('title' not in info or type(info['title']) is str)
                and type(result['capabilities']) is dict and result['authMethods'] == CATALOG)

    def _reverse(self, value):
        # Only bounded numeric correlation IDs may be echoed. String IDs may
        # themselves carry arbitrary URLs/secrets and are rejected without echo.
        if not (_keys(value, ('jsonrpc', 'id', 'method', 'params')) and type(value['id']) is int
                and 0 <= value['id'] <= 2147483647 and type(value['params']) is dict):
            self._stop('unexpected_message')
            return ()
        if value['method'] == 'elicitation/create':
            self._s['elicitation_rejected'] = True
            result = {'action': 'cancel'}
        elif value['method'] == 'session/request_permission':
            self._s['permission_rejected'] = True
            result = {'outcome': {'outcome': 'cancelled'}}
        else:
            self._stop('unexpected_message')
            return ()
        self._stop('interaction_refused')
        return (self._emit(dict(jsonrpc='2.0', id=value['id'], result=result)),)
