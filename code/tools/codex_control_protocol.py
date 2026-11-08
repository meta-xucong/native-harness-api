"""IO-free user login/catalog protocol. No execution, credential import or retry."""
import json
import time
from native_harness_api.beta15_session import decode_frame
from native_harness_api.codex_session import account_identity_digest


class CodexControlProtocol:
    def __init__(self, *, deadline, expected_account_digest, on_event=None,
                 mode='models', on_device=None, clock=time.monotonic):
        if mode not in ('login', 'models'):
            raise ValueError('CONTROL_MODE')
        self.mode, self.deadline, self.clock = mode, deadline, clock
        self.expected_account_digest, self.on_device = expected_account_digest, on_device
        self.phase = 'new'
        self.fault = self.outcome = None
        self.thread_id = self.turn_id = None
        self.terminal_observed = False
        self.account = None
        self.models = []
        self._next = 1
        self._pending = {}
        self._login_id = None
        self._completion = None
        self._cursors = set()
        self._count = self._bytes = 0

    def fail(self, reason):
        self.fault = self.fault or reason
        return ()

    def tick(self):
        if not self.terminal_observed and self.clock() >= self.deadline:
            self.fail('timeout')
        return self.fault is None

    def cancel(self):
        # Killing this owned server is not revocation or rollback of device login.
        return ()

    @staticmethod
    def _frame(value):
        return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode() + b'\n'

    def _request(self, method, params):
        number = self._next
        self._next += 1
        self._pending[number] = method
        return self._frame({'id': number, 'method': method, 'params': params})

    def start(self):
        if self.phase != 'new':
            return self.fail('protocol')
        self.phase = 'initializing'
        return (self._request('initialize', {'clientInfo': {'name': 'native-harness-api', 'title': 'Native Harness API', 'version': '0.3'}}),)

    def _login_completed(self):
        if self._completion is None or self._login_id is None:
            return ()
        if self._completion.get('loginId') != self._login_id or self._completion.get('success') is not True:
            raise ValueError('login_failed')
        self.phase = 'confirming_account'
        return (self._request('account/read', {'refreshToken': False}),)

    def receive(self, raw):
        try:
            if not self.tick():
                return ()
            self._count += 1
            self._bytes += len(raw)
            if self._count > 4096 or self._bytes > 16 * 1024 * 1024:
                raise ValueError('evidence_limit')
            value = decode_frame(raw)
            if 'method' in value:
                if 'id' in value:
                    raise ValueError('unexpected_interaction')
                method, params = value['method'], value.get('params', {})
                if method == 'account/login/completed':
                    if self.mode != 'login' or self.phase != 'logging_in' or self._completion is not None:
                        raise ValueError('login_ownership')
                    self._completion = params
                    return self._login_completed()
                if method == 'account/updated' and self.phase not in ('logging_in', 'confirming_account'):
                    raise ValueError('account_changed')
                return ()
            number = value.get('id')
            if type(number) is not int or number not in self._pending or 'error' in value:
                raise ValueError('response_invalid')
            method = self._pending.pop(number)
            result = value['result']
            if method == 'initialize':
                if not isinstance(result, dict) or not all(isinstance(result.get(k), str) for k in ('userAgent', 'codexHome', 'platformFamily', 'platformOs')):
                    raise ValueError('initialize_invalid')
                self.phase = 'reading_account'
                return (self._frame({'method': 'initialized'}), self._request('account/read', {'refreshToken': False}))
            if method == 'account/read':
                account = result['account']
                if self.mode == 'login' and self.phase == 'reading_account':
                    if account is not None:
                        raise ValueError('new_profile_not_empty')
                    self.phase = 'logging_in'
                    return (self._request('account/login/start', {'type': 'chatgptDeviceCode'}),)
                digest = account_identity_digest(account)
                if self.mode == 'models' and digest != self.expected_account_digest:
                    raise ValueError('account_mismatch')
                self.account = {key: account.get(key) for key in ('type', 'email', 'workspaceName')}
                if self.mode == 'login':
                    self.terminal_observed = True
                    self.outcome = 'succeeded'
                    self.phase = 'terminal'
                else:
                    self.phase = 'listing_models'
                    return (self._request('model/list', {'cursor': None, 'limit': 100, 'includeHidden': False}),)
            elif method == 'account/login/start':
                if result['type'] != 'chatgptDeviceCode' or not all(isinstance(result.get(k), str) and result[k] for k in ('loginId', 'verificationUrl', 'userCode')):
                    raise ValueError('login_invalid')
                self._login_id = result['loginId']
                if self.on_device is not None:
                    self.on_device(result['verificationUrl'], result['userCode'])
                return self._login_completed()
            elif method == 'model/list':
                for model in result['data']:
                    if not isinstance(model.get('model'), str) or not model['model']:
                        raise ValueError('model_invalid')
                    self.models.append({key: model.get(key) for key in ('id', 'model', 'displayName', 'description', 'isDefault')})
                cursor = result['nextCursor']
                if cursor is not None:
                    if not isinstance(cursor, str) or not cursor or cursor in self._cursors:
                        raise ValueError('cursor_invalid')
                    self._cursors.add(cursor)
                    return (self._request('model/list', {'cursor': cursor, 'limit': 100, 'includeHidden': False}),)
                self.terminal_observed = True
                self.outcome = 'succeeded'
                self.phase = 'terminal'
            return ()
        except (KeyError, TypeError, ValueError, AttributeError):
            return self.fail('control_protocol')

    def evidence(self):
        return {'outcome': self.outcome, 'fault': self.fault, 'terminal_observed': self.terminal_observed,
                'account': self.account, 'models': list(self.models)}


def stages(protocol, workspace, model_id, prompt):
    return [(protocol.start, lambda: protocol.terminal_observed)]
