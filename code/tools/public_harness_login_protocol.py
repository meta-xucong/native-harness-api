"""IO-free, single-attempt CN WorkBuddy managed-login reducer.

Only emits public beta16 JSON-RPC frames. Caller owns trusted TTY, framing,
process lifetime, storage and deadlines. Effects are (wire_bytes, private_events).
Private events are transient UI handoffs, NEVER diagnostic/log/summary payloads.
No vendor import, browser, filesystem, clock, network or model/session API.
"""
import json
from urllib.parse import urlsplit
from native_harness_api.public_sfe_session import account_identity_digest
from native_harness_api.beta15_reducer import ContractError
from public_harness_catalog_protocol import CatalogProtocol, _bounded, _constant, _keys, _meta, _pairs, _product

MAX_BYTES = 65536
MAX_FRAMES = 64
MAX_ELICITATIONS = 4


def _text(value):
    return type(value) is str and 0 < len(value) <= 1024 and bool(value.strip()) and not any(ord(c) < 32 for c in value)


def _nullable_text(value):
    return value is None or (type(value) is str and len(value) <= 2048 and not any(ord(c) < 32 for c in value))


def _account(value):
    if not _keys(value, ('state', 'mode', 'account'), ('_meta',)) or not _meta(value):
        return False
    if value['state'] == 'unauthenticated':
        return value['mode'] is None and value['account'] is None
    if value['state'] != 'authenticated' or value['mode'] != 'managed':
        return False
    account = value['account']
    if not _keys(account, ('id',), ('displayName', 'avatarUrl', 'authMethodId', 'organization', 'department', 'providerIdentity')) or not _text(account['id']):
        return False
    if any(not _nullable_text(account.get(k)) for k in ('displayName', 'avatarUrl', 'authMethodId', 'department')):
        return False
    if account.get('authMethodId') not in (None, 'workbuddy-desktop'):
        return False
    org = account.get('organization')
    if org is not None and not (_keys(org, ('id',), ('displayName',)) and _text(org['id']) and _nullable_text(org.get('displayName'))):
        return False
    identity = account.get('providerIdentity')
    return 'providerIdentity' not in account or (_keys(identity, ('accountType',), ('idp', 'oneidAccountId')) and type(identity['accountType']) is str and all(_text(identity[k]) for k in ('idp', 'oneidAccountId') if k in identity))


def _status(value):
    return _keys(value, ('authenticated',), ('message', '_meta')) and _meta(value) and type(value['authenticated']) is bool and _nullable_text(value.get('message'))


def _id(value):
    return type(value) is int and 0 <= value <= 2147483647


