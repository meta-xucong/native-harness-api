"""Managed beta16 startup over the separately packaged Node application."""
import time

from .beta15_reducer import check
from .public_sfe_session import PublicSfeSession, account, account_identity_digest, keys, product
from .public_sfe_owned_session import PublicSfeOwnedSession


class WorkBuddyNodeSession(PublicSfeSession):
    RETAIN_LATE_SESSION_FOR_CLOSE = True
    _SETUP_REQUESTS = ('initialize', 'product/read', 'account/read', 'auth/status',
                       'session/new', 'session/set_config_option')

    def __init__(self, *, expected_account_digest, capture_account=False, product_version='5.9.3', **kwargs):
        super().__init__(expected_account_digest='0' * 64 if capture_account else expected_account_digest, **kwargs)
        self._capture_account = capture_account
        self.identity = None
        self.restored_digest = None
        self._product_revision = None
        self._restoring = False
        self._last_setup_step = 'launch'
        self._product_version = product_version
        self._session_new_intent_issued = False

    def _request(self, method, params, inner=None):
        if method in self._SETUP_REQUESTS:
            self._last_setup_step = method
        if method == 'session/new':
            self._session_new_intent_issued = True
        return super()._request(method, params, inner)

    def _after_initialize(self):
        # A managed account can read as authenticated before the asynchronous
        # restore commits. Wait for beta16's Account then Product publications.
        self._restoring = True
        self._last_setup_step = 'account/updated'
        return ()

    def _incoming(self, value):
        if self._restoring and value['method'] in ('account/updated', 'product/updated'):
            check(keys(value, ('jsonrpc', 'method', 'params')) and self.tick())
            payload = value['params']
            if value['method'] == 'account/updated':
                check(self.restored_digest is None and account(payload))
                digest = account_identity_digest(payload['account'])
                check(self._capture_account or digest == self.expected_account_digest)
                self.restored_digest = digest
                self._last_setup_step = 'product/updated'
                if self._capture_account:
                    who = payload['account']
                    self.identity = (who['id'], who.get('displayName'), (who.get('organization') or {}).get('id'))
                return ()
            check(self.restored_digest is not None and product(payload, version=self._product_version)
                  and payload['generation'] >= 2)
            self._restoring = False
            self._product_revision = payload['generation'], payload['revision']
            return (self._request('product/read', {}),)
        return super()._incoming(value)

    def _check_product(self, value):
        check(product(value, version=self._product_version)
              and (value['generation'], value['revision']) == self._product_revision)

    def _check_account(self, value):
        check(account(value) and account_identity_digest(value['account']) == self.restored_digest)

    @property
    def awaiting_session_new(self):
        return any(method == 'session/new' for method, _ in self._pending.values())

    def evidence(self):
        return {**super().evidence(), 'last_setup_step': self._last_setup_step,
                'session_new_intent_issued': self._session_new_intent_issued}


class WorkBuddyNodeOwnedSession(PublicSfeOwnedSession):
    def __init__(self, transport, *, product_version='5.9.3'):
        super().__init__(transport)
        self.product_version = product_version

    def _make_protocol(self, **kwargs):
        return WorkBuddyNodeSession(product_version=self.product_version, **kwargs)

    def _begin_close(self, deadline):
        if (self.protocol.awaiting_session_new and not self.protocol.tick()
                and self.protocol.failure_code == 'timeout'):
            # Leave the second half of the remaining grace for session/close and EOF.
            wait_until = (time.monotonic() + deadline) / 2
            self._pump_until(lambda: not self.protocol.awaiting_session_new, wait_until, honor_turn=False)
        super()._begin_close(deadline)


class WorkBuddyRecoverySession(WorkBuddyNodeOwnedSession):
    REPORT_INTERRUPTION = True

    def __init__(self, transport, *, capture_account, product_version='5.9.3'):
        super().__init__(transport, product_version=product_version)
        self.capture_account = capture_account

    def _make_protocol(self, **kwargs):
        return super()._make_protocol(capture_account=self.capture_account, **kwargs)

    def _stages(self, workspace, model_id, prompt):
        return [(self.protocol.start, lambda: self.protocol.ready)]