class LoginProtocol:
    """Terminal states cannot restart. ACK, account presence and user identity are distinct."""
    def __init__(self):
        self.state = 'new'
        self.expected = None
        self.cancelled = False
        self.login_sent = False
        self.account_authenticated = False
        self.pending_elicitation = None
        self._identity_event = None
        self.elicitation_ids = set()
        self.reverse_ids = set()
        self._s = dict(schema_version=1, state='new', failure_code='none', frames_received=0,
                       frames_sent=0, baseline_verified=False, login_intent_confirmed=False,
                       login_sent=False, login_ack=False, login_settled=False, cancellation_sent=False,
                       timeout_seen=False, account_read_verified=False, auth_status_verified=False,
                       identity_confirmed=False, account_updated_seen=False, elicitation_count=0,
                       native_account_verified=False, real_model_ready=False)

    @property
    def done(self):
        return self.state in ('verified', 'not_authenticated', 'cancelled', 'cancelled_committed', 'existing_account', 'identity_rejected', 'failed', 'unknown')

    @property
    def next_action(self):
        if self.pending_elicitation is not None:
            return 'confirm_url_handoff'
        return {'new': 'start', 'awaiting_intent': 'confirm_login_intent',
                'awaiting_identity': 'confirm_account_identity'}.get(self.state, 'close' if self.done else 'wait')

    def summary(self):
        return {**self._s, 'state': self.state, 'next_action': self.next_action}

    def _stop(self, code):
        self.state = 'unknown' if self.login_sent else 'failed'
        self._s['failure_code'] = code
        self._s['native_account_verified'] = False
        self.pending_elicitation = None
        self._identity_event = None
        return (), ()

    def _wire(self, frame):
        self._s['frames_sent'] += 1
        return json.dumps(frame, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'

    def _request(self, number, method):
        self.expected = number
        return self._wire({'jsonrpc': '2.0', 'id': number, 'method': method, 'params': {}})

    def start(self):
        if self.state != 'new':
            return self._stop('invalid_state')
        self.state = 'baseline'; self.expected = 1
        params = {'protocolVersion': 1, 'clientInfo': {'name': 'native-harness-local-login', 'version': '1'},
                  'capabilities': {'agent': {'elicitation': {'form': {}, 'url': {}}},
                                   'config': {'warning': {}}, 'account': {'mode': 'managed'}, 'product': {'mode': 'managed'}}}
        return (self._wire({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': params}),), ()

    def intent(self, confirmed=False):
        """Caller obtained explicit local-user approval for service/browser/new-store behavior."""
        if self.state != 'awaiting_intent' or self.login_sent or confirmed is not True:
            return self._stop('intent_refused')
        self.login_sent = self._s['login_sent'] = self._s['login_intent_confirmed'] = True
        self.state = 'login_pending'; self.expected = 5
        return (self._wire({'jsonrpc': '2.0', 'id': 5, 'method': 'auth/login', 'params': {'methodId': 'workbuddy-desktop'}}),), ()

    def cancel(self, timeout=False):
        """Signal cancellation, not completion. Caller later invokes closed() if no settlement."""
        if not self.login_sent or self.done:
            return self._stop('invalid_cancel')
        self.cancelled = True
        self._s['timeout_seen'] = self._s['timeout_seen'] or timeout is True
        wires = []
        if self.pending_elicitation is not None:
            wires.append(self._wire({'jsonrpc': '2.0', 'id': self.pending_elicitation, 'result': {'action': 'cancel'}}))
            self.pending_elicitation = None
        if self.expected == 5 and not self._s['cancellation_sent']:
            self._s['cancellation_sent'] = True
            wires.append(self._wire({'jsonrpc': '2.0', 'method': '$/cancel_request', 'params': {'requestId': 5}}))
        if self.state == 'awaiting_identity':
            self.state = 'cancelled_committed'
        return tuple(wires), ()

    def elicitation_reply(self, accepted=False):
        """URL acknowledgment only, after trusted handoff. Never submits form secrets."""
        if self.pending_elicitation is None or self.expected != 5 or self.done:
            return self._stop('invalid_elicitation_reply')
        if accepted is not True:
            return self.cancel()
        number = self.pending_elicitation; self.pending_elicitation = None
        return (self._wire({'jsonrpc': '2.0', 'id': number, 'result': {'action': 'accept'}}),), ()

    def confirm_identity(self, confirmed=False):
        """Explicit separate user decision on the private account_identity UI event."""
        if self.state != 'awaiting_identity' or self.cancelled:
            return self._stop('invalid_identity_confirmation')
        self._s['identity_confirmed'] = confirmed is True
        self._s['native_account_verified'] = confirmed is True
        self.state = 'verified' if confirmed is True else 'identity_rejected'
        return (), ()

    def closed(self):
        if not self.done:
            return self._stop('transport_closed')
        return (), ()

    def receive(self, frame):
        if self.done or self.state == 'new':
            return self._stop('unexpected_frame')
        self._s['frames_received'] += 1
        if self._s['frames_received'] > MAX_FRAMES:
            return self._stop('frame_limit')
        try:
            if type(frame) is not bytes or not 0 < len(frame) <= MAX_BYTES:
                raise ValueError()
            value = json.loads(frame.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
            if not _bounded(value) or type(value) is not dict or value.get('jsonrpc') != '2.0':
                raise ValueError()
            if 'method' in value:
                return self._incoming(value)
            if not (_keys(value, ('jsonrpc', 'id', 'result')) or _keys(value, ('jsonrpc', 'id', 'error'))):
                raise ValueError()
            if type(value['id']) is not int or self.expected is None or value['id'] != self.expected:
                raise ValueError()
            number = self.expected; self.expected = None
            if 'error' in value:
                error = value['error']
                if not _keys(error, ('code', 'message'), ('data',)) or type(error['code']) is not int or type(error['message']) is not str:
                    raise ValueError()
                if number != 5:
                    return self._stop('rpc_error')
                # A settled login cannot leave a live UI handoff. Deny any
                # outstanding peer request before explicit account reconciliation.
                wires = []
                if self.pending_elicitation is not None:
                    wires.append(self._wire({'jsonrpc': '2.0', 'id': self.pending_elicitation,
                                             'result': {'action': 'cancel'}}))
                    self.pending_elicitation = None
                # Keep known URL IDs only to recognize a late complete notice;
                # they cannot create a handoff or authorize another login.
                self._s['login_settled'] = True
                self.state = 'reconciling'
                return (*wires, self._request(6, 'account/read')), ()
            return self._result(number, value['result'])
        except (ContractError, ValueError, UnicodeError, RecursionError, OverflowError, TypeError, KeyError):
            return self._stop('malformed_or_unpaired')

    def _result(self, number, result):
        if number == 1:
            if not CatalogProtocol._initialize(result):
                return self._stop('initialize_rejected')
            return (self._wire({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}}), self._request(2, 'product/read')), ()
        if number == 2:
            if not _product(result) or type(result['generation']) is not int or result['generation'] < 1:
                return self._stop('product_rejected')
            return (self._request(3, 'account/read'),), ()
        if number in (3, 6):
            if not _account(result):
                return self._stop('account_rejected')
            self.account_authenticated = result['state'] == 'authenticated'
            events = ()
            if number == 6:
                self._s['account_read_verified'] = True
                if self.account_authenticated:
                    # Fresh explicit read only; server notification or ACK is not proof.
                    account = result['account']
                    self._identity_event = {'kind': 'account_identity', 'digest': account_identity_digest(account), 'account': {k: account[k] for k in ('id', 'displayName', 'organization') if k in account}}
            return (self._request(4 if number == 3 else 7, 'auth/status'),), events
        if number in (4, 7):
            if not _status(result) or result['authenticated'] != self.account_authenticated:
                return self._stop('status_mismatch')
            if number == 4:
                self._s['baseline_verified'] = not self.account_authenticated
                self.state = 'existing_account' if self.account_authenticated else 'awaiting_intent'
            else:
                self._s['auth_status_verified'] = True
                if self.cancelled:
                    self.state = 'cancelled_committed' if self.account_authenticated else 'cancelled'
                else:
                    self.state = 'awaiting_identity' if self.account_authenticated else 'not_authenticated'
            events = (self._identity_event,) if self.state == 'awaiting_identity' and self._identity_event is not None else ()
            self._identity_event = None
            return (), events
        if number == 5:
            if not _keys(result, (), ('_meta',)) or not _meta(result):
                return self._stop('login_result_rejected')
            if self.pending_elicitation is not None:
                return self._stop('unsettled_elicitation')
            self._s['login_ack'] = self._s['login_settled'] = True
            self.state = 'reconciling'
            return (self._request(6, 'account/read'),), ()
        return self._stop('unexpected_response')

    def _incoming(self, value):
        if 'id' not in value:
            if not _keys(value, ('jsonrpc', 'method', 'params')):
                return self._stop('unexpected_notification')
            params = value['params']
            if value['method'] == 'product/updated':
                if not _product(params) or type(params['generation']) is not int or params['generation'] < 1:
                    return self._stop('product_changed')
                return (), ()
            if value['method'] == 'account/updated' and _account(params):
                self._s['account_updated_seen'] = True
                if not self.login_sent and params['state'] == 'authenticated':
                    self.state = 'existing_account'
                # Updates never grant readiness or replace explicit read/status.
                if self.state == 'awaiting_identity' or self.expected == 7:
                    return self._stop('identity_changed')
                return (), ()
            if value['method'] == 'elicitation/complete' and _keys(params, ('elicitationId',), ('_meta',)) and _meta(params) and params['elicitationId'] in self.elicitation_ids:
                self.elicitation_ids.remove(params['elicitationId'])
                return (), ()
            return self._stop('unexpected_notification')
        if not _keys(value, ('jsonrpc', 'id', 'method', 'params')) or not _id(value['id']) or value['id'] in self.reverse_ids:
            return self._stop('unexpected_request')
        self.reverse_ids.add(value['id'])
        if value['method'] != 'elicitation/create' or self.expected != 5 or self.pending_elicitation is not None:
            return self._stop('unexpected_request')
        p = value['params']
        if not (_keys(p, ('mode', 'message', 'requestId', 'elicitationId', 'url'), ('_meta',)) if type(p) is dict and p.get('mode') == 'url' else _keys(p, ('mode', 'message', 'requestId', 'requestedSchema'), ('_meta',))):
            return self._stop('elicitation_rejected')
        if not _meta(p) or type(p['requestId']) is not int or p['requestId'] != 5 or type(p['message']) is not str or not _nullable_text(p['message']):
            return self._stop('elicitation_rejected')
        self._s['elicitation_count'] += 1
        if self._s['elicitation_count'] > MAX_ELICITATIONS:
            return self._stop('elicitation_limit')
        self.pending_elicitation = value['id']
        if p['mode'] == 'form':
            # No form fields are inspected, rendered, accepted or retained.
            return self.cancel()
        if p['mode'] != 'url' or not _text(p['elicitationId']) or p['elicitationId'] in self.elicitation_ids or type(p['url']) is not str:
            return self._stop('elicitation_rejected')
        url = urlsplit(p['url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or any(ord(c) < 33 for c in p['url']):
            return self._stop('url_rejected')
        self.elicitation_ids.add(p['elicitationId'])
        if self.cancelled:
            return self.cancel()
        # No automatic open and no URL allowlist claim: trusted local UI must verify destination.
        return (), ({'kind': 'url_handoff', 'url': p['url']},)
